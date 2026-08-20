"""Lossless project timeline interchange and conservative EDL export."""

from __future__ import annotations

import copy
import json
import math
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


TIMELINE_SCHEMA_VERSION = 1


class TimelineInterchangeError(ValueError):
    """Raised when a timeline cannot be represented safely."""


def _atomic_json(destination: str | os.PathLike[str], payload: Mapping[str, Any], overwrite: bool) -> Path:
    path = Path(destination)
    if path.exists() and not overwrite:
        raise TimelineInterchangeError(f"destination already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists() and not overwrite:
            raise TimelineInterchangeError(f"destination already exists: {path}")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return path


def build_timeline_document(project: Mapping[str, Any], revision: str | int | None = None) -> dict[str, Any]:
    """Wrap a project in a versioned document with one canonical payload."""

    if not isinstance(project, Mapping):
        raise TimelineInterchangeError("project must be an object")
    project_payload = copy.deepcopy(dict(project))
    if revision is not None:
        project_payload["revision"] = revision
    document: dict[str, Any] = {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "project": project_payload,
    }
    return document


def export_timeline_json(
    project: Mapping[str, Any], destination: str | os.PathLike[str], *, revision: str | int | None = None, overwrite: bool = False
) -> Path:
    return _atomic_json(destination, build_timeline_document(project, revision), overwrite)


def import_timeline_json(source: str | os.PathLike[str]) -> dict[str, Any]:
    path = Path(source)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TimelineInterchangeError(f"unable to read timeline: {path}") from exc
    if not isinstance(document, Mapping) or document.get("schema_version") != TIMELINE_SCHEMA_VERSION:
        raise TimelineInterchangeError("unsupported timeline schema")
    if isinstance(document.get("project"), Mapping):
        project = document["project"]
        legacy_fields = {
            "revision": project.get("revision"),
            "source": project.get("source", project.get("sources", [])),
            "clips": project.get("clips", []),
            "transitions": project.get("transitions", []),
            "subtitles": project.get("subtitles", project.get("segments", [])),
            "audio": project.get("audio", project.get("audio_tracks", [])),
        }
        for key, expected in legacy_fields.items():
            if key in document and document[key] != expected:
                raise TimelineInterchangeError(f"timeline field conflicts with canonical project: {key}")
        return copy.deepcopy(dict(project))
    return {
        "revision": document.get("revision"),
        "source": copy.deepcopy(document.get("source", [])),
        "clips": copy.deepcopy(document.get("clips", [])),
        "transitions": copy.deepcopy(document.get("transitions", [])),
        "subtitles": copy.deepcopy(document.get("subtitles", [])),
        "audio": copy.deepcopy(document.get("audio", [])),
    }


def _edl_seconds(seconds: Any, field: str = "EDL time") -> float:
    try:
        value = float(seconds)
    except (TypeError, ValueError) as exc:
        raise TimelineInterchangeError(f"{field} must be numeric") from exc
    if not math.isfinite(value):
        raise TimelineInterchangeError(f"{field} must be finite")
    if value < 0:
        raise TimelineInterchangeError(f"{field} must not be negative")
    return value


def _timecode(seconds: Any, fps: int) -> str:
    value = _edl_seconds(seconds)
    frame = int(value * fps + 0.5)
    hours, frame = divmod(frame, fps * 3600)
    minutes, frame = divmod(frame, fps * 60)
    seconds_part, frames = divmod(frame, fps)
    return f"{hours:02d}:{minutes:02d}:{seconds_part:02d}:{frames:02d}"


def _clip_warnings(project: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    for transition in project.get("transitions", []) or []:
        if isinstance(transition, Mapping) and str(transition.get("type", "cut")).lower() not in {"cut", "dissolve"}:
            warnings.append(f"unsupported transition: {transition.get('type')}")
    for clip in project.get("clips", []) or []:
        if isinstance(clip, Mapping) and clip.get("effect"):
            warnings.append(f"unrepresentable effect on clip: {clip.get('id', 'unknown')}")
    return warnings


def export_edl(
    project: Mapping[str, Any], destination: str | os.PathLike[str], *, fps: int = 30, overwrite: bool = False
) -> Path:
    if fps <= 0 or fps > 240:
        raise TimelineInterchangeError("fps must be between 1 and 240")
    clips = project.get("clips", []) or []
    lines = [f"TITLE: {project.get('name', 'Subtitle Edit Bay')}", "FCM: NON-DROP FRAME", ""]
    source_entries = project.get("source", project.get("sources", [])) or []
    if isinstance(source_entries, Mapping):
        source_entries = [source_entries]
    source_paths = {
        str(source.get("id")): str(source.get("path"))
        for source in source_entries
        if isinstance(source, Mapping) and source.get("id") and source.get("path")
    }
    for index, clip in enumerate(clips, start=1):
        if not isinstance(clip, Mapping):
            raise TimelineInterchangeError("every clip must be an object")
        source_start = clip.get("source_start", clip.get("in", clip.get("start", 0)))
        source_end = clip.get("source_end", clip.get("out", clip.get("end")))
        timeline_start = clip.get("timeline_start", clip.get("start", 0))
        timeline_end = clip.get("timeline_end", clip.get("end"))
        if source_end is None or timeline_end is None:
            raise TimelineInterchangeError(f"clip {index} is missing an end time")
        source_start_value = _edl_seconds(source_start, f"clip {index} source start")
        source_end_value = _edl_seconds(source_end, f"clip {index} source end")
        timeline_start_value = _edl_seconds(timeline_start, f"clip {index} timeline start")
        timeline_end_value = _edl_seconds(timeline_end, f"clip {index} timeline end")
        if source_end_value <= source_start_value:
            raise TimelineInterchangeError(f"clip {index} source end must be after source start")
        if timeline_end_value <= timeline_start_value:
            raise TimelineInterchangeError(f"clip {index} timeline end must be after timeline start")
        source_reference = clip.get("source", clip.get("source_id", "UNKNOWN"))
        source_reference = source_paths.get(str(source_reference), source_reference)
        lines.extend(
            [
                f"{index:03d}  AX       V     C        {_timecode(source_start_value, fps)} {_timecode(source_end_value, fps)} {_timecode(timeline_start_value, fps)} {_timecode(timeline_end_value, fps)}",
                f"* SOURCE FILE: {source_reference}",
            ]
        )
    for warning in _clip_warnings(project):
        lines.append(f"* WARNING: {warning}")
    path = Path(destination)
    content = "\n".join(lines) + "\n"
    if path.exists() and not overwrite:
        raise TimelineInterchangeError(f"destination already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists() and not overwrite:
            raise TimelineInterchangeError(f"destination already exists: {path}")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return path


def export_warnings(project: Mapping[str, Any]) -> list[str]:
    """Return EDL compatibility warnings before the user confirms export."""

    return _clip_warnings(project)
