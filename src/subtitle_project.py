from __future__ import annotations

import heapq
import json
import math
import unicodedata
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np


PROJECT_SCHEMA_VERSION = 1
PROJECT_TYPE = "subtitle-edit-project"
MIN_SEGMENT_DURATION_SECONDS = 0.05
DEFAULT_WAVEFORM_BINS = 720
DEFAULT_WAVEFORM_SAMPLE_RATE = 400


class SubtitleProjectError(ValueError):
    """Raised when an editable subtitle project is malformed."""


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def derive_project_path(video_path: str | Path, output_dir: str | Path) -> Path:
    return Path(output_dir) / f"{Path(video_path).stem}.subtitle-project.json"


def derive_ass_path(project_path: str | Path) -> Path:
    path = Path(project_path)
    suffix = ".subtitle-project.json"
    name = path.name[:-len(suffix)] if path.name.endswith(suffix) else path.stem
    return path.with_name(f"{name}.edited.ass")


def derive_render_path(project_path: str | Path) -> Path:
    path = Path(project_path)
    suffix = ".subtitle-project.json"
    name = path.name[:-len(suffix)] if path.name.endswith(suffix) else path.stem
    return path.with_name(f"{name}.edited.subtitled.mp4")


def _finite_number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise SubtitleProjectError(f"{field} must be a number") from error
    if not math.isfinite(result):
        raise SubtitleProjectError(f"{field} must be finite")
    return result


def normalize_segment(segment: dict[str, Any], index: int) -> dict[str, Any]:
    start = max(0.0, _finite_number(segment.get("start", 0.0), "segment.start"))
    end = _finite_number(segment.get("end", start + MIN_SEGMENT_DURATION_SECONDS), "segment.end")
    if end < start + MIN_SEGMENT_DURATION_SECONDS:
        end = start + MIN_SEGMENT_DURATION_SECONDS
    text = str(segment.get("text", "")).strip()
    speaker = str(segment.get("speaker", "Oz")).strip() or "Oz"
    font_scale = max(0.1, min(4.0, _finite_number(segment.get("subtitle_font_scale", 1.0), "segment.subtitle_font_scale")))
    font_family = "".join(
        char
        for char in str(segment.get("subtitle_font_family", "")).strip()
        if char >= " " and char != "\x7f"
    )[:256]
    normalized = deepcopy(segment)
    normalized.update(
        {
            "id": str(segment.get("id") or f"subtitle-{index + 1:06d}"),
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
            "speaker": speaker,
            "emphasis": str(segment.get("emphasis", "normal")),
            "position": str(segment.get("position", "bottom")),
            "layout_row": max(0, int(segment.get("layout_row", 0))),
            "max_width": max(4, int(segment.get("max_width", 24))),
            "subtitle_font_scale": round(font_scale, 4),
            "subtitle_font_family": font_family,
            "subtitle_volume_level": float(segment.get("subtitle_volume_level", 0.0)),
            "layout_packed": True,
            "manual_text": bool(segment.get("manual_text", False)),
            "manual_timing": bool(segment.get("manual_timing", False)),
            "manual_speaker": bool(segment.get("manual_speaker", False)),
            "manual_font_scale": bool(segment.get("manual_font_scale", False)),
            "manual_font_family": bool(segment.get("manual_font_family", False)),
        }
    )
    return normalized


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in {"F", "W", "A"} else 1 for char in text)


