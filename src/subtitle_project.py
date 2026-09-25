from __future__ import annotations

import heapq
import json
import subprocess
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from .audio_mixer import reconcile_audio_mix
from .ass_template import DEFAULT_SUBTITLE_OUTLINE_COLOR, DEFAULT_SUBTITLE_OUTLINE_THICKNESS
from .color_config import normalize_rgb_color
from .subtitle_line_count import format_segment_text, normalize_subtitle_line_count
from .transcription_context import TranscriptionContextError, normalize_transcription_context
from .media_probe import probe_media_duration
from .subtitle_project_model import (
    PROJECT_SCHEMA_VERSION as PROJECT_SCHEMA_VERSION,
    PROJECT_TYPE as PROJECT_TYPE,
    SubtitleProject as SubtitleProject,
    migrate_project_payload as migrate_project_payload,
    utc_timestamp as utc_timestamp,
)
from .subtitle_project_schema import (
    AudioMix as AudioMix,
    AudioMixChannel as AudioMixChannel,
    SpeakerInfo as SpeakerInfo,
    SubtitleSegment as SubtitleSegment,
    WaveformInfo as WaveformInfo,
    SubtitleProjectError as SubtitleProjectError,
    MIN_SEGMENT_DURATION_SECONDS as MIN_SEGMENT_DURATION_SECONDS,
    DEFAULT_WAVEFORM_SAMPLE_RATE as DEFAULT_WAVEFORM_SAMPLE_RATE,
    normalize_segment as normalize_segment,
    _finite_number as _finite_number,
    _subtitle_line_count as _subtitle_line_count,
)


DEFAULT_WAVEFORM_BINS = 720


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


def derive_short_render_path(project_path: str | Path) -> Path:
    path = Path(project_path)
    suffix = ".subtitle-project.json"
    name = path.name[:-len(suffix)] if path.name.endswith(suffix) else path.stem
    return path.with_name(f"{name}.short.mp4")


def project_work_directory(project_path: str | Path) -> Path:
    """Keep transcription intermediates with the project, apart from exports."""
    path = Path(project_path)
    name = path.name.removesuffix(".subtitle-project.json")
    return path.with_name(f".{name}.work")


def resolve_render_output_path(
    project_path: str | Path,
    project: Mapping[str, Any],
    output_path: str | Path | None = None,
    *,
    short: bool = False,
) -> Path:
    if output_path:
        return Path(output_path)
    output_dir = str(project.get("output_dir", ""))
    if not output_dir:
        raise ValueError("完成動画の出力先フォルダを選択してください")
    name = derive_short_render_path(project_path) if short else derive_render_path(project_path)
    return Path(output_dir) / name.name


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in {"F", "W", "A"} else 1 for char in text)


def _layout_row_span(segment: dict[str, Any]) -> int:
    formatted = format_segment_text(segment)
    span = max(1, formatted.count(r"\N") + 1)
    subtitle_line_count = normalize_subtitle_line_count(segment.get("subtitle_line_count", "auto"))
    if subtitle_line_count == "2":
        return max(2, span)
    return span


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

    def take_free_span(span: int) -> int:
        if span <= 1:
            return take_free_row()
        if span == 2:
            return take_free_pair()

        run_start = -1
        run_length = 0
        for row, is_free in enumerate(row_is_free):
            if is_free:
                if run_length == 0:
                    run_start = row
                run_length += 1
                if run_length >= span:
                    return run_start
            else:
                run_start = -1
                run_length = 0

        if run_length > 0:
            row_is_free.extend([False] * (span - run_length))
            return run_start
        base_row = len(row_is_free)
        row_is_free.extend([False] * span)
        return base_row

    for segment in sorted(segments, key=lambda item: (item["start"], item["end"], item["id"])):
        span = _layout_row_span(segment)
        start = float(segment["start"])
        release_finished(start)
        base_row = take_free_span(span)
        segment["layout_row"] = base_row
        segment["layout_row_span"] = span
        for row in range(base_row, base_row + span):
            row_is_free[row] = False
            heapq.heappush(release_queue, (float(segment["end"]), row))
    return segments


