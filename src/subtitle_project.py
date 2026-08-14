from __future__ import annotations

import heapq
import json
import math
import unicodedata
from dataclasses import dataclass, field
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .audio_mixer import reconcile_audio_mix
from .ass_template import DEFAULT_SUBTITLE_OUTLINE_COLOR, DEFAULT_SUBTITLE_OUTLINE_THICKNESS
from .color_config import normalize_rgb_color
from .subtitle_line_count import normalize_subtitle_line_count
from .transcription_context import TranscriptionContextError, normalize_transcription_context


PROJECT_SCHEMA_VERSION = 1
PROJECT_TYPE = "subtitle-edit-project"
MIN_SEGMENT_DURATION_SECONDS = 0.05
DEFAULT_WAVEFORM_BINS = 720
DEFAULT_WAVEFORM_SAMPLE_RATE = 400


@dataclass(frozen=True)
class SubtitleSegment:
    id: str
    start: float
    end: float
    text: str
    speaker: str
    emphasis: str
    position: str
    layout_row: int
    layout_row_span: int
    max_width: int
    subtitle_line_count: str
    subtitle_font_scale: float
    subtitle_font_family: str
    subtitle_volume_level: float
    layout_packed: bool
    manual_text: bool
    manual_timing: bool
    manual_speaker: bool
    manual_line_count: bool
    manual_font_scale: bool
    manual_font_family: bool
    source_speaker: str = ""
    source_track: str = ""
    source_file: str = ""
    words: list[dict[str, Any]] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: dict[str, Any], index: int = 0) -> "SubtitleSegment":
        normalized = normalize_segment(payload, index)
        return cls(
            id=str(normalized["id"]),
            start=float(normalized["start"]),
            end=float(normalized["end"]),
            text=str(normalized["text"]),
            speaker=str(normalized["speaker"]),
            emphasis=str(normalized["emphasis"]),
            position=str(normalized["position"]),
            layout_row=int(normalized["layout_row"]),
            layout_row_span=max(1, int(normalized.get("layout_row_span", 1))),
            max_width=max(4, int(normalized["max_width"])),
            subtitle_line_count=str(normalized["subtitle_line_count"]),
            subtitle_font_scale=float(normalized["subtitle_font_scale"]),
            subtitle_font_family=str(normalized["subtitle_font_family"]),
            subtitle_volume_level=float(normalized["subtitle_volume_level"]),
            layout_packed=bool(normalized["layout_packed"]),
            manual_text=bool(normalized["manual_text"]),
            manual_timing=bool(normalized["manual_timing"]),
            manual_speaker=bool(normalized["manual_speaker"]),
            manual_line_count=bool(normalized["manual_line_count"]),
            manual_font_scale=bool(normalized["manual_font_scale"]),
            manual_font_family=bool(normalized["manual_font_family"]),
            source_speaker=str(normalized.get("source_speaker", "")),
            source_track=str(normalized.get("source_track", "")),
            source_file=str(normalized.get("source_file", "")),
            words=deepcopy(normalized.get("words", [])),
            extras=deepcopy({
                key: value
                for key, value in normalized.items()
                if key not in {
                    "id", "start", "end", "text", "speaker", "emphasis", "position",
                    "layout_row", "layout_row_span", "max_width", "subtitle_line_count",
                    "subtitle_font_scale", "subtitle_font_family", "subtitle_volume_level",
                    "layout_packed", "manual_text", "manual_timing", "manual_speaker",
                    "manual_line_count", "manual_font_scale", "manual_font_family",
                    "source_speaker", "source_track", "source_file", "words",
                }
            }),
        )

    def to_json(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "speaker": self.speaker,
            "emphasis": self.emphasis,
            "position": self.position,
            "layout_row": self.layout_row,
            "layout_row_span": self.layout_row_span,
            "max_width": self.max_width,
            "subtitle_line_count": self.subtitle_line_count,
            "subtitle_font_scale": self.subtitle_font_scale,
            "subtitle_font_family": self.subtitle_font_family,
            "subtitle_volume_level": self.subtitle_volume_level,
            "layout_packed": self.layout_packed,
            "manual_text": self.manual_text,
            "manual_timing": self.manual_timing,
            "manual_speaker": self.manual_speaker,
            "manual_line_count": self.manual_line_count,
            "manual_font_scale": self.manual_font_scale,
            "manual_font_family": self.manual_font_family,
            "words": list(self.words),
            "source_speaker": self.source_speaker,
            "source_track": self.source_track,
            "source_file": self.source_file,
        }
        payload.update(deepcopy(self.extras))
        return payload