def assign_project_layout_rows(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reflow edited overlaps without dropping captions when more than three rows are needed."""
    row_is_free: list[bool] = []
    release_queue: list[tuple[float, int]] = []
    free_rows: list[int] = []
    free_pairs: list[int] = []

    def release_finished(start: float) -> None:
        while release_queue and release_queue[0][0] <= start:
            _, row = heapq.heappop(release_queue)
            row_is_free[row] = True
            heapq.heappush(free_rows, row)
            if row > 0 and row_is_free[row - 1]:
                heapq.heappush(free_pairs, row - 1)
            if row + 1 < len(row_is_free) and row_is_free[row + 1]:
                heapq.heappush(free_pairs, row)

    def take_free_row() -> int:
        while free_rows and not row_is_free[free_rows[0]]:
            heapq.heappop(free_rows)
        if free_rows:
            return heapq.heappop(free_rows)
        row_is_free.append(False)
        return len(row_is_free) - 1

    def take_free_pair() -> int:
        while free_pairs:
            row = free_pairs[0]
            if row + 1 < len(row_is_free) and row_is_free[row] and row_is_free[row + 1]:
                break
            heapq.heappop(free_pairs)
        existing_pair = free_pairs[0] if free_pairs else None
        trailing_pair = len(row_is_free) - 1 if row_is_free and row_is_free[-1] else len(row_is_free)
        if existing_pair is not None and existing_pair <= trailing_pair:
            return heapq.heappop(free_pairs)
        if trailing_pair == len(row_is_free):
            row_is_free.extend((False, False))
        else:
            row_is_free.append(False)
        return trailing_pair

    for segment in sorted(segments, key=lambda item: (item["start"], item["end"], item["id"])):
        span = 2 if _display_width(str(segment.get("text", ""))) > int(segment.get("max_width", 24)) else 1
        start = float(segment["start"])
        release_finished(start)
        base_row = take_free_pair() if span == 2 else take_free_row()
        segment["layout_row"] = base_row
        segment["layout_row_span"] = span
        for row in range(base_row, base_row + span):
            row_is_free[row] = False
            heapq.heappush(release_queue, (float(segment["end"]), row))
    return segments


def validate_project(project: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(project, dict):
        raise SubtitleProjectError("project root must be an object")
    if int(project.get("schema_version", 0)) != PROJECT_SCHEMA_VERSION:
        raise SubtitleProjectError(
            f"unsupported project schema_version: {project.get('schema_version')!r}"
        )
    if project.get("project_type") != PROJECT_TYPE:
        raise SubtitleProjectError("not a subtitle edit project")
    video = project.get("video")
    if not isinstance(video, dict) or not str(video.get("path", "")).strip():
        raise SubtitleProjectError("video.path is required")
    segments = project.get("segments")
    if not isinstance(segments, list):
        raise SubtitleProjectError("segments must be an array")
    normalized = [normalize_segment(segment, index) for index, segment in enumerate(segments)]
    ids = [segment["id"] for segment in normalized]
    if len(ids) != len(set(ids)):
        raise SubtitleProjectError("segment ids must be unique")
    project["segments"] = assign_project_layout_rows(
        sorted(normalized, key=lambda item: (item["start"], item["end"], item["id"]))
    )
    project.setdefault("audio_sources", [])
    project.setdefault("speakers", [])
    project.setdefault("waveforms", [])
    project.setdefault("subtitle_settings", {})
    project.setdefault("render_settings", {})
    project.setdefault("transcription", {})
    project.setdefault("created_at", utc_timestamp())
    project.setdefault("updated_at", project["created_at"])
    return project


def create_project(
    *,
    video_path: str | Path,
    output_dir: str | Path,
    segments: Iterable[dict[str, Any]],
    audio_sources: Iterable[dict[str, Any]] = (),
    speakers: Iterable[dict[str, Any]] = (),
    waveforms: Iterable[dict[str, Any]] = (),
    subtitle_settings: dict[str, Any] | None = None,
    render_settings: dict[str, Any] | None = None,
    transcription: dict[str, Any] | None = None,
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    now = utc_timestamp()
    project = {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "project_type": PROJECT_TYPE,
        "created_at": now,
        "updated_at": now,
        "video": {
            "path": str(Path(video_path).resolve()),
            "duration_seconds": max(0.0, float(duration_seconds or 0.0)),
        },
        "output_dir": str(Path(output_dir).resolve()),
        "audio_sources": [deepcopy(source) for source in audio_sources],
        "speakers": [deepcopy(speaker) for speaker in speakers],
        "waveforms": [deepcopy(waveform) for waveform in waveforms],
        "subtitle_settings": deepcopy(subtitle_settings or {}),
        "render_settings": deepcopy(render_settings or {}),
        "transcription": deepcopy(transcription or {}),
        "segments": [deepcopy(segment) for segment in segments],
    }
    return validate_project(project)


def project_from_transcript(
    transcript_path: str | Path,
    *,
    video_path: str | Path,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    transcript = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
    if not isinstance(transcript, dict) or not isinstance(transcript.get("segments"), list):
        raise SubtitleProjectError("transcript JSON must contain a segments array")
    return create_project(
        video_path=video_path,
        output_dir=output_dir or Path(transcript_path).parent,
        segments=transcript["segments"],
        transcription={"imported_transcript": str(Path(transcript_path).resolve())},
    )


def load_project(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_project(payload)


def save_project(
    path: str | Path,
    project: dict[str, Any],
    *,
    project_is_validated: bool = False,
    update_project: bool = True,
) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = deepcopy(project) if update_project else project
    if not project_is_validated:
        payload = validate_project(payload)
    payload["updated_at"] = utc_timestamp()
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    if update_project:
        project.clear()
        project.update(payload)
    return output


def project_to_transcript(
    project: dict[str, Any],
    *,
    project_is_validated: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    source = project if project_is_validated else validate_project(deepcopy(project))
    return {"segments": [deepcopy(segment) for segment in source["segments"] if segment["text"]]}


def waveform_peaks_from_samples(samples: np.ndarray, bins: int = DEFAULT_WAVEFORM_BINS) -> list[float]:
    if samples.size == 0 or bins <= 0:
        return []
    absolute = np.abs(samples.astype(np.float32, copy=False))
    bin_count = min(int(bins), int(absolute.size))
    chunks = np.array_split(absolute, bin_count)
    peaks = np.asarray([float(np.percentile(chunk, 92)) if chunk.size else 0.0 for chunk in chunks])
    reference = float(np.percentile(peaks, 95)) if peaks.size else 0.0
    if reference <= 1e-7:
        return [0.0] * bin_count
    return [round(float(np.clip(value / reference, 0.0, 1.0)), 4) for value in peaks]


def build_waveform(
    audio_path: str | Path,
    *,
    speaker: str,
    style: str,
    color: str,
    offset_seconds: float,
    samples: np.ndarray,
    sample_rate: int = DEFAULT_WAVEFORM_SAMPLE_RATE,
    bins: int = DEFAULT_WAVEFORM_BINS,
) -> dict[str, Any]:
    return {
        "speaker": speaker,
        "style": style,
        "color": color,
        "source_path": str(Path(audio_path).resolve()),
        "offset_seconds": round(float(offset_seconds), 3),
        "duration_seconds": round(float(samples.size) / max(1, sample_rate), 3),
        "sample_rate": sample_rate,
        "peaks": waveform_peaks_from_samples(samples, bins=bins),
    }
