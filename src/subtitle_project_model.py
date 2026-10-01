"""字幕プロジェクト全体のモデル。ファイル操作・組版・メディア処理には依存しない。"""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .data_boundary import coerce_int, is_object_mapping, is_object_iterable
from .short_video_schema import ShortVideo
from .subtitle_project_schema import (
    AudioMix,
    SpeakerInfo,
    WaveformInfo,
    SubtitleSegment,
    SubtitleProjectError,
    _finite_number,
)
from .video_timeline import VideoTimeline, VideoTimelineError
from .video_sequence import VideoSequence, VideoSequenceError

PROJECT_SCHEMA_VERSION = 1
PROJECT_TYPE = "subtitle-edit-project"


@dataclass(frozen=True)
class SubtitleProject:
    schema_version: int
    project_type: str
    created_at: str
    updated_at: str
    video: dict[object, object]
    output_dir: str
    audio_sources: list[SpeakerInfo]
    speakers: list[SpeakerInfo]
    waveforms: list[WaveformInfo]
    subtitle_settings: dict[object, object] | None
    render_settings: dict[object, object] | None
    transcription: dict[object, object] | None
    transcription_context: dict[object, object] | None
    audio_mix: AudioMix | None
    segments: list[SubtitleSegment]
    timeline: VideoTimeline
    sequence: VideoSequence = field(default_factory=VideoSequence)
    short_video: ShortVideo = field(default_factory=ShortVideo)
    extras: dict[object, object] = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: object) -> "SubtitleProject":
        migrated = migrate_project_payload(payload)
        video = migrated.get("video", {})
        if not isinstance(video, dict) or not is_object_mapping(video):
            raise SubtitleProjectError("video must be an object")
        segments = [
            SubtitleSegment.from_json(segment, index=index)
            for index, segment in enumerate(_project_items(migrated.get("segments", []), "segments"))
            if isinstance(segment, dict) and is_object_mapping(segment)
        ]
        video_duration = max(
            0.0,
            _finite_number(
                video.get("duration_seconds", 0.0) or 0.0,
                "video.duration_seconds",
            ),
        )
        try:
            timeline = VideoTimeline.from_json(
                migrated.get("timeline"),
                source_duration=video_duration,
            )
        except VideoTimelineError as error:
            raise SubtitleProjectError(str(error)) from error
        try:
            sequence = VideoSequence.from_json(
                migrated.get("sequence"),
                legacy_video=video,
            )
            if sequence.is_legacy_single_video():
                sequence = sequence.sync_legacy_video(video)
        except VideoSequenceError as error:
            raise SubtitleProjectError(str(error)) from error
        return cls(
            schema_version=coerce_int(migrated.get("schema_version", PROJECT_SCHEMA_VERSION)),
            project_type=str(migrated.get("project_type", PROJECT_TYPE)),
            created_at=str(migrated.get("created_at", utc_timestamp())),
            updated_at=str(migrated.get("updated_at", utc_timestamp())),
            video=deepcopy(dict(video)),
            output_dir=str(migrated.get("output_dir", "")),
            audio_sources=[
                SpeakerInfo.from_json(source)
                for source in _project_items(migrated.get("audio_sources", []), "audio_sources")
                if isinstance(source, dict) and is_object_mapping(source)
            ],
            speakers=[
                SpeakerInfo.from_json(speaker)
                for speaker in _project_items(migrated.get("speakers", []), "speakers")
                if isinstance(speaker, dict) and is_object_mapping(speaker)
            ],
            waveforms=[
                WaveformInfo.from_json(waveform)
                for waveform in _project_items(migrated.get("waveforms", []), "waveforms")
                if isinstance(waveform, dict) and is_object_mapping(waveform)
            ],
            subtitle_settings=_copy_section(migrated.get("subtitle_settings", {}), "subtitle_settings"),
            render_settings=_copy_section(migrated.get("render_settings", {}), "render_settings"),
            transcription=_copy_section(migrated.get("transcription", {}), "transcription"),
            transcription_context=_copy_section(migrated.get("transcription_context", {}), "transcription_context"),
            audio_mix=AudioMix.from_json(migrated["audio_mix"])
            if isinstance(migrated.get("audio_mix"), dict)
            else None,
            segments=segments,
            timeline=timeline,
            sequence=sequence,
            short_video=ShortVideo.from_json(migrated.get("short_video")),
            extras=deepcopy(
                {
                    key: value
                    for key, value in migrated.items()
                    if key
                    not in {
                        "schema_version",
                        "project_type",
                        "created_at",
                        "updated_at",
                        "video",
                        "output_dir",
                        "audio_sources",
                        "speakers",
                        "waveforms",
                        "subtitle_settings",
                        "render_settings",
                        "transcription",
                        "transcription_context",
                        "audio_mix",
                        "segments",
                        "timeline",
                        "sequence",
                        "short_video",
                    }
                }
            ),
        )

    def to_json(self) -> dict[object, object]:
        payload: dict[object, object] = {
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
            "timeline": self.timeline.to_json(),
            "sequence": self.sequence.to_json(),
        }
        if self.audio_mix is not None:
            payload["audio_mix"] = self.audio_mix.to_json()
        payload["short_video"] = self.short_video.to_json()
        payload.update(deepcopy(self.extras))
        return payload


def migrate_project_payload(payload: object) -> dict[object, object]:
    if not is_object_mapping(payload):
        raise SubtitleProjectError("project root must be an object")
    migrated = deepcopy(dict(payload))
    schema_version = coerce_int(migrated.get("schema_version", PROJECT_SCHEMA_VERSION) or PROJECT_SCHEMA_VERSION)
    if schema_version > PROJECT_SCHEMA_VERSION:
        raise SubtitleProjectError(f"unsupported project schema_version: {migrated.get('schema_version')!r}")

    if "project_type" not in migrated:
        migrated["project_type"] = PROJECT_TYPE
    if schema_version < PROJECT_SCHEMA_VERSION:
        migrated["schema_version"] = PROJECT_SCHEMA_VERSION

    return migrated


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _project_items(value: object, field: str) -> Iterable[object]:
    if not is_object_iterable(value):
        raise SubtitleProjectError(f"{field} must be iterable")
    return value


def _copy_section(value: object, field: str) -> dict[object, object] | None:
    """nullと未知の設定キーを保持し、設定の意味の検証は利用側に委ねる。"""
    if value is None:
        return None
    if not is_object_mapping(value):
        raise SubtitleProjectError(f"{field} must be an object or null")
    return deepcopy(dict(value))
