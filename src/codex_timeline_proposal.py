from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Iterable, Mapping

from .short_video_commands import (
    AddShortVideoRangeClip,
    MoveShortVideoClip,
    RemoveShortVideoClip,
    SetShortVideoDurationTarget,
    ShortVideoCommand,
    UpdateShortVideoClip,
    UseShortVideoHighlightCandidate,
    apply_short_video_commands,
    short_video_from_project,
)
from .short_video_schema import ShortVideoError
from .video_timeline import MIN_CUT_DURATION_SECONDS, VideoTimelineError, timeline_from_project


TIMELINE_PROPOSAL_SCHEMA_VERSION = 1
TIMELINE_TARGETS = {"normal", "short"}
NORMAL_OPERATION_TYPES = {
    "add_cut",
    "remove_range",
    "restore_cut",
    "restore_range",
    "update_cut_range",
    "clear_cuts",
}
SHORT_OPERATION_TYPES = {
    "add_clip_by_range",
    "remove_clip",
    "move_clip",
    "update_clip_range",
    "use_highlight_candidate",
    "set_short_duration_target",
}
ROOT_FIELDS = {
    "schema_version",
    "summary",
    "target",
    "operations",
    "warnings",
    "base_revision",
    "base_state_revision",
}
OPERATION_FIELDS = {
    "id",
    "type",
    "reason",
    "cut_id",
    "clip_id",
    "before_clip_id",
    "source_start",
    "source_end",
    "highlight_candidate_id",
    "target_seconds",
}
OPERATION_FIELDS_BY_TYPE = {
    "add_cut": {"source_start", "source_end"},
    "remove_range": {"source_start", "source_end"},
    "restore_cut": {"cut_id"},
    "restore_range": {"source_start", "source_end"},
    "update_cut_range": {"cut_id", "source_start", "source_end"},
    "clear_cuts": set(),
    "add_clip_by_range": {"clip_id", "source_start", "source_end"},
    "remove_clip": {"clip_id"},
    "move_clip": {"clip_id", "before_clip_id"},
    "update_clip_range": {"clip_id", "source_start", "source_end"},
    "use_highlight_candidate": {"clip_id", "highlight_candidate_id"},
    "set_short_duration_target": {"target_seconds"},
}
SAFE_SEGMENT_FIELDS = {"id", "start", "end", "text", "speaker"}
SAFE_SELECTION_FIELDS = {"basis", "sourcePositionMs", "outputPositionMs", "segment_id"}


class TimelineProposalError(ValueError):
    """Raised when a Codex timeline proposal is invalid or stale."""


class TimelineProposalRevisionConflict(TimelineProposalError):
    """Raised when project or target state changed after proposal generation."""


class TimelineProposalConfirmationRequired(TimelineProposalError):
    """Raised when a large structural change needs a second confirmation."""


