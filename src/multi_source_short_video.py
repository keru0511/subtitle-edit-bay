"""Backward-compatible multi-source short-video project primitives."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import TypedDict, cast

from src.data_boundary import coerce_float, is_object_iterable, is_object_list, is_object_mapping


MULTI_SOURCE_SCHEMA_VERSION = 1


class MultiSourceError(ValueError):
    """Raised when source or timeline references are invalid."""


class SourceNormalizationPlan(TypedDict):
    source_id: str
    filters: list[str]
    video_filters: list[str]
    audio_filters: list[str]
    target_fps: float
    target_size: list[int]


class NormalizationPlan(TypedDict):
    fps: float
    width: int
    height: int
    audio_sample_rate: int
    sources: list[SourceNormalizationPlan]


def _string_mapping(value: object, field: str) -> Mapping[str, object]:
    if not is_object_mapping(value) or any(not isinstance(key, str) for key in value):
        raise MultiSourceError(f"{field} must be an object with string keys")
    return cast(Mapping[str, object], value)


def _sources(project: Mapping[str, object]) -> list[dict[str, object]]:
    value = project.get("sources")
    if not is_object_list(value) or any(
        not isinstance(source, dict) or any(not isinstance(key, str) for key in source) for source in value
    ):
        raise MultiSourceError("sources must be a list of objects")
    return cast(list[dict[str, object]], value)


def _clips(project: Mapping[str, object]) -> list[object]:
    value = project.get("clips")
    if not is_object_list(value):
        raise MultiSourceError("clips must be a list")
    return value


def _source_id(source: Mapping[str, object]) -> str:
    source_id = source.get("source_id")
    if not isinstance(source_id, str):
        raise MultiSourceError("source_id must be a string")
    return source_id


def _number(value: object, field: str, *, positive: bool = False) -> float:
    try:
        result = coerce_float(value)
    except (TypeError, ValueError) as exc:
        raise MultiSourceError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise MultiSourceError(f"{field} must be finite")
    if result < 0 or (positive and result <= 0):
        raise MultiSourceError(f"{field} must be {'positive' if positive else 'non-negative'}")
    return result


def _positive_int(value: object, field: str) -> int:
    number = _number(value, field, positive=True)
    if not number.is_integer():
        raise MultiSourceError(f"{field} must be an integer")
    return int(number)


def _identity(path: str, metadata: Mapping[str, object]) -> str:
    explicit = metadata.get("media_fingerprint") or metadata.get("content_hash")
    if explicit:
        return str(explicit)
    stable_metadata = {
        key: metadata.get(key)
        for key in ("duration", "fps", "width", "height", "audio_sample_rate", "stream_layout")
        if metadata.get(key) is not None
    }
    if not stable_metadata:
        stable_metadata = {"path": path}
    return hashlib.sha256(
        json.dumps(stable_metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def source_id_for(path: str, metadata: Mapping[str, object] | None = None) -> str:
    return f"source-{_identity(path, metadata or {})[:16]}"


def _source(
    path: str, metadata: Mapping[str, object] | None = None, *, source_id: str | None = None
) -> dict[str, object]:
    values = dict(metadata or {})
    values.update(
        {
            "source_id": source_id or source_id_for(path, values),
            "path": path,
            "fingerprint": _identity(path, values),
            "missing": bool(values.get("missing", False)),
        }
    )
    return values


def ensure_multi_source_project(project: Mapping[str, object]) -> dict[str, object]:
    """Migrate a single-source project in memory without changing its source file."""

    if not isinstance(project, Mapping):
        raise MultiSourceError("project must be an object")
    result = copy.deepcopy(dict(project))
    existing = result.get("sources")
    if is_object_mapping(existing):
        existing = [existing]
    if existing:
        if not is_object_iterable(existing):
            raise MultiSourceError("sources must be iterable")
        sources: list[dict[str, object]] = []
        for index, value in enumerate(existing):
            item = dict(_string_mapping(value, f"source {index}"))
            path = str(item.get("path", item.get("file", "")))
            if not path:
                raise MultiSourceError(f"source {index} is missing a path")
            sources.append(_source(path, item, source_id=str(item.get("source_id", item.get("id", ""))) or None))
    else:
        video = result.get("video")
        source = result.get("source")
        path_value = result.get("video_path")
        metadata: Mapping[str, object] = {}
        if is_object_mapping(video):
            metadata = _string_mapping(video, "video")
            path_value = path_value or metadata.get("path")
        elif is_object_mapping(source):
            metadata = _string_mapping(source, "source")
            path_value = path_value or metadata.get("path")
        if not path_value:
            raise MultiSourceError("single-source project is missing a video path")
        sources = [_source(str(path_value), metadata)]
    result["sources"] = sources
    result["multi_source_schema_version"] = MULTI_SOURCE_SCHEMA_VERSION
    clips = result.get("clips", [])
    if is_object_list(clips):
        migrated_clips: list[object] = []
        for clip in clips:
            if is_object_mapping(clip):
                migrated = dict(_string_mapping(clip, "clip"))
                migrated["source_id"] = clip.get("source_id", _source_id(sources[0]))
                migrated_clips.append(migrated)
            else:
                migrated_clips.append(clip)
        result["clips"] = migrated_clips
    return result


def add_source(
    project: Mapping[str, object], path: str, metadata: Mapping[str, object] | None = None
) -> dict[str, object]:
    result = ensure_multi_source_project(project)
    sources = _sources(result)
    new_source = _source(path, metadata)
    if any(item["fingerprint"] == new_source["fingerprint"] for item in sources):
        raise MultiSourceError("source is already registered")
    sources.append(new_source)
    return result


def mark_source_missing(project: Mapping[str, object], source_id: str, missing: bool = True) -> dict[str, object]:
    result = ensure_multi_source_project(project)
    found = False
    for source in _sources(result):
        if _source_id(source) == source_id:
            source["missing"] = missing
            found = True
    if not found:
        raise MultiSourceError(f"unknown source: {source_id}")
    return result


def relink_source(
    project: Mapping[str, object], source_id: str, path: str, metadata: Mapping[str, object] | None = None
) -> dict[str, object]:
    result = ensure_multi_source_project(project)
    for source in _sources(result):
        if _source_id(source) == source_id:
            source.update(dict(metadata or {}), path=path, missing=False)
            source["source_id"] = source_id
            source["fingerprint"] = _identity(path, {**source, **(metadata or {})})
            return result
    raise MultiSourceError(f"unknown source: {source_id}")


def add_clip(
    project: Mapping[str, object],
    source_id: str,
    source_start: float,
    source_end: float,
    *,
    timeline_start: float | None = None,
    timeline_end: float | None = None,
) -> dict[str, object]:
    result = ensure_multi_source_project(project)
    if not any(_source_id(source) == source_id and not source.get("missing", False) for source in _sources(result)):
        raise MultiSourceError(f"source is unavailable: {source_id}")
    source_start = _number(source_start, "source_start")
    source_end = _number(source_end, "source_end")
    if source_end <= source_start:
        raise MultiSourceError("source_end must be after source_start")
    timeline_start = source_start if timeline_start is None else _number(timeline_start, "timeline_start")
    timeline_end = (
        timeline_start + (source_end - source_start) if timeline_end is None else _number(timeline_end, "timeline_end")
    )
    if timeline_end <= timeline_start:
        raise MultiSourceError("timeline_end must be after timeline_start")
    if "clips" not in result:
        result["clips"] = []
    _clips(result).append(
        {
            "source_id": source_id,
            "source_start": source_start,
            "source_end": source_end,
            "timeline_start": timeline_start,
            "timeline_end": timeline_end,
        }
    )
    return result


def remove_source(project: Mapping[str, object], source_id: str, *, remove_clips: bool = False) -> dict[str, object]:
    result = ensure_multi_source_project(project)
    clips = result.get("clips", [])
    if not is_object_iterable(clips):
        raise MultiSourceError("clips must be iterable")
    references = [clip for clip in clips if is_object_mapping(clip) and clip.get("source_id") == source_id]
    if references and not remove_clips:
        raise MultiSourceError("source is still referenced by clips")
    current_sources = _sources(result)
    sources = [source for source in current_sources if _source_id(source) != source_id]
    if len(sources) == len(current_sources):
        raise MultiSourceError(f"unknown source: {source_id}")
    result["sources"] = sources
    if remove_clips:
        result["clips"] = [clip for clip in clips if not is_object_mapping(clip) or clip.get("source_id") != source_id]
    return result


def normalization_plan(
    project: Mapping[str, object],
    *,
    target_fps: float | None = None,
    target_width: int | None = None,
    target_height: int | None = None,
) -> NormalizationPlan:
    result = ensure_multi_source_project(project)
    sources = _sources(result)
    target_fps = (
        max(
            _number(source.get("fps") if source.get("fps") is not None else 30, "source fps", positive=True)
            for source in sources
        )
        if target_fps is None
        else _number(target_fps, "target_fps", positive=True)
    )
    target_width = (
        max(
            _positive_int(source.get("width") if source.get("width") is not None else 1920, "source width")
            for source in sources
        )
        if target_width is None
        else _positive_int(target_width, "target_width")
    )
    target_height = (
        max(
            _positive_int(source.get("height") if source.get("height") is not None else 1080, "source height")
            for source in sources
        )
        if target_height is None
        else _positive_int(target_height, "target_height")
    )
    plan: list[SourceNormalizationPlan] = []
    for source in sources:
        video_filters = [
            f"fps={target_fps:g}",
            f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease",
            f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black",
            "setsar=1",
            "format=yuv420p",
        ]
        _positive_int(
            source.get("audio_sample_rate") if source.get("audio_sample_rate") is not None else 48000,
            "audio_sample_rate",
        )
        audio_filters = ["aresample=48000"]
        plan.append(
            {
                "source_id": _source_id(source),
                "filters": video_filters,
                "video_filters": video_filters,
                "audio_filters": audio_filters,
                "target_fps": target_fps,
                "target_size": [target_width, target_height],
            }
        )
    return {
        "fps": target_fps,
        "width": target_width,
        "height": target_height,
        "audio_sample_rate": 48000,
        "sources": plan,
    }


def merge_source_candidates(candidates: Sequence[Mapping[str, object]], *, limit: int = 10) -> list[dict[str, object]]:
    def sort_key(item: Mapping[str, object]) -> tuple[float, str]:
        return -coerce_float(item.get("score", 0)), str(item.get("candidate_id", ""))

    ordered = sorted((copy.deepcopy(dict(item)) for item in candidates), key=sort_key)
    selected: list[dict[str, object]] = []
    source_ids: set[str] = set()
    for candidate in ordered:
        if len(selected) >= limit:
            break
        candidate_source_id = candidate.get("source_id")
        if candidate_source_id not in source_ids:
            selected.append(candidate)
            source_ids.add(str(candidate_source_id))
    for candidate in ordered:
        if len(selected) >= limit:
            break
        if candidate not in selected:
            selected.append(candidate)
    return selected


def speaker_style_key(source_id: str, speaker: str, palette_size: int = 12) -> str:
    if palette_size < 1:
        raise MultiSourceError("palette_size must be positive")
    digest = hashlib.sha256(f"{source_id}\0{speaker}".encode("utf-8")).hexdigest()
    return f"speaker-style-{source_id}-{int(digest[:8], 16) % palette_size}"


def build_concat_filter_script(project: Mapping[str, object], *, output_path: str | None = None) -> str:
    result = ensure_multi_source_project(project)
    plan = normalization_plan(result)
    source_list = _sources(result)
    sources = {_source_id(source): source for source in source_list}
    lines: list[str] = []
    for index, source in enumerate(source_list):
        source_id = _source_id(source)
        if source.get("missing", False):
            raise MultiSourceError(f"source is missing: {source_id}")
        path = source.get("path", "")
        if not path:
            raise MultiSourceError(f"source path is empty: {source_id}")
        source_plan = next(item for item in plan["sources"] if item["source_id"] == source_id)
        lines.append(f"INPUT {index}: {path}")
        lines.append(f"[in{index}:v]{','.join(source_plan['video_filters'])}[v{index}]")
        lines.append(f"[in{index}:a]{','.join(source_plan['audio_filters'])}[a{index}]")
    clips = result.get("clips", [])
    if not is_object_iterable(clips):
        raise MultiSourceError("clips must be iterable")
    for index, clip in enumerate(clips):
        if not is_object_mapping(clip) or clip.get("source_id") not in sources:
            raise MultiSourceError("clip references an unknown source")
        lines.append(
            f"CLIP {index}: source={clip['source_id']} source_time={clip.get('source_start', 0)}-{clip.get('source_end')} timeline_time={clip.get('timeline_start', 0)}-{clip.get('timeline_end')}"
        )
    if output_path:
        lines.append(f"OUTPUT: {output_path}")
    return "\n".join(lines) + "\n"
