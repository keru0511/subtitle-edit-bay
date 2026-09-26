from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from collections.abc import Callable, Iterable, Mapping
from typing import TypedDict

from .data_boundary import coerce_float, is_object_dict, is_object_iterable, is_object_sequence
from .subtitle_project import SubtitleProjectError, validate_project


class BulkEditError(ValueError):
    pass


@dataclass(frozen=True)
class BulkEditQuery:
    text: str = ""
    exact: bool = False
    case_sensitive: bool = False
    regex: bool = False
    speaker: str = ""
    start: float | None = None
    end: float | None = None
    segment_ids: frozenset[str] = frozenset()
    review_rule_id: str = ""


@dataclass(frozen=True)
class BulkEditAction:
    text_replace_from: str | None = None
    text_replace_to: str = ""
    speaker_rename: Mapping[str, str] | None = None
    style: Mapping[str, object] | None = None
    time_shift: float = 0.0


class BulkEditChange(TypedDict):
    id: str
    before: dict[object, object]
    after: dict[object, object]


@dataclass(frozen=True)
class BulkEditPreview:
    segment_ids: tuple[str, ...]
    changes: tuple[BulkEditChange, ...]


@dataclass(frozen=True)
class BulkEditResult:
    project: dict[object, object]
    preview: BulkEditPreview
    before: dict[object, object]
    after: dict[object, object]


def _project_segments(project: Mapping[object, object]) -> list[dict[object, object]]:
    value = project.get("segments", [])
    if not is_object_sequence(value) or isinstance(value, (str, bytes)):
        raise BulkEditError("segments must be an array")
    segments: list[dict[object, object]] = []
    for segment in value:
        if not is_object_dict(segment):
            raise BulkEditError("segment must be an object")
        segments.append(segment)
    return segments


def _review_rule_ids(segment: Mapping[object, object]) -> set[str]:
    values = segment.get("review_rule_ids", [])
    if not is_object_iterable(values):
        raise BulkEditError("review_rule_ids must be iterable")
    return {str(item) for item in values}


def find_matching_segment_ids(
    project: Mapping[object, object],
    query: BulkEditQuery,
) -> list[str]:
    pattern = _compile_pattern(query)
    result: list[str] = []
    for segment in _project_segments(project):
        segment_id = str(segment.get("id", ""))
        text = str(segment.get("text", ""))
        if query.segment_ids and segment_id not in query.segment_ids:
            continue
        if query.speaker and str(segment.get("speaker", "")) != query.speaker:
            continue
        if query.start is not None and coerce_float(segment.get("end", 0.0)) <= query.start:
            continue
        if query.end is not None and coerce_float(segment.get("start", 0.0)) >= query.end:
            continue
        if query.review_rule_id and query.review_rule_id not in _review_rule_ids(segment):
            continue
        if pattern is not None and not _matches(text, query, pattern):
            continue
        result.append(segment_id)
    return result


def preview_bulk_edit(
    project: Mapping[object, object],
    query: BulkEditQuery,
    action: BulkEditAction,
    *,
    excluded_segment_ids: Iterable[str] = (),
) -> BulkEditPreview:
    ids = find_matching_segment_ids(project, query)
    excluded = {str(item) for item in excluded_segment_ids}
    changes: list[BulkEditChange] = []
    by_id = {str(item.get("id")): item for item in _project_segments(project)}
    for segment_id in ids:
        if segment_id in excluded:
            continue
        before = by_id[segment_id]
        after = _apply_to_segment(before, action, query)
        if before != after:
            changes.append({"id": segment_id, "before": deepcopy(before), "after": after})
    return BulkEditPreview(tuple(item["id"] for item in changes), tuple(changes))


def apply_bulk_edit(
    project: Mapping[object, object],
    query: BulkEditQuery,
    action: BulkEditAction,
    *,
    excluded_segment_ids: Iterable[str] = (),
    cancel_check: Callable[[], bool] | None = None,
) -> BulkEditResult:
    before = deepcopy(dict(project))
    preview = preview_bulk_edit(before, query, action, excluded_segment_ids=excluded_segment_ids)
    candidate = deepcopy(before)
    by_id = {str(item.get("id")): item for item in _project_segments(candidate)}
    for index, segment_id in enumerate(preview.segment_ids):
        if cancel_check and cancel_check():
            raise BulkEditError("bulk edit cancelled")
        by_id[segment_id].clear()
        by_id[segment_id].update(deepcopy(preview.changes[index]["after"]))
    try:
        validated = validate_project(candidate)
    except (SubtitleProjectError, TypeError, ValueError) as error:
        raise BulkEditError(str(error)) from error
    return BulkEditResult(validated, preview, before, deepcopy(validated))


def _compile_pattern(query: BulkEditQuery) -> re.Pattern[str] | None:
    if not query.text:
        return None
    flags = 0 if query.case_sensitive else re.IGNORECASE
    try:
        return re.compile(query.text if query.regex else re.escape(query.text), flags)
    except re.error as error:
        raise BulkEditError(f"無効な正規表現です: {error}") from error


def _matches(text: str, query: BulkEditQuery, pattern: re.Pattern[str]) -> bool:
    if query.exact:
        return bool(pattern.fullmatch(text))
    return bool(pattern.search(text))


def _apply_to_segment(
    segment: Mapping[object, object],
    action: BulkEditAction,
    query: BulkEditQuery,
) -> dict[object, object]:
    updated = deepcopy(dict(segment))
    if action.text_replace_from is not None:
        try:
            replacement_query = BulkEditQuery(
                text=action.text_replace_from,
                exact=query.exact,
                case_sensitive=query.case_sensitive,
                regex=query.regex,
            )
            replacement_pattern = _compile_pattern(replacement_query)
            text = str(updated.get("text", ""))
            if replacement_pattern is None:
                raise BulkEditError("置換文字列が空です")
            if query.exact and not replacement_pattern.fullmatch(text):
                raise BulkEditError("置換対象がexact queryと一致しません")
            updated["text"] = replacement_pattern.sub(action.text_replace_to, text)
        except re.error as error:
            raise BulkEditError(f"無効な置換正規表現です: {error}") from error
        updated["manual_text"] = True
    rename = dict(action.speaker_rename or {})
    if str(updated.get("speaker", "")) in rename:
        updated["speaker"] = rename[str(updated.get("speaker", ""))]
        updated["manual_speaker"] = True
    for key, value in dict(action.style or {}).items():
        if key not in {"subtitle_font_family", "subtitle_font_scale", "position", "emphasis"}:
            raise BulkEditError(f"unsupported style field: {key}")
        updated[key] = value
    if action.time_shift:
        updated["start"] = coerce_float(updated.get("start", 0.0)) + action.time_shift
        updated["end"] = coerce_float(updated.get("end", 0.0)) + action.time_shift
        updated["manual_timing"] = True
    return updated