@dataclass(frozen=True)
class SpeakerInfo:
    name: str
    style: str
    track_key: str
    file_name: str
    path: str
    color: str = "#7FD957"
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "SpeakerInfo":
        return cls(
            name=str(payload.get("name", "Oz")),
            style=str(payload.get("style", payload.get("speaker", "Oz"))),
            track_key=str(payload.get("track_key", "")),
            file_name=str(payload.get("file_name", "")),
            path=str(payload.get("path", "")),
            color=str(payload.get("color", "#7FD957")),
            extras=deepcopy({key: value for key, value in payload.items() if key not in {
                "name", "style", "track_key", "file_name", "path", "color",
            }}),
        )

    def to_json(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "style": self.style,
            "track_key": self.track_key,
            "file_name": self.file_name,
            "path": self.path,
            "color": self.color,
        }
        payload.update(deepcopy(self.extras))
        return payload


@dataclass(frozen=True)
class WaveformInfo:
    speaker: str
    style: str
    color: str
    source_path: str
    offset_seconds: float
    duration_seconds: float
    sample_rate: int
    peaks: list[float]
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "WaveformInfo":
        return cls(
            speaker=str(payload.get("speaker", "")),
            style=str(payload.get("style", "")),
            color=str(payload.get("color", "#7FD957")),
            source_path=str(payload.get("source_path", "")),
            offset_seconds=float(payload.get("offset_seconds", 0.0)),
            duration_seconds=float(payload.get("duration_seconds", 0.0)),
            sample_rate=int(payload.get("sample_rate", DEFAULT_WAVEFORM_SAMPLE_RATE)),
            peaks=list(deepcopy(payload.get("peaks", []))),
            extras=deepcopy({key: value for key, value in payload.items() if key not in {
                "speaker", "style", "color", "source_path", "offset_seconds",
                "duration_seconds", "sample_rate", "peaks",
            }}),
        )

    def to_json(self) -> dict[str, Any]:
        payload = {
            "speaker": self.speaker,
            "style": self.style,
            "color": self.color,
            "source_path": self.source_path,
            "offset_seconds": self.offset_seconds,
            "duration_seconds": self.duration_seconds,
            "sample_rate": self.sample_rate,
            "peaks": list(self.peaks),
        }
        payload.update(deepcopy(self.extras))
        return payload


@dataclass(frozen=True)
class AudioMixChannel:
    id: str
    kind: str
    label: str
    enabled: bool
    muted: bool
    solo: bool
    volume_percent: float
    selector: str | None = None
    path: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "AudioMixChannel":
        return cls(
            id=str(payload.get("id", "")),
            kind=str(payload.get("kind", "external")),
            label=str(payload.get("label", "")),
            enabled=bool(payload.get("enabled", False)),
            muted=bool(payload.get("muted", False)),
            solo=bool(payload.get("solo", False)),
            volume_percent=float(payload.get("volume_percent", 100.0)),
            selector=str(payload.get("selector")) if payload.get("selector") is not None else None,
            path=str(payload.get("path")) if payload.get("path") is not None else None,
            extras={},
        )

    def to_json(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "enabled": self.enabled,
            "muted": self.muted,
            "solo": self.solo,
            "volume_percent": self.volume_percent,
        }
        if self.selector is not None:
            payload["selector"] = self.selector
        if self.path is not None:
            payload["path"] = self.path
        payload.update(deepcopy(self.extras))
        return payload


@dataclass(frozen=True)
class AudioMix:
    version: int
    customized: bool
    channels: list[AudioMixChannel]
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "AudioMix":
        channels = [
            AudioMixChannel.from_json(channel)
            for channel in payload.get("channels", [])
            if isinstance(channel, dict)
        ]
        return cls(
            version=int(payload.get("version", 1)),
            customized=bool(payload.get("customized", False)),
            channels=channels,
            extras={},
        )

    def to_json(self) -> dict[str, Any]:
        payload = {
            "version": self.version,
            "customized": self.customized,
            "channels": [channel.to_json() for channel in self.channels],
        }
        payload.update(deepcopy(self.extras))
        return payload


