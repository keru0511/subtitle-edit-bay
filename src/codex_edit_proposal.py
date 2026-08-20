from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .subtitle_project import SubtitleProjectError, normalize_segment, validate_project


class EditProposalError(ValueError):
    """Raised when a Codex edit proposal is invalid or cannot be applied."""


class EditProposalRevisionConflict(EditProposalError):
    """Raised when a proposal was created for an older project revision."""


OPERATION_TYPES = {
    "update_segment",
    "add_segment",
    "delete_segment",
    "split_segment",
    "merge_segments",
}
UPDATE_FIELDS = {
    "text",
    "start",
    "end",
    "speaker",
    "emphasis",
    "position",
    "subtitle_font_scale",
    "subtitle_font_family",
    "subtitle_line_count",
    "max_width",
}
SEGMENT_FIELDS = {
    "id",
    "start",
    "end",
    "text",
    "speaker",
    "emphasis",
    "position",
    "subtitle_font_scale",
    "subtitle_font_family",
    "subtitle_line_count",
    "max_width",
    "words",
}
ROOT_FIELDS = {"summary", "operations", "warnings", "base_revision"}
OPERATION_FIELDS = {
    "id",
    "type",
    "segment_id",
    "segment_ids",
    "changes",
    "segment",
    "split_at",
    "new_segment_id",
    "first_text",
    "second_text",
    "reason",
}


@dataclass(frozen=True)
class EditOperation:
    operation_id: str
    type: str
    segment_id: str = ""
    segment_ids: tuple[str, ...] = ()
    changes: Mapping[str, Any] | None = None
    segment: Mapping[str, Any] | None = None
    split_at: float | None = None
    new_segment_id: str = ""
    first_text: str | None = None
    second_text: str | None = None
    reason: str = ""

    @classmethod
    def from_json(cls, payload: Mapping[str, Any], index: int) -> "EditOperation":
        _require_object(payload, f"operations[{index}]")
        _reject_unknown(payload, OPERATION_FIELDS, f"operations[{index}]")
        operation_type = str(payload.get("type", ""))
        if operation_type not in OPERATION_TYPES:
            raise EditProposalError(f"unsupported operation type: {operation_type!r}")
        operation_id = str(payload.get("id") or f"operation-{index + 1:04d}")
        segment_id = str(payload.get("segment_id", ""))
        raw_segment_ids = payload.get("segment_ids", [])
        if raw_segment_ids is None:
            raw_segment_ids = []
        if not isinstance(raw_segment_ids, list) or not all(isinstance(item, str) for item in raw_segment_ids):
            raise EditProposalError(f"operations[{index}].segment_ids must be an array of strings")
        changes = payload.get("changes")
        if changes is not None:
            _require_object(changes, f"operations[{index}].changes")
            _reject_unknown(changes, UPDATE_FIELDS, f"operations[{index}].changes")
        segment = payload.get("segment")
        if segment is not None:
            _require_object(segment, f"operations[{index}].segment")
            _reject_unknown(segment, SEGMENT_FIELDS, f"operations[{index}].segment")
        split_at = payload.get("split_at")
        if split_at is not None:
            try:
                split_at = float(split_at)
            except (TypeError, ValueError) as error:
                raise EditProposalError(f"operations[{index}].split_at must be a number") from error
        return cls(
            operation_id=operation_id,
            type=operation_type,
            segment_id=segment_id,
            segment_ids=tuple(raw_segment_ids),
            changes=deepcopy(changes) if changes is not None else None,
            segment=deepcopy(segment) if segment is not None else None,
            split_at=split_at,
            new_segment_id=str(payload.get("new_segment_id", "")),
            first_text=str(payload["first_text"]) if "first_text" in payload else None,
            second_text=str(payload["second_text"]) if "second_text" in payload else None,
            reason=str(payload.get("reason", "")),
        )

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.operation_id,
            "type": self.type,
            "reason": self.reason,
        }
        if self.segment_id:
            payload["segment_id"] = self.segment_id
        if self.segment_ids:
            payload["segment_ids"] = list(self.segment_ids)
        if self.changes is not None:
            payload["changes"] = deepcopy(dict(self.changes))
        if self.segment is not None:
            payload["segment"] = deepcopy(dict(self.segment))
        if self.split_at is not None:
            payload["split_at"] = self.split_at
        if self.new_segment_id:
            payload["new_segment_id"] = self.new_segment_id
        if self.first_text is not None:
            payload["first_text"] = self.first_text
        if self.second_text is not None:
            payload["second_text"] = self.second_text
        return payload


