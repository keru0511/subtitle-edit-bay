from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from .subtitle_review_rules import RULE_VERSION, ReviewFinding, review_segment_rules


REVIEW_STATUSES = {"open", "resolved", "ignored", "false_positive", "stale"}


@dataclass(frozen=True)
class SubtitleReviewIssue:
    issue_id: str
    segment_ids: tuple[str, ...]
    rule_id: str
    rule_version: str
    severity: str
    reasons: tuple[str, ...]
    evidence: Mapping[str, Any]
    project_revision: int
    content_fingerprint: str
    status: str = "open"
    created_at: str = ""
    reviewed_at: str = ""
    logical_key: str = ""
    supersedes: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "segment_ids": list(self.segment_ids),
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "severity": self.severity,
            "reasons": list(self.reasons),
            "evidence": dict(self.evidence),
            "project_revision": self.project_revision,
            "content_fingerprint": self.content_fingerprint,
            "status": self.status,
            "created_at": self.created_at,
            "reviewed_at": self.reviewed_at,
            "logical_key": self.logical_key,
            "supersedes": self.supersedes,
        }


class SubtitleReviewCancelled(RuntimeError):
    pass


def generate_review_queue(
    segments: Iterable[Mapping[str, Any]],
    *,
    project_revision: int = 0,
    rule_version: str = RULE_VERSION,
    cancel_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[float], None] | None = None,
) -> list[SubtitleReviewIssue]:
    ordered = sorted(
        [dict(item) for item in segments],
        key=lambda item: (float(item.get("start", 0.0)), str(item.get("id", ""))),
    )
    issues: list[SubtitleReviewIssue] = []
    for index, segment in enumerate(ordered):
        if cancel_check and cancel_check():
            raise SubtitleReviewCancelled("subtitle review cancelled")
        findings = review_segment_rules(segment, ordered[index - 1] if index else None)
        for finding in findings:
            segment_id = str(segment.get("id", f"segment-{index}"))
            fingerprint = _fingerprint(segment, finding.rule_id, rule_version)
            logical_key = _logical_key((segment_id,), finding.rule_id, rule_version)
            issues.append(
                SubtitleReviewIssue(
                    issue_id=hashlib.sha1(f"{logical_key}:{fingerprint}".encode()).hexdigest()[:16],
                    segment_ids=(segment_id,),
                    rule_id=finding.rule_id,
                    rule_version=rule_version,
                    severity=finding.severity,
                    reasons=(finding.reason,),
                    evidence=finding.evidence,
                    project_revision=project_revision,
                    content_fingerprint=fingerprint,
                    created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    logical_key=logical_key,
                )
            )
        if progress_callback:
            progress_callback((index + 1) / max(1, len(ordered)))
    severity_order = {"high": 0, "medium": 1, "low": 2}
    return sorted(issues, key=lambda item: (severity_order.get(item.severity, 9), item.segment_ids, item.rule_id))


class SubtitleReviewQueue:
    def __init__(self, issues: Iterable[SubtitleReviewIssue] | None = None) -> None:
        self.issues = {item.issue_id: item for item in (issues or [])}

    def update_status(self, issue_id: str, status: str) -> SubtitleReviewIssue:
        if status not in REVIEW_STATUSES - {"stale"}:
            raise ValueError(f"unsupported review status: {status}")
        issue = self.issues[issue_id]
        updated = SubtitleReviewIssue(**{**issue.__dict__, "status": status, "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        self.issues[issue_id] = updated
        return updated

    def reconcile(
        self,
        generated: Iterable[SubtitleReviewIssue],
    ) -> list[SubtitleReviewIssue]:
        """Merge a newly generated queue without losing review decisions."""
        generated_items = list(generated)
        existing_by_key = {
            _issue_logical_key(issue): issue for issue in self.issues.values()
        }
        reconciled: dict[str, SubtitleReviewIssue] = {}
        matched_keys: set[str] = set()
        for current in generated_items:
            key = _issue_logical_key(current)
            previous = existing_by_key.get(key)
            matched_keys.add(key)
            if previous is None:
                reconciled[current.issue_id] = current
                continue
            if previous.content_fingerprint == current.content_fingerprint:
                reconciled[previous.issue_id] = SubtitleReviewIssue(
                    **{
                        **current.__dict__,
                        "issue_id": previous.issue_id,
                        "status": previous.status,
                        "created_at": previous.created_at or current.created_at,
                        "reviewed_at": previous.reviewed_at,
                        "logical_key": key,
                        "supersedes": previous.supersedes,
                    }
                )
                continue
            reconciled[previous.issue_id] = SubtitleReviewIssue(
                **{**previous.__dict__, "status": "stale"}
            )
            reconciled[current.issue_id] = SubtitleReviewIssue(
                **{**current.__dict__, "logical_key": key, "supersedes": previous.issue_id}
            )
        for issue in self.issues.values():
            key = _issue_logical_key(issue)
            if key not in matched_keys and issue.issue_id not in reconciled:
                reconciled[issue.issue_id] = SubtitleReviewIssue(
                    **{**issue.__dict__, "status": "stale"}
                )
        self.issues = reconciled
        return list(self.issues.values())

    def mark_stale(self, segments: Iterable[Mapping[str, Any]], rule_version: str = RULE_VERSION) -> list[SubtitleReviewIssue]:
        by_id = {str(item.get("id")): item for item in segments}
        updated: list[SubtitleReviewIssue] = []
        for issue_id, issue in list(self.issues.items()):
            segment = by_id.get(issue.segment_ids[0]) if issue.segment_ids else None
            if segment is None or _fingerprint(segment, issue.rule_id, rule_version) != issue.content_fingerprint:
                changed = SubtitleReviewIssue(**{**issue.__dict__, "status": "stale"})
                self.issues[issue_id] = changed
                updated.append(changed)
        return updated

    def filtered(self, *, status: str | None = None, severity: str | None = None, rule_id: str | None = None) -> list[SubtitleReviewIssue]:
        return [
            item for item in self.issues.values()
            if (status is None or item.status == status)
            and (severity is None or item.severity == severity)
            and (rule_id is None or item.rule_id == rule_id)
        ]

    def to_json(self) -> list[dict[str, Any]]:
        return [item.to_json() for item in self.issues.values()]


def _fingerprint(segment: Mapping[str, Any], rule_id: str, rule_version: str) -> str:
    payload = {
        "id": segment.get("id"),
        "start": segment.get("start"),
        "end": segment.get("end"),
        "text": segment.get("text"),
        "speaker": segment.get("speaker"),
        "rule_id": rule_id,
        "rule_version": rule_version,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _logical_key(segment_ids: Iterable[str], rule_id: str, rule_version: str) -> str:
    payload = {
        "segment_ids": list(segment_ids),
        "rule_id": rule_id,
        "rule_version": rule_version,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:24]


def _issue_logical_key(issue: SubtitleReviewIssue) -> str:
    return issue.logical_key or _logical_key(issue.segment_ids, issue.rule_id, issue.rule_version)

