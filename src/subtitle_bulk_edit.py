from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

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
    speaker_rename: Mapping[str, str] = None  # type: ignore[assignment]
    style: Mapping[str, Any] = None  # type: ignore[assignment]
    time_shift: float = 0.0


@dataclass(frozen=True)
class BulkEditPreview:
    segment_ids: tuple[str, ...]
    changes: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class BulkEditResult:
    project: dict[str, Any]
    preview: BulkEditPreview
    before: dict[str, Any]
    after: dict[str, Any]


def find_matching_segment_ids(
    project: Mapping[str, Any],
    query: BulkEditQuery,
) -> list[str]:
    pattern = _compile_pattern(query)
    result: list[str] = []
    for segment in project.get("segments", []):
        segment_id = str(segment.get("id", ""))
        text = str(segment.get("text", ""))
        if query.segment_ids and segment_id not in query.segment_ids:
            continue
        if query.speaker and str(segment.get("speaker", "")) != query.speaker:
            continue
        if query.start is not None and float(segment.get("end", 0.0)) <= query.start:
            continue
        if query.end is not None and float(segment.get("start", 0.0)) >= query.end:
            continue
        if query.review_rule_id and query.review_rule_id not in {str(item) for item in segment.get("review_rule_ids", [])}:
            continue
        if pattern is not None and not _matches(text, query, pattern):
            continue
        result.append(segment_id)
    return result


def preview_bulk_edit(
    project: Mapping[str, Any],
    query: BulkEditQuery,
    action: BulkEditAction,
    *,
    excluded_segment_ids: Iterable[str] = (),
) -> BulkEditPreview:
    ids = find_matching_segment_ids(project, query)
    excluded = {str(item) for item in excluded_segment_ids}
    changes: list[dict[str, Any]] = []
    by_id = {str(item.get("id")): item for item in project.get("segments", [])}
    for segment_id in ids:
        if segment_id in excluded:
            continue
        before = by_id[segment_id]
        after = _apply_to_segment(before, action, query)
        if before != after:
            changes.append({"id": segment_id, "before": deepcopy(before), "after": after})
    return BulkEditPreview(tuple(item["id"] for item in changes), tuple(changes))


def apply_bulk_edit(
    project: Mapping[str, Any],
    query: BulkEditQuery,
    action: BulkEditAction,
    *,
    excluded_segment_ids: Iterable[str] = (),
    cancel_check: callable | None = None,
) -> BulkEditResult:
    before = deepcopy(dict(project))
    preview = preview_bulk_edit(before, query, action, excluded_segment_ids=excluded_segment_ids)
    candidate = deepcopy(before)
    by_id = {str(item.get("id")): item for item in candidate.get("segments", [])}
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
    segment: Mapping[str, Any],
    action: BulkEditAction,
    query: BulkEditQuery,
) -> dict[str, Any]:
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
        updated["start"] = float(updated.get("start", 0.0)) + action.time_shift
        updated["end"] = float(updated.get("end", 0.0)) + action.time_shift
        updated["manual_timing"] = True
    return updated

