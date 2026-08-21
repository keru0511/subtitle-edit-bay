"""Backward-compatible multi-source short-video project primitives."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


MULTI_SOURCE_SCHEMA_VERSION = 1


class MultiSourceError(ValueError):
    """Raised when source or timeline references are invalid."""


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise MultiSourceError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise MultiSourceError(f"{field} must be finite")
    if result < 0 or (positive and result <= 0):
        raise MultiSourceError(f"{field} must be {'positive' if positive else 'non-negative'}")
    return result


def _positive_int(value: Any, field: str) -> int:
    number = _number(value, field, positive=True)
    if not number.is_integer():
        raise MultiSourceError(f"{field} must be an integer")
    return int(number)


def _identity(path: str, metadata: Mapping[str, Any]) -> str:
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
    return hashlib.sha256(json.dumps(stable_metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def source_id_for(path: str, metadata: Mapping[str, Any] | None = None) -> str:
    return f"source-{_identity(path, metadata or {})[:16]}"


def _source(path: str, metadata: Mapping[str, Any] | None = None, *, source_id: str | None = None) -> dict[str, Any]:
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


def ensure_multi_source_project(project: Mapping[str, Any]) -> dict[str, Any]:
    """Migrate a single-source project in memory without changing its source file."""

    if not isinstance(project, Mapping):
        raise MultiSourceError("project must be an object")
    result = copy.deepcopy(dict(project))
    existing = result.get("sources")
    if isinstance(existing, Mapping):
        existing = [existing]
    if existing:
        sources: list[dict[str, Any]] = []
        for index, value in enumerate(existing):
            if not isinstance(value, Mapping):
                raise MultiSourceError(f"source {index} must be an object")
            item = dict(value)
            path = str(item.get("path", item.get("file", "")))
            if not path:
                raise MultiSourceError(f"source {index} is missing a path")
            sources.append(_source(path, item, source_id=str(item.get("source_id", item.get("id", ""))) or None))
    else:
        video = result.get("video")
        source = result.get("source")
        path = result.get("video_path")
        metadata: Mapping[str, Any] = {}
        if isinstance(video, Mapping):
            path = path or video.get("path")
            metadata = video
        elif isinstance(source, Mapping):
            path = path or source.get("path")
            metadata = source
        if not path:
            raise MultiSourceError("single-source project is missing a video path")
        sources = [_source(str(path), metadata)]
    result["sources"] = sources
    result["multi_source_schema_version"] = MULTI_SOURCE_SCHEMA_VERSION
    clips = result.get("clips", [])
    if isinstance(clips, list):
        result["clips"] = [
            {**clip, "source_id": clip.get("source_id", sources[0]["source_id"])} if isinstance(clip, Mapping) else clip
            for clip in clips
        ]
    return result


def add_source(project: Mapping[str, Any], path: str, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result = ensure_multi_source_project(project)
    new_source = _source(path, metadata)
    if any(item["fingerprint"] == new_source["fingerprint"] for item in result["sources"]):
        raise MultiSourceError("source is already registered")
    result["sources"].append(new_source)
    return result


def mark_source_missing(project: Mapping[str, Any], source_id: str, missing: bool = True) -> dict[str, Any]:
    result = ensure_multi_source_project(project)
    found = False
    for source in result["sources"]:
        if source["source_id"] == source_id:
            source["missing"] = missing
            found = True
    if not found:
        raise MultiSourceError(f"unknown source: {source_id}")
    return result


def relink_source(project: Mapping[str, Any], source_id: str, path: str, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result = ensure_multi_source_project(project)
    for source in result["sources"]:
        if source["source_id"] == source_id:
            source.update(dict(metadata or {}), path=path, missing=False)
            source["source_id"] = source_id
            source["fingerprint"] = _identity(path, {**source, **(metadata or {})})
            return result
    raise MultiSourceError(f"unknown source: {source_id}")


def add_clip(
    project: Mapping[str, Any],
    source_id: str,
    source_start: float,
    source_end: float,
    *,
    timeline_start: float | None = None,
    timeline_end: float | None = None,
) -> dict[str, Any]:
    result = ensure_multi_source_project(project)
    if not any(source["source_id"] == source_id and not source.get("missing", False) for source in result["sources"]):
        raise MultiSourceError(f"source is unavailable: {source_id}")
    source_start = _number(source_start, "source_start")
    source_end = _number(source_end, "source_end")
    if source_end <= source_start:
        raise MultiSourceError("source_end must be after source_start")
    timeline_start = source_start if timeline_start is None else _number(timeline_start, "timeline_start")
    timeline_end = timeline_start + (source_end - source_start) if timeline_end is None else _number(timeline_end, "timeline_end")
    if timeline_end <= timeline_start:
        raise MultiSourceError("timeline_end must be after timeline_start")
    result.setdefault("clips", []).append(
        {
            "source_id": source_id,
            "source_start": source_start,
            "source_end": source_end,
            "timeline_start": timeline_start,
            "timeline_end": timeline_end,
        }
    )
    return result


def remove_source(project: Mapping[str, Any], source_id: str, *, remove_clips: bool = False) -> dict[str, Any]:
    result = ensure_multi_source_project(project)
    references = [clip for clip in result.get("clips", []) if isinstance(clip, Mapping) and clip.get("source_id") == source_id]
    if references and not remove_clips:
        raise MultiSourceError("source is still referenced by clips")
    sources = [source for source in result["sources"] if source["source_id"] != source_id]
    if len(sources) == len(result["sources"]):
        raise MultiSourceError(f"unknown source: {source_id}")
    result["sources"] = sources
    if remove_clips:
        result["clips"] = [clip for clip in result.get("clips", []) if not isinstance(clip, Mapping) or clip.get("source_id") != source_id]
    return result


def normalization_plan(
    project: Mapping[str, Any], *, target_fps: float | None = None, target_width: int | None = None, target_height: int | None = None
) -> dict[str, Any]:
    result = ensure_multi_source_project(project)
    sources = result["sources"]
    target_fps = (
        max(_number(source.get("fps") if source.get("fps") is not None else 30, "source fps", positive=True) for source in sources)
        if target_fps is None
        else _number(target_fps, "target_fps", positive=True)
    )
    target_width = (
        max(_positive_int(source.get("width") if source.get("width") is not None else 1920, "source width") for source in sources)
        if target_width is None
        else _positive_int(target_width, "target_width")
    )
    target_height = (
        max(_positive_int(source.get("height") if source.get("height") is not None else 1080, "source height") for source in sources)
        if target_height is None
        else _positive_int(target_height, "target_height")
    )
    plan = []
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
                "source_id": source["source_id"],
                "filters": video_filters,
                "video_filters": video_filters,
                "audio_filters": audio_filters,
                "target_fps": target_fps,
                "target_size": [target_width, target_height],
            }
        )
    return {"fps": target_fps, "width": target_width, "height": target_height, "audio_sample_rate": 48000, "sources": plan}


def merge_source_candidates(candidates: Sequence[Mapping[str, Any]], *, limit: int = 10) -> list[dict[str, Any]]:
    ordered = sorted((copy.deepcopy(dict(item)) for item in candidates), key=lambda item: (-float(item.get("score", 0)), str(item.get("candidate_id", ""))))
    selected: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    for candidate in ordered:
        if len(selected) >= limit:
            break
        if candidate.get("source_id") not in source_ids:
            selected.append(candidate)
            source_ids.add(str(candidate.get("source_id")))
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


def build_concat_filter_script(project: Mapping[str, Any], *, output_path: str | None = None) -> str:
    result = ensure_multi_source_project(project)
    plan = normalization_plan(result)
    sources = {source["source_id"]: source for source in result["sources"]}
    lines: list[str] = []
    for index, source in enumerate(result["sources"]):
        if source.get("missing", False):
            raise MultiSourceError(f"source is missing: {source['source_id']}")
        path = source.get("path", "")
        if not path:
            raise MultiSourceError(f"source path is empty: {source['source_id']}")
        source_plan = next(item for item in plan["sources"] if item["source_id"] == source["source_id"])
        lines.append(f"INPUT {index}: {path}")
        lines.append(f"[in{index}:v]{','.join(source_plan['video_filters'])}[v{index}]")
        lines.append(f"[in{index}:a]{','.join(source_plan['audio_filters'])}[a{index}]")
    for index, clip in enumerate(result.get("clips", [])):
        if not isinstance(clip, Mapping) or clip.get("source_id") not in sources:
            raise MultiSourceError("clip references an unknown source")
        lines.append(
            f"CLIP {index}: source={clip['source_id']} source_time={clip.get('source_start', 0)}-{clip.get('source_end')} timeline_time={clip.get('timeline_start', 0)}-{clip.get('timeline_end')}"
        )
    if output_path:
        lines.append(f"OUTPUT: {output_path}")
    return "\n".join(lines) + "\n"