def _require_object(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TimelineProposalError(f"{field} must be an object")
    return value


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise TimelineProposalError(f"{field} contains unsupported fields: {', '.join(unknown)}")


def _finite_seconds(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise TimelineProposalError(f"{field} must be a number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise TimelineProposalError(f"{field} must be a number") from error
    if not math.isfinite(result):
        raise TimelineProposalError(f"{field} must be finite")
    return round(result, 3)


def _required_id(payload: Mapping[str, Any], field: str, operation_index: int) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise TimelineProposalError(f"operations[{operation_index}].{field} is required")
    return value.strip()


@dataclass(frozen=True)
class TimelineProposalOperation:
    id: str
    type: str
    reason: str = ""
    cut_id: str = ""
    clip_id: str = ""
    before_clip_id: str = ""
    source_start: float | None = None
    source_end: float | None = None
    highlight_candidate_id: str = ""
    target_seconds: float | None = None

    @classmethod
    def from_json(
        cls,
        payload: Mapping[str, Any],
        index: int,
        *,
        target: str,
    ) -> "TimelineProposalOperation":
        _require_object(payload, f"operations[{index}]")
        _reject_unknown(payload, OPERATION_FIELDS, f"operations[{index}]")
        operation_type = payload.get("type")
        allowed = NORMAL_OPERATION_TYPES if target == "normal" else SHORT_OPERATION_TYPES
        if not isinstance(operation_type, str) or operation_type not in allowed:
            raise TimelineProposalError(f"unsupported {target} operation type: {operation_type!r}")
        _reject_unknown(
            payload,
            {"id", "type", "reason"} | OPERATION_FIELDS_BY_TYPE[operation_type],
            f"operations[{index}] {operation_type}",
        )
        operation_id = _required_id(payload, "id", index)
        reason = payload.get("reason", "")
        if not isinstance(reason, str):
            raise TimelineProposalError(f"operations[{index}].reason must be a string")
        cut_id = ""
        clip_id = ""
        before_clip_id = ""
        start = None
        end = None
        candidate_id = ""
        target_seconds = None
        if operation_type in {"restore_cut", "update_cut_range"}:
            cut_id = _required_id(payload, "cut_id", index)
        if operation_type in {"add_clip_by_range", "remove_clip", "move_clip", "update_clip_range"}:
            clip_id = _required_id(payload, "clip_id", index)
        if "before_clip_id" in payload:
            raw_before = payload["before_clip_id"]
            if not isinstance(raw_before, str):
                raise TimelineProposalError(f"operations[{index}].before_clip_id must be a string")
            before_clip_id = raw_before.strip()
        if operation_type in {
            "add_cut",
            "remove_range",
            "restore_range",
            "update_cut_range",
            "add_clip_by_range",
            "update_clip_range",
        }:
            start = _finite_seconds(payload.get("source_start"), f"operations[{index}].source_start")
            end = _finite_seconds(payload.get("source_end"), f"operations[{index}].source_end")
        if operation_type == "use_highlight_candidate":
            candidate_id = _required_id(payload, "highlight_candidate_id", index)
            clip_id = _required_id(payload, "clip_id", index)
        if operation_type == "set_short_duration_target":
            target_seconds = _finite_seconds(payload.get("target_seconds"), f"operations[{index}].target_seconds")
            if target_seconds <= 0.0:
                raise TimelineProposalError(f"operations[{index}].target_seconds must be positive")
        return cls(
            id=operation_id,
            type=operation_type,
            reason=reason,
            cut_id=cut_id,
            clip_id=clip_id,
            before_clip_id=before_clip_id,
            source_start=start,
            source_end=end,
            highlight_candidate_id=candidate_id,
            target_seconds=target_seconds,
        )

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"id": self.id, "type": self.type, "reason": self.reason}
        for key in ("cut_id", "clip_id", "before_clip_id", "highlight_candidate_id"):
            if value := getattr(self, key):
                payload[key] = value
        if self.source_start is not None:
            payload["source_start"] = self.source_start
        if self.source_end is not None:
            payload["source_end"] = self.source_end
        if self.target_seconds is not None:
            payload["target_seconds"] = self.target_seconds
        return payload


@dataclass(frozen=True)
class TimelineProposal:
    summary: str
    target: str
    operations: tuple[TimelineProposalOperation, ...]
    base_revision: int
    base_state_revision: str
    warnings: tuple[str, ...] = ()
    schema_version: int = TIMELINE_PROPOSAL_SCHEMA_VERSION

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "TimelineProposal":
        _require_object(payload, "proposal")
        _reject_unknown(payload, ROOT_FIELDS, "proposal")
        if payload.get("schema_version") != TIMELINE_PROPOSAL_SCHEMA_VERSION:
            raise TimelineProposalError(f"schema_version must be {TIMELINE_PROPOSAL_SCHEMA_VERSION}")
        target = payload.get("target")
        if target not in TIMELINE_TARGETS:
            raise TimelineProposalError("proposal.target must be normal or short")
        base_revision = payload.get("base_revision")
        if type(base_revision) is not int or base_revision < 0:
            raise TimelineProposalError("proposal.base_revision must be a non-negative integer")
        state_revision = payload.get("base_state_revision")
        if not isinstance(state_revision, str) or not state_revision.strip():
            raise TimelineProposalError("proposal.base_state_revision must be a non-empty string")
        raw_operations = payload.get("operations")
        if not isinstance(raw_operations, list) or not raw_operations:
            raise TimelineProposalError("proposal.operations must be a non-empty array")
        operations = tuple(
            TimelineProposalOperation.from_json(item, index, target=target) for index, item in enumerate(raw_operations)
        )
        operation_ids = [operation.id for operation in operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise TimelineProposalError("proposal operation ids must be unique")
        warnings = payload.get("warnings", [])
        if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
            raise TimelineProposalError("proposal.warnings must be an array of strings")
        summary = payload.get("summary")
        if not isinstance(summary, str):
            raise TimelineProposalError("proposal.summary must be a string")
        return cls(
            summary=summary,
            target=target,
            operations=operations,
            base_revision=base_revision,
            base_state_revision=state_revision,
            warnings=tuple(warnings),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "summary": self.summary,
            "target": self.target,
            "operations": [operation.to_json() for operation in self.operations],
            "warnings": list(self.warnings),
            "base_revision": self.base_revision,
            "base_state_revision": self.base_state_revision,
        }


@dataclass(frozen=True)
class TimelineProposalApplyResult:
    project: dict[str, Any]
    target: str
    applied_operation_ids: tuple[str, ...]
    changed_ids: tuple[str, ...]


def timeline_state_revision(project: Mapping[str, Any], target: str) -> str:
    if target == "normal":
        state: Mapping[str, Any] = timeline_from_project(dict(project)).to_json()
    elif target == "short":
        state = short_video_from_project(project).to_json()
    else:
        raise TimelineProposalError(f"unknown timeline target: {target}")
    canonical = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def build_timeline_proposal_context(
    project: Mapping[str, Any],
    *,
    target: str,
    project_revision: int,
    highlight_candidates: Iterable[Mapping[str, Any]] = (),
    selection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    duration = max(0.0, float(project.get("video", {}).get("duration_seconds", 0.0)))
    segments = [
        {key: deepcopy(value) for key, value in segment.items() if key in SAFE_SEGMENT_FIELDS}
        for segment in project.get("segments", [])
        if isinstance(segment, Mapping)
    ]
    safe_highlights = [
        {
            key: deepcopy(value)
            for key, value in candidate.items()
            if key in {"id", "start", "end", "score", "reason", "source_segment_ids"}
        }
        for candidate in highlight_candidates
        if isinstance(candidate, Mapping)
    ]
    if target == "normal":
        target_state: Mapping[str, Any] = timeline_from_project(dict(project)).as_view()
    elif target == "short":
        short_video = short_video_from_project(project)
        target_state = {
            "time_basis": "source",
            "clips": [clip.to_json() for clip in short_video.clips],
            "duration_target_seconds": short_video.duration_target_seconds,
        }
    else:
        raise TimelineProposalError(f"unknown timeline target: {target}")
    return {
        "target": target,
        "time_basis": "source",
        "project_revision": project_revision,
        "state_revision": timeline_state_revision(project, target),
        "video_duration": duration,
        "target_state": target_state,
        "segments": segments,
        "highlight_candidates": safe_highlights,
        "selection": {
            key: deepcopy(value) for key, value in dict(selection or {}).items() if key in SAFE_SELECTION_FIELDS
        },
    }


def _selected_operations(
    proposal: TimelineProposal,
    selected_operation_ids: Iterable[str] | None,
) -> tuple[TimelineProposalOperation, ...]:
    if selected_operation_ids is None:
        return proposal.operations
    if isinstance(selected_operation_ids, (str, bytes)):
        raise TimelineProposalError("selected operation ids must be non-empty strings")
    try:
        requested = tuple(selected_operation_ids)
    except TypeError as error:
        raise TimelineProposalError("selected operation ids must be an iterable of strings") from error
    if not all(isinstance(operation_id, str) and operation_id for operation_id in requested):
        raise TimelineProposalError("selected operation ids must be non-empty strings")
    selected = set(requested)
    known = {operation.id for operation in proposal.operations}
    unknown = selected - known
    if unknown:
        raise TimelineProposalError("unknown selected operation ids: " + ", ".join(sorted(unknown)))
    operations = tuple(operation for operation in proposal.operations if operation.id in selected)
    if not operations:
        raise TimelineProposalError("no proposal operations were selected")
    return operations


def _requires_confirmation(
    project: Mapping[str, Any],
    proposal: TimelineProposal,
    operations: tuple[TimelineProposalOperation, ...],
) -> bool:
    if proposal.target == "normal":
        timeline = timeline_from_project(dict(project))
        if any(operation.type == "clear_cuts" for operation in operations) and timeline.cuts:
            return True
        added = sum(
            max(0.0, float(operation.source_end or 0.0) - float(operation.source_start or 0.0))
            for operation in operations
            if operation.type in {"add_cut", "remove_range"}
        )
        return timeline.source_duration > 0.0 and added >= timeline.source_duration * 0.5
    short_video = short_video_from_project(project)
    clip_ids = {clip.proposal_id for clip in short_video.clips}
    removed = {operation.clip_id for operation in operations if operation.type == "remove_clip"}
    return bool(clip_ids) and clip_ids.issubset(removed)


def _short_command(operation: TimelineProposalOperation) -> ShortVideoCommand:
    """Translate a schema-validated proposal operation into a domain command."""

    if operation.type == "add_clip_by_range":
        return AddShortVideoRangeClip(
            clip_id=operation.clip_id,
            source_start=operation.source_start,
            source_end=operation.source_end,
        )
    if operation.type == "remove_clip":
        return RemoveShortVideoClip(clip_id=operation.clip_id)
    if operation.type == "move_clip":
        return MoveShortVideoClip(
            clip_id=operation.clip_id,
            before_clip_id=operation.before_clip_id,
        )
    if operation.type == "update_clip_range":
        return UpdateShortVideoClip(
            clip_id=operation.clip_id,
            changes={"start": operation.source_start, "end": operation.source_end},
        )
    if operation.type == "use_highlight_candidate":
        return UseShortVideoHighlightCandidate(
            clip_id=operation.clip_id,
            highlight_candidate_id=operation.highlight_candidate_id,
        )
    if operation.type == "set_short_duration_target":
        return SetShortVideoDurationTarget(target_seconds=operation.target_seconds)
    raise TimelineProposalError(f"unsupported short operation type: {operation.type!r}")


def apply_timeline_proposal(
    project: Mapping[str, Any],
    proposal: TimelineProposal | Mapping[str, Any],
    *,
    current_revision: int,
    selected_operation_ids: Iterable[str] | None = None,
    highlight_candidates: Iterable[Mapping[str, Any]] = (),
    confirmed_large_change: bool = False,
) -> TimelineProposalApplyResult:
    parsed = proposal if isinstance(proposal, TimelineProposal) else TimelineProposal.from_json(proposal)
    if parsed.base_revision != current_revision:
        raise TimelineProposalRevisionConflict("proposal project revision is stale")
    if parsed.base_state_revision != timeline_state_revision(project, parsed.target):
        raise TimelineProposalRevisionConflict("proposal timeline state revision is stale")
    operations = _selected_operations(parsed, selected_operation_ids)
    if _requires_confirmation(project, parsed, operations) and not confirmed_large_change:
        raise TimelineProposalConfirmationRequired("large timeline change requires confirmation")
    candidate = deepcopy(dict(project))
    changed_ids: set[str] = set()
    try:
        if parsed.target == "normal":
            timeline = timeline_from_project(candidate)
            for operation in operations:
                if operation.type in {"add_cut", "remove_range"}:
                    timeline = timeline.add_cut(
                        operation.source_start,
                        operation.source_end,
                        cut_id=operation.cut_id or operation.id,
                    )
                elif operation.type == "restore_cut":
                    timeline = timeline.restore_cut(operation.cut_id)
                elif operation.type == "restore_range":
                    split_index = 0

                    def next_split_id() -> str:
                        nonlocal split_index
                        split_index += 1
                        return f"{operation.id}-split-{split_index}"

                    timeline = timeline.restore_range(
                        operation.source_start,
                        operation.source_end,
                        id_factory=next_split_id,
                    )
                elif operation.type == "update_cut_range":
                    timeline = timeline.update_cut(operation.cut_id, operation.source_start, operation.source_end)
                elif operation.type == "clear_cuts":
                    timeline = timeline.clear_cuts()
                changed_ids.add(operation.cut_id or operation.id)
            candidate["timeline"] = timeline.to_json()
        else:
            command_result = apply_short_video_commands(
                candidate,
                (_short_command(operation) for operation in operations),
                highlight_candidates=highlight_candidates,
            )
            candidate["short_video"] = command_result.short_video.to_json()
            changed_ids.update(command_result.changed_clip_ids)
    except (ShortVideoError, VideoTimelineError) as error:
        raise TimelineProposalError(str(error)) from error
    return TimelineProposalApplyResult(
        project=candidate,
        target=parsed.target,
        applied_operation_ids=tuple(operation.id for operation in operations),
        changed_ids=tuple(sorted(changed_ids)),
    )


_OPERATION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "type"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "type": {"type": "string", "enum": sorted(NORMAL_OPERATION_TYPES | SHORT_OPERATION_TYPES)},
        "reason": {"type": "string"},
        "cut_id": {"type": "string", "minLength": 1},
        "clip_id": {"type": "string", "minLength": 1},
        "before_clip_id": {"type": "string"},
        "source_start": {"type": "number", "minimum": 0},
        "source_end": {"type": "number", "exclusiveMinimum": 0},
        "highlight_candidate_id": {"type": "string", "minLength": 1},
        "target_seconds": {"type": "number", "exclusiveMinimum": 0},
    },
}


TIMELINE_PROPOSAL_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "summary",
        "target",
        "operations",
        "warnings",
        "base_revision",
        "base_state_revision",
    ],
    "properties": {
        "schema_version": {"type": "integer", "const": TIMELINE_PROPOSAL_SCHEMA_VERSION},
        "summary": {"type": "string"},
        "target": {"type": "string", "enum": sorted(TIMELINE_TARGETS)},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "base_revision": {"type": "integer", "minimum": 0},
        "base_state_revision": {"type": "string", "minLength": 1},
        "operations": {
            "type": "array",
            "minItems": 1,
            "items": _OPERATION_OUTPUT_SCHEMA,
        },
    },
}