@dataclass(frozen=True)
class CodexEditProposal:
    summary: str
    operations: tuple[EditOperation, ...]
    warnings: tuple[str, ...] = ()
    base_revision: int | None = None

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "CodexEditProposal":
        _require_object(payload, "proposal")
        _reject_unknown(payload, ROOT_FIELDS, "proposal")
        raw_operations = payload.get("operations")
        if not isinstance(raw_operations, list) or not raw_operations:
            raise EditProposalError("proposal.operations must be a non-empty array")
        operations = tuple(
            EditOperation.from_json(item, index)
            for index, item in enumerate(raw_operations)
        )
        operation_ids = [item.operation_id for item in operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise EditProposalError("proposal operation ids must be unique")
        warnings = payload.get("warnings", [])
        if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
            raise EditProposalError("proposal.warnings must be an array of strings")
        base_revision = payload.get("base_revision")
        if base_revision is not None:
            try:
                base_revision = int(base_revision)
            except (TypeError, ValueError) as error:
                raise EditProposalError("proposal.base_revision must be an integer") from error
        return cls(
            summary=str(payload.get("summary", "")),
            operations=operations,
            warnings=tuple(warnings),
            base_revision=base_revision,
        )

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "summary": self.summary,
            "operations": [operation.to_json() for operation in self.operations],
            "warnings": list(self.warnings),
        }
        if self.base_revision is not None:
            payload["base_revision"] = self.base_revision
        return payload


@dataclass(frozen=True)
class EditProposalApplyResult:
    project: dict[str, Any]
    diff: dict[str, list[dict[str, Any]]]
    changed_segment_ids: tuple[str, ...]
    applied_operation_ids: tuple[str, ...]


def apply_edit_proposal(
    project: Mapping[str, Any],
    proposal: CodexEditProposal | Mapping[str, Any],
    *,
    selected_operation_ids: Iterable[str] | None = None,
    expected_revision: int | None = None,
    current_revision: int | None = None,
) -> EditProposalApplyResult:
    """Validate and apply a proposal to a copy of ``project`` atomically."""
    parsed = proposal if isinstance(proposal, CodexEditProposal) else CodexEditProposal.from_json(proposal)
    if expected_revision is not None and current_revision is not None and expected_revision != current_revision:
        raise EditProposalRevisionConflict(
            f"proposal revision {expected_revision} does not match current revision {current_revision}"
        )
    if parsed.base_revision is not None and current_revision is not None and parsed.base_revision != current_revision:
        raise EditProposalRevisionConflict(
            f"proposal revision {parsed.base_revision} does not match current revision {current_revision}"
        )

    selected = set(selected_operation_ids) if selected_operation_ids is not None else None
    candidate = deepcopy(dict(project))
    before_segments = deepcopy(candidate.get("segments", []))
    applied_ids: list[str] = []
    changed_ids: set[str] = set()
    try:
        for operation in parsed.operations:
            if selected is not None and operation.operation_id not in selected:
                continue
            _apply_operation(candidate, operation, changed_ids)
            applied_ids.append(operation.operation_id)
        if not applied_ids:
            raise EditProposalError("no proposal operations were selected")
        validated = validate_project(candidate)
    except (EditProposalError, SubtitleProjectError, TypeError, ValueError) as error:
        if isinstance(error, EditProposalError):
            raise
        raise EditProposalError(str(error)) from error

    after_segments = deepcopy(validated.get("segments", []))
    return EditProposalApplyResult(
        project=validated,
        diff=build_segment_diff(before_segments, after_segments),
        changed_segment_ids=tuple(sorted(changed_ids)),
        applied_operation_ids=tuple(applied_ids),
    )