def validate_project(project: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(project, dict):
        raise SubtitleProjectError("project root must be an object")
    migrated = migrate_project_payload(project)
    model = SubtitleProject.from_json(migrated)
    project = model.to_json()
    if project.get("schema_version") != PROJECT_SCHEMA_VERSION:
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
    typed_segments = [SubtitleSegment.from_json(segment, index=index) for index, segment in enumerate(segments)]
    normalized = [segment.to_json() for segment in typed_segments]
    ids = [segment["id"] for segment in normalized]
    if len(ids) != len(set(ids)):
        raise SubtitleProjectError("segment ids must be unique")
    project["segments"] = assign_project_layout_rows(
        sorted(normalized, key=lambda item: (item["start"], item["end"], item["id"]))
    )
    project.setdefault("audio_sources", [])
    project.setdefault("speakers", [])
    project.setdefault("waveforms", [])
    subtitle_settings = project.setdefault("subtitle_settings", {})
    if not isinstance(subtitle_settings, dict):
        raise SubtitleProjectError("subtitle_settings must be an object")
    try:
        subtitle_settings["outline_color"] = normalize_rgb_color(
            subtitle_settings.get("outline_color", DEFAULT_SUBTITLE_OUTLINE_COLOR)
        )
        outline_thickness = int(
            subtitle_settings.get("outline_thickness", DEFAULT_SUBTITLE_OUTLINE_THICKNESS)
        )
    except (TypeError, ValueError, OverflowError) as error:
        raise SubtitleProjectError(f"invalid subtitle outline setting: {error}") from error
    if not 0 <= outline_thickness <= 20:
        raise SubtitleProjectError("subtitle outline thickness must be between 0 and 20")
    subtitle_settings["outline_thickness"] = outline_thickness
    project.setdefault("render_settings", {})
    project.setdefault("transcription", {})
    try:
        project["transcription_context"] = normalize_transcription_context(project.get("transcription_context"))
    except TranscriptionContextError as error:
        raise SubtitleProjectError(str(error)) from error
    reconcile_audio_mix(project)
    project.setdefault("created_at", utc_timestamp())
    project.setdefault("updated_at", project["created_at"])
    return project


def create_project(
    *,
    video_path: str | Path,
    output_dir: str | Path = "",
    segments: Iterable[dict[str, Any]],
    audio_sources: Iterable[dict[str, Any]] = (),
    speakers: Iterable[dict[str, Any]] = (),
    waveforms: Iterable[dict[str, Any]] = (),
    subtitle_settings: dict[str, Any] | None = None,
    render_settings: dict[str, Any] | None = None,
    transcription: dict[str, Any] | None = None,
    transcription_context: dict[str, Any] | None = None,
    audio_mix: dict[str, Any] | None = None,
    timeline: dict[str, Any] | None = None,
    sequence: dict[str, Any] | None = None,
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
        "output_dir": str(Path(output_dir).resolve()) if output_dir else "",
        "audio_sources": [deepcopy(source) for source in audio_sources],
        "speakers": [deepcopy(speaker) for speaker in speakers],
        "waveforms": [deepcopy(waveform) for waveform in waveforms],
        "subtitle_settings": deepcopy(subtitle_settings or {}),
        "render_settings": deepcopy(render_settings or {}),
        "transcription": deepcopy(transcription or {}),
        "transcription_context": deepcopy(transcription_context or {}),
        "audio_mix": deepcopy(audio_mix or {}),
        "segments": [deepcopy(segment) for segment in segments],
        "timeline": deepcopy(timeline) if timeline is not None else None,
        "sequence": deepcopy(sequence) if sequence is not None else None,
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


def load_project(path: str | Path, *, resolve_video_duration: bool = False) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if resolve_video_duration and isinstance(payload, dict):
        video = payload.get("video", {})
        if isinstance(video, dict) and not video.get("duration_seconds"):
            video_path = str(video.get("path", ""))
            if Path(video_path).is_file():
                try:
                    video["duration_seconds"] = probe_media_duration(video_path)
                except (OSError, ValueError, subprocess.CalledProcessError):
                    # Missing duration is safe for subtitle editing, but timeline
                    # validation must refuse cuts rather than guess from captions.
                    pass
    return validate_project(payload)


def load_project_model(path: str | Path) -> SubtitleProject:
    """Load a validated project as the canonical internal domain model."""
    return SubtitleProject.from_json(load_project(path))


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


def save_project_model(
    path: str | Path,
    project: SubtitleProject,
) -> Path:
    """Serialize a domain model only at the persistence boundary."""
    return save_project(path, project.to_json())


def project_to_view_payload(project: SubtitleProject | dict[str, Any]) -> dict[str, Any]:
    """Build a GUI-specific payload without exposing persistence models to QML."""
    model = project if isinstance(project, SubtitleProject) else SubtitleProject.from_json(validate_project(project))
    return {
        "video": deepcopy(model.video),
        "output_dir": model.output_dir,
        "speakers": [
            {
                "name": speaker.name,
                "style": speaker.style,
                "track_key": speaker.track_key,
                "color": speaker.color,
            }
            for speaker in model.speakers
        ],
        "segments": [
            {
                "id": segment.id,
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
                "speaker": segment.speaker,
                "layout_row": segment.layout_row,
                "layout_row_span": segment.layout_row_span,
                "subtitle_font_scale": segment.subtitle_font_scale,
                "subtitle_font_family": segment.subtitle_font_family,
                "subtitle_line_count": segment.subtitle_line_count,
            }
            for segment in model.segments
        ],
        "subtitle_settings": deepcopy(model.subtitle_settings),
        "render_settings": deepcopy(model.render_settings),
        "timeline": model.timeline.as_view(),
    }


def project_to_transcript(
    project: SubtitleProject | dict[str, Any],
    *,
    project_is_validated: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    if isinstance(project, SubtitleProject):
        model = project
    else:
        payload = project if project_is_validated else validate_project(deepcopy(project))
        model = SubtitleProject.from_json(payload)
    return {
        "segments": [segment.to_json() for segment in model.segments if segment.text]
    }


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