@dataclass(frozen=True)
class SubtitleProject:
    schema_version: int
    project_type: str
    created_at: str
    updated_at: str
    video: dict[str, Any]
    output_dir: str
    audio_sources: list[SpeakerInfo]
    speakers: list[SpeakerInfo]
    waveforms: list[WaveformInfo]
    subtitle_settings: dict[str, Any]
    render_settings: dict[str, Any]
    transcription: dict[str, Any]
    transcription_context: dict[str, Any]
    audio_mix: AudioMix | None
    segments: list[SubtitleSegment]
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "SubtitleProject":
        video = payload.get("video", {})
        if not isinstance(video, dict):
            raise SubtitleProjectError("video must be an object")
        migrated = migrate_project_payload(payload)
        segments = [
            SubtitleSegment.from_json(segment, index=index)
            for index, segment in enumerate(migrated.get("segments", []))
            if isinstance(segment, dict)
        ]
        return cls(
            schema_version=int(migrated.get("schema_version", PROJECT_SCHEMA_VERSION)),
            project_type=str(migrated.get("project_type", PROJECT_TYPE)),
            created_at=str(migrated.get("created_at", utc_timestamp())),
            updated_at=str(migrated.get("updated_at", utc_timestamp())),
            video=deepcopy(video),
            output_dir=str(migrated.get("output_dir", "")),
            audio_sources=[SpeakerInfo.from_json(source) for source in migrated.get("audio_sources", []) if isinstance(source, dict)],
            speakers=[SpeakerInfo.from_json(speaker) for speaker in migrated.get("speakers", []) if isinstance(speaker, dict)],
            waveforms=[WaveformInfo.from_json(waveform) for waveform in migrated.get("waveforms", []) if isinstance(waveform, dict)],
            subtitle_settings=deepcopy(migrated.get("subtitle_settings", {})),
            render_settings=deepcopy(migrated.get("render_settings", {})),
            transcription=deepcopy(migrated.get("transcription", {})),
            transcription_context=deepcopy(migrated.get("transcription_context", {})),
            audio_mix=AudioMix.from_json(migrated["audio_mix"]) if isinstance(migrated.get("audio_mix"), dict) else None,
            segments=segments,
            extras=deepcopy({key: value for key, value in migrated.items() if key not in {
                "schema_version", "project_type", "created_at", "updated_at", "video", "output_dir",
                "audio_sources", "speakers", "waveforms", "subtitle_settings", "render_settings",
                "transcription", "transcription_context", "audio_mix", "segments",
            }}),
        )

    def to_json(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "project_type": self.project_type,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "video": deepcopy(self.video),
            "output_dir": self.output_dir,
            "audio_sources": [speaker.to_json() for speaker in self.audio_sources],
            "speakers": [speaker.to_json() for speaker in self.speakers],
            "waveforms": [waveform.to_json() for waveform in self.waveforms],
            "subtitle_settings": deepcopy(self.subtitle_settings),
            "render_settings": deepcopy(self.render_settings),
            "transcription": deepcopy(self.transcription),
            "transcription_context": deepcopy(self.transcription_context),
            "segments": [segment.to_json() for segment in self.segments],
        }
        if self.audio_mix is not None:
            payload["audio_mix"] = self.audio_mix.to_json()
        payload.update(deepcopy(self.extras))
        return payload


def migrate_project_payload(payload: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(payload)
    schema_version = int(migrated.get("schema_version", PROJECT_SCHEMA_VERSION) or PROJECT_SCHEMA_VERSION)
    if schema_version > PROJECT_SCHEMA_VERSION:
        raise SubtitleProjectError(f"unsupported project schema_version: {migrated.get('schema_version')!r}")

    if "project_type" not in migrated:
        migrated["project_type"] = PROJECT_TYPE
    if schema_version < PROJECT_SCHEMA_VERSION:
        migrated["schema_version"] = PROJECT_SCHEMA_VERSION

    return migrated


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


def _subtitle_line_count(value: object) -> str:
    try:
        return normalize_subtitle_line_count(value)
    except ValueError as error:
        raise SubtitleProjectError(str(error)) from error


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
    _subtitle_line_count(segment.get("subtitle_line_count", segment.get("line_count_override", "auto")))
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
            "subtitle_line_count": "auto",
            "subtitle_font_scale": round(font_scale, 4),
            "subtitle_font_family": font_family,
            "subtitle_volume_level": float(segment.get("subtitle_volume_level", 0.0)),
            "layout_packed": True,
            "manual_text": bool(segment.get("manual_text", False)),
            "manual_timing": bool(segment.get("manual_timing", False)),
            "manual_speaker": bool(segment.get("manual_speaker", False)),
            "manual_line_count": False,
            "manual_font_scale": bool(segment.get("manual_font_scale", False)),
            "manual_font_family": bool(segment.get("manual_font_family", False)),
        }
    )
    return normalized


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in {"F", "W", "A"} else 1 for char in text)


def _layout_row_span(segment: dict[str, Any]) -> int:
    text = str(segment.get("text", "")).replace("\r\n", "\n").replace("\r", "\n").replace(r"\N", "\n")
    if "\n" in text:
        return 2
    return 2 if _display_width(text) > int(segment.get("max_width", 24)) else 1


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
        span = _layout_row_span(segment)
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
    output_dir: str | Path,
    segments: Iterable[dict[str, Any]],
    audio_sources: Iterable[dict[str, Any]] = (),
    speakers: Iterable[dict[str, Any]] = (),
    waveforms: Iterable[dict[str, Any]] = (),
    subtitle_settings: dict[str, Any] | None = None,
    render_settings: dict[str, Any] | None = None,
    transcription: dict[str, Any] | None = None,
    transcription_context: dict[str, Any] | None = None,
    audio_mix: dict[str, Any] | None = None,
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
        "transcription_context": deepcopy(transcription_context or {}),
        "audio_mix": deepcopy(audio_mix or {}),
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