def build_segment_diff(
    before_segments: list[Mapping[str, Any]],
    after_segments: list[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    before = {str(item.get("id")): dict(item) for item in before_segments}
    after = {str(item.get("id")): dict(item) for item in after_segments}
    added = [deepcopy(after[key]) for key in after if key not in before]
    removed = [deepcopy(before[key]) for key in before if key not in after]
    updated = [
        {"before": deepcopy(before[key]), "after": deepcopy(after[key])}
        for key in after
        if key in before and before[key] != after[key]
    ]
    return {"added": added, "updated": updated, "removed": removed}


def build_undo_entry(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    return {"before": deepcopy(dict(before)), "after": deepcopy(dict(after)), "kind": "codex_proposal"}


def _apply_operation(
    project: dict[str, Any],
    operation: EditOperation,
    changed_ids: set[str],
) -> None:
    segments = project.get("segments")
    if not isinstance(segments, list):
        raise EditProposalError("project.segments must be an array")
    if operation.type == "update_segment":
        index = _find_segment_index(segments, operation.segment_id)
        changes = dict(operation.changes or {})
        if not changes:
            raise EditProposalError(f"{operation.operation_id}: changes must not be empty")
        segments[index].update(deepcopy(changes))
        _set_manual_flags(segments[index], changes)
        changed_ids.add(operation.segment_id)
        return
    if operation.type == "delete_segment":
        index = _find_segment_index(segments, operation.segment_id)
        segments.pop(index)
        changed_ids.add(operation.segment_id)
        return
    if operation.type == "add_segment":
        if operation.segment is None:
            raise EditProposalError(f"{operation.operation_id}: segment is required")
        segment = deepcopy(dict(operation.segment))
        segment_id = str(segment.get("id", ""))
        if not segment_id:
            raise EditProposalError(f"{operation.operation_id}: segment.id is required")
        if any(str(item.get("id")) == segment_id for item in segments):
            raise EditProposalError(f"segment id already exists: {segment_id}")
        segments.append(segment)
        changed_ids.add(segment_id)
        return
    if operation.type == "split_segment":
        _apply_split(segments, operation, changed_ids)
        return
    if operation.type == "merge_segments":
        _apply_merge(segments, operation, changed_ids)
        return
    raise EditProposalError(f"unsupported operation type: {operation.type}")


def _apply_split(
    segments: list[dict[str, Any]],
    operation: EditOperation,
    changed_ids: set[str],
) -> None:
    index = _find_segment_index(segments, operation.segment_id)
    original = deepcopy(segments[index])
    if operation.split_at is None or not original["start"] < operation.split_at < original["end"]:
        raise EditProposalError(f"{operation.operation_id}: split_at must be inside the segment")
    new_id = operation.new_segment_id or f"{operation.segment_id}-split"
    if any(str(item.get("id")) == new_id for item in segments):
        raise EditProposalError(f"segment id already exists: {new_id}")
    first = deepcopy(original)
    second = deepcopy(original)
    first["end"] = operation.split_at
    second["id"] = new_id
    second["start"] = operation.split_at
    if operation.first_text is not None:
        first["text"] = operation.first_text
    if operation.second_text is not None:
        second["text"] = operation.second_text
    _set_manual_flags(first, {"start": first["start"], "end": first["end"], "text": first["text"]})
    _set_manual_flags(second, {"start": second["start"], "end": second["end"], "text": second["text"]})
    segments[index:index + 1] = [first, second]
    changed_ids.update({operation.segment_id, new_id})


def _apply_merge(
    segments: list[dict[str, Any]],
    operation: EditOperation,
    changed_ids: set[str],
) -> None:
    if len(operation.segment_ids) < 2 or len(set(operation.segment_ids)) != len(operation.segment_ids):
        raise EditProposalError(f"{operation.operation_id}: at least two unique segment_ids are required")
    indexes = [_find_segment_index(segments, segment_id) for segment_id in operation.segment_ids]
    ordered = sorted(indexes)
    selected = [segments[index] for index in ordered]
    merged = deepcopy(selected[0])
    merged["start"] = min(float(item["start"]) for item in selected)
    merged["end"] = max(float(item["end"]) for item in selected)
    merged["text"] = str(operation.first_text) if operation.first_text is not None else " ".join(
        str(item.get("text", "")).strip() for item in selected
    ).strip()
    _set_manual_flags(merged, {"start": merged["start"], "end": merged["end"], "text": merged["text"]})
    for index in reversed(ordered):
        segments.pop(index)
    segments.insert(ordered[0], merged)
    changed_ids.update(operation.segment_ids)


def _find_segment_index(segments: list[dict[str, Any]], segment_id: str) -> int:
    if not segment_id:
        raise EditProposalError("segment_id is required")
    for index, segment in enumerate(segments):
        if str(segment.get("id")) == segment_id:
            return index
    raise EditProposalError(f"segment not found: {segment_id}")


def _set_manual_flags(segment: dict[str, Any], changes: Mapping[str, Any]) -> None:
    if "text" in changes:
        segment["manual_text"] = True
    if "start" in changes or "end" in changes:
        segment["manual_timing"] = True
    if "speaker" in changes:
        segment["manual_speaker"] = True
    if "subtitle_line_count" in changes:
        segment["manual_line_count"] = True
    if "subtitle_font_scale" in changes:
        segment["manual_font_scale"] = True
    if "subtitle_font_family" in changes:
        segment["manual_font_family"] = True


def _require_object(value: object, field: str) -> None:
    if not isinstance(value, Mapping):
        raise EditProposalError(f"{field} must be an object")


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise EditProposalError(f"{field} contains unsupported fields: {', '.join(unknown)}")

