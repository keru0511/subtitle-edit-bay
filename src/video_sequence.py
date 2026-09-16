"""Pure domain model for the normal video's multi-clip sequence.

The existing :mod:`video_timeline` module describes non-destructive cuts in a
single source video.  This module deliberately models a different boundary:
an ordered sequence of references to one or more video assets.  It contains
no Qt, FFmpeg, or GUI state so the render and UI slices can depend on it
without introducing a controller cycle.

Transition data belongs to the incoming clip.  For example, ``clips[1]``'s
transition describes the boundary between ``clips[0]`` and ``clips[1]``.  A
non-cut transition overlaps the two source ranges by its duration.  During an
overlap, output-to-source mapping deterministically chooses the incoming clip;
the renderer remains responsible for compositing both clips.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any, Callable
from uuid import uuid4


VIDEO_SEQUENCE_SCHEMA_VERSION = 1
MIN_SEQUENCE_CLIP_DURATION_SECONDS = 0.05
VALID_SEQUENCE_TRANSITION_TYPES = ("crossfade", "fade", "cut")
_TIME_PRECISION = 3
_TIME_EPSILON = 0.0005


class VideoSequenceError(ValueError):
    """Raised when a multi-clip sequence is malformed or unsafe to mutate."""


SequenceError = VideoSequenceError


def _finite_seconds(value: Any, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise VideoSequenceError(f"{field_name} must be a number") from error
    if not math.isfinite(result):
        raise VideoSequenceError(f"{field_name} must be finite")
    return result


def _required_id(value: Any, field_name: str) -> str:
    identifier = str(value or "").strip()
    if not identifier:
        raise VideoSequenceError(f"{field_name} is required")
    return identifier


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def _rounded(value: float) -> float:
    return round(value, _TIME_PRECISION)


def _validate_range(
    source_start: Any,
    source_end: Any,
    field_prefix: str,
    *,
    asset_duration: float = 0.0,
) -> tuple[float, float]:
    start = _finite_seconds(source_start, f"{field_prefix}.source_start")
    end = _finite_seconds(source_end, f"{field_prefix}.source_end")
    if start < 0.0:
        raise VideoSequenceError(f"{field_prefix}.source_start must be non-negative")
    if end - start < MIN_SEQUENCE_CLIP_DURATION_SECONDS - _TIME_EPSILON:
        raise VideoSequenceError(
            f"{field_prefix} must be at least {MIN_SEQUENCE_CLIP_DURATION_SECONDS:.2f} seconds"
        )
    if asset_duration > 0.0 and end > asset_duration + _TIME_EPSILON:
        raise VideoSequenceError(
            f"{field_prefix}.source_end exceeds the asset duration ({asset_duration:.3f})"
        )
    return _rounded(start), _rounded(end)


@dataclass(frozen=True)
class SequenceAsset:
    """A canonical reference to an input video asset."""

    id: str
    path: str
    duration_seconds: float = 0.0
    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        identifier = _required_id(self.id, "asset.id")
        path = str(self.path or "").strip()
        if not path:
            raise VideoSequenceError("asset.path is required")
        duration = _finite_seconds(self.duration_seconds, "asset.duration_seconds")
        if duration < 0.0:
            raise VideoSequenceError("asset.duration_seconds must be non-negative")
        object.__setattr__(self, "id", identifier)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "duration_seconds", _rounded(duration))
        object.__setattr__(self, "extras", deepcopy(self.extras))

    @classmethod
    def from_json(cls, payload: Mapping[str, Any], *, index: int = 0) -> "SequenceAsset":
        if not isinstance(payload, Mapping):
            raise VideoSequenceError(f"sequence.assets[{index}] must be an object")
        raw_duration = payload.get("duration_seconds", payload.get("duration", 0.0))
        return cls(
            id=payload.get("id"),
            path=str(payload.get("path", payload.get("file", ""))),
            duration_seconds=raw_duration,
            extras=deepcopy(
                {
                    key: value
                    for key, value in payload.items()
                    if key not in {"id", "path", "file", "duration_seconds", "duration"}
                }
            ),
        )

    def to_json(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "path": self.path,
            "duration_seconds": self.duration_seconds,
        }
        payload.update(deepcopy(self.extras))
        return payload

    @property
    def duration(self) -> float:
        return self.duration_seconds


@dataclass(frozen=True)
class SequenceTransition:
    """The transition into a clip in the ordered sequence."""

    type: str = "cut"
    duration: float = 0.0
    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        transition_type = str(self.type or "").strip().lower()
        if transition_type not in VALID_SEQUENCE_TRANSITION_TYPES:
            raise VideoSequenceError(
                f"transition.type must be one of {VALID_SEQUENCE_TRANSITION_TYPES}"
            )
        duration = _finite_seconds(self.duration, "transition.duration")
        if duration < 0.0:
            raise VideoSequenceError("transition.duration must be non-negative")
        if transition_type == "cut" and duration > _TIME_EPSILON:
            raise VideoSequenceError("cut transition duration must be zero")
        object.__setattr__(self, "type", transition_type)
        object.__setattr__(self, "duration", _rounded(duration))
        object.__setattr__(self, "extras", deepcopy(self.extras))

    @classmethod
    def from_json(
        cls,
        payload: Mapping[str, Any] | None,
        *,
        default_type: str = "cut",
    ) -> "SequenceTransition":
        if payload is None:
            return cls(type=default_type)
        if not isinstance(payload, Mapping):
            raise VideoSequenceError("clip.transition must be an object")
        duration = payload.get("duration", payload.get("duration_seconds", 0.0))
        return cls(
            type=payload.get("type", default_type),
            duration=duration,
            extras=deepcopy(
                {
                    key: value
                    for key, value in payload.items()
                    if key not in {"type", "duration", "duration_seconds"}
                }
            ),
        )

    @property
    def overlap_seconds(self) -> float:
        return 0.0 if self.type == "cut" else self.duration

    @property
    def duration_seconds(self) -> float:
        return self.duration

    def to_json(self) -> dict[str, Any]:
        payload = {"type": self.type, "duration": self.duration}
        payload.update(deepcopy(self.extras))
        return payload


@dataclass(frozen=True)
class SequenceClip:
    """A source range and its non-secret, per-clip edit state."""

    id: str
    asset_id: str
    source_start: float
    source_end: float
    transition: SequenceTransition = field(default_factory=SequenceTransition)
    audio_linked: bool = True
    volume: float = 1.0
    audio_offset_seconds: float = 0.0
    muted: bool = False
    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        identifier = _required_id(self.id, "clip.id")
        asset_id = _required_id(self.asset_id, "clip.asset_id")
        start, end = _validate_range(
            self.source_start,
            self.source_end,
            "clip",
        )
        if not isinstance(self.transition, SequenceTransition):
            raise VideoSequenceError("clip.transition must be a SequenceTransition")
        volume = _finite_seconds(self.volume, "clip.volume")
        if volume < 0.0 or volume > 2.0:
            raise VideoSequenceError("clip.volume must be between 0 and 2")
        offset = _finite_seconds(self.audio_offset_seconds, "clip.audio_offset_seconds")
        object.__setattr__(self, "id", identifier)
        object.__setattr__(self, "asset_id", asset_id)
        object.__setattr__(self, "source_start", start)
        object.__setattr__(self, "source_end", end)
        object.__setattr__(self, "volume", _rounded(volume))
        object.__setattr__(self, "audio_offset_seconds", _rounded(offset))
        object.__setattr__(self, "audio_linked", bool(self.audio_linked))
        object.__setattr__(self, "muted", bool(self.muted))
        object.__setattr__(self, "extras", deepcopy(self.extras))

    @classmethod
    def from_json(cls, payload: Mapping[str, Any], *, index: int = 0) -> "SequenceClip":
        if not isinstance(payload, Mapping):
            raise VideoSequenceError(f"sequence.clips[{index}] must be an object")
        asset_id = payload.get("asset_id", payload.get("source_asset_id"))
        offset = payload.get(
            "audio_offset_seconds",
            payload.get("audio_offset", payload.get("offset_seconds", 0.0)),
        )
        return cls(
            id=payload.get("id"),
            asset_id=asset_id,
            source_start=payload.get("source_start", payload.get("start", 0.0)),
            source_end=payload.get("source_end", payload.get("end", 0.0)),
            transition=SequenceTransition.from_json(payload.get("transition")),
            audio_linked=payload.get("audio_linked", payload.get("audioLinked", True)),
            volume=payload.get("volume", payload.get("audio_volume", 1.0)),
            audio_offset_seconds=offset,
            muted=payload.get("muted", payload.get("audio_muted", False)),
            extras=deepcopy(
                {
                    key: value
                    for key, value in payload.items()
                    if key not in {
                        "id", "asset_id", "source_asset_id", "source_start", "source_end",
                        "start", "end", "transition", "audio_linked", "audioLinked",
                        "volume", "audio_volume", "audio_offset_seconds", "audio_offset",
                        "offset_seconds", "muted", "audio_muted",
                    }
                }
            ),
        )

    @property
    def duration(self) -> float:
        return _rounded(self.source_end - self.source_start)

    @property
    def source_asset_id(self) -> str:
        return self.asset_id

    @property
    def offset_seconds(self) -> float:
        return self.audio_offset_seconds

    def to_json(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "asset_id": self.asset_id,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "transition": self.transition.to_json(),
            "audio_linked": self.audio_linked,
            "volume": self.volume,
            "audio_offset_seconds": self.audio_offset_seconds,
            "muted": self.muted,
        }
        payload.update(deepcopy(self.extras))
        return payload


@dataclass(frozen=True)
class SequencePosition:
    """The deterministic source position selected for an output position."""

    clip_id: str
    source_time: float

    def to_json(self) -> dict[str, Any]:
        return {"clip_id": self.clip_id, "source_time": self.source_time}


@dataclass(frozen=True)
class SequenceTimelineClip:
    clip: SequenceClip
    output_start: float
    overlap: float = 0.0

    @property
    def duration(self) -> float:
        return self.clip.duration

    @property
    def output_end(self) -> float:
        return _rounded(self.output_start + self.duration)

    def as_view(self) -> dict[str, Any]:
        return {
            "clipId": self.clip.id,
            "assetId": self.clip.asset_id,
            "sourceStart": self.clip.source_start,
            "sourceEnd": self.clip.source_end,
            "outputStart": self.output_start,
            "outputEnd": self.output_end,
            "duration": self.duration,
            "overlap": self.overlap,
            "transition": self.clip.transition.to_json(),
        }


@dataclass(frozen=True)
class SequenceTimeline:
    clips: tuple[SequenceTimelineClip, ...]
    total_duration: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "clips", tuple(self.clips))
        object.__setattr__(self, "total_duration", _rounded(max(0.0, self.total_duration)))

    def output_to_source_seconds(self, output_seconds: Any) -> SequencePosition:
        if not self.clips:
            raise VideoSequenceError("sequence has no clips")
        position = _finite_seconds(output_seconds, "output_seconds")
        position = min(max(0.0, position), self.total_duration)
        selected = self.clips[0]
        for entry in reversed(self.clips):
            if entry.output_start <= position:
                selected = entry
                break
        offset = min(max(0.0, position - selected.output_start), selected.duration)
        return SequencePosition(
            clip_id=selected.clip.id,
            source_time=_rounded(selected.clip.source_start + offset),
        )

    def source_to_output_seconds(self, clip_id: str, source_seconds: Any) -> float:
        identifier = _required_id(clip_id, "clip_id")
        selected = next((entry for entry in self.clips if entry.clip.id == identifier), None)
        if selected is None:
            raise VideoSequenceError(f"unknown clip: {identifier}")
        source_position = _finite_seconds(source_seconds, "source_seconds")
        source_position = min(
            max(selected.clip.source_start, source_position),
            selected.clip.source_end,
        )
        return _rounded(selected.output_start + source_position - selected.clip.source_start)

    def as_view(self) -> dict[str, Any]:
        return {
            "outputDuration": self.total_duration,
            "clips": [entry.as_view() for entry in self.clips],
        }


def build_sequence_timeline(clips: tuple[SequenceClip, ...] | list[SequenceClip]) -> SequenceTimeline:
    """Build output placement and overlap information from ordered clips."""

    entries: list[SequenceTimelineClip] = []
    total_duration = 0.0
    for index, clip in enumerate(clips):
        overlap = clip.transition.overlap_seconds if index > 0 else 0.0
        if index == 0 and clip.transition.overlap_seconds > _TIME_EPSILON:
            raise VideoSequenceError("the first clip must use a cut transition")
        if index > 0 and overlap > min(clips[index - 1].duration, clip.duration) + _TIME_EPSILON:
            raise VideoSequenceError(
                f"transition into clip {clip.id!r} exceeds adjacent clip duration"
            )
        output_start = max(0.0, total_duration - overlap)
        entry = SequenceTimelineClip(
            clip=clip,
            output_start=_rounded(output_start),
            overlap=_rounded(overlap),
        )
        entries.append(entry)
        total_duration = entry.output_end
    return SequenceTimeline(clips=tuple(entries), total_duration=total_duration)


def _validate_sequence_parts(
    assets: tuple[SequenceAsset, ...],
    clips: tuple[SequenceClip, ...],
) -> None:
    asset_ids = [asset.id for asset in assets]
    if len(asset_ids) != len(set(asset_ids)):
        raise VideoSequenceError("sequence asset ids must be unique")
    clip_ids = [clip.id for clip in clips]
    if len(clip_ids) != len(set(clip_ids)):
        raise VideoSequenceError("sequence clip ids must be unique")
    assets_by_id = {asset.id: asset for asset in assets}
    for index, clip in enumerate(clips):
        asset = assets_by_id.get(clip.asset_id)
        if asset is None:
            raise VideoSequenceError(f"clip {clip.id!r} references unknown asset: {clip.asset_id}")
        _validate_range(
            clip.source_start,
            clip.source_end,
            f"clip {clip.id!r}",
            asset_duration=asset.duration_seconds,
        )
        if index == 0 and clip.transition.overlap_seconds > _TIME_EPSILON:
            raise VideoSequenceError("the first clip must use a cut transition")
        if index > 0:
            previous_duration = clips[index - 1].duration
            maximum_overlap = min(previous_duration, clip.duration)
            if clip.transition.overlap_seconds > maximum_overlap + _TIME_EPSILON:
                raise VideoSequenceError(
                    f"transition into clip {clip.id!r} exceeds adjacent clip duration"
                )


@dataclass(frozen=True)
class VideoSequence:
    """Immutable ordered normal-video sequence and its edit operations."""

    assets: tuple[SequenceAsset, ...] = ()
    clips: tuple[SequenceClip, ...] = ()
    schema_version: int = VIDEO_SEQUENCE_SCHEMA_VERSION
    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            schema_version = int(self.schema_version)
        except (TypeError, ValueError) as error:
            raise VideoSequenceError("sequence.schema_version must be an integer") from error
        if schema_version <= 0 or schema_version > VIDEO_SEQUENCE_SCHEMA_VERSION:
            raise VideoSequenceError(f"unsupported sequence.schema_version: {self.schema_version!r}")
        assets = tuple(self.assets)
        clips = tuple(self.clips)
        if not all(isinstance(asset, SequenceAsset) for asset in assets):
            raise VideoSequenceError("sequence assets must be SequenceAsset values")
        if not all(isinstance(clip, SequenceClip) for clip in clips):
            raise VideoSequenceError("sequence clips must be SequenceClip values")
        _validate_sequence_parts(assets, clips)
        object.__setattr__(self, "assets", assets)
        object.__setattr__(self, "clips", clips)
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "extras", deepcopy(self.extras))

    @classmethod
    def from_json(
        cls,
        payload: Mapping[str, Any] | None,
        *,
        legacy_video: Mapping[str, Any] | None = None,
    ) -> "VideoSequence":
        if payload is None:
            if legacy_video is None:
                return cls()
            return cls.from_legacy_video(legacy_video)
        if not isinstance(payload, Mapping):
            raise VideoSequenceError("sequence must be an object")
        try:
            schema_version = int(payload.get("schema_version", VIDEO_SEQUENCE_SCHEMA_VERSION))
        except (TypeError, ValueError) as error:
            raise VideoSequenceError("sequence.schema_version must be an integer") from error
        if schema_version <= 0 or schema_version > VIDEO_SEQUENCE_SCHEMA_VERSION:
            raise VideoSequenceError(
                f"unsupported sequence.schema_version: {payload.get('schema_version')!r}"
            )
        raw_assets = payload.get("assets", [])
        raw_clips = payload.get("clips", [])
        if not isinstance(raw_assets, list):
            raise VideoSequenceError("sequence.assets must be an array")
        if not isinstance(raw_clips, list):
            raise VideoSequenceError("sequence.clips must be an array")
        assets = tuple(
            SequenceAsset.from_json(asset, index=index)
            for index, asset in enumerate(raw_assets)
        )
        clips = tuple(
            SequenceClip.from_json(clip, index=index)
            for index, clip in enumerate(raw_clips)
        )
        return cls(
            assets=assets,
            clips=clips,
            schema_version=schema_version,
            extras=deepcopy(
                {
                    key: value
                    for key, value in payload.items()
                    if key not in {"schema_version", "assets", "clips"}
                }
            ),
        )

    @classmethod
    def from_legacy_video(cls, video: Mapping[str, Any]) -> "VideoSequence":
        if not isinstance(video, Mapping):
            raise VideoSequenceError("legacy video must be an object")
        path = str(video.get("path", "")).strip()
        if not path:
            raise VideoSequenceError("legacy video.path is required")
        raw_duration = video.get("duration_seconds", 0.0) or 0.0
        duration = _finite_seconds(raw_duration, "video.duration_seconds")
        if duration < 0.0:
            raise VideoSequenceError("video.duration_seconds must be non-negative")
        asset = SequenceAsset(
            id="asset-video",
            path=path,
            duration_seconds=duration,
            extras=deepcopy(
                {
                    key: value
                    for key, value in video.items()
                    if key not in {"path", "duration_seconds"}
                }
            ),
        )
        clips: tuple[SequenceClip, ...] = ()
        # Unknown duration is kept as an asset reference but must not be
        # represented by a fabricated zero-length renderable clip.
        if duration > 0.0:
            clips = (
                SequenceClip(
                    id="clip-video",
                    asset_id=asset.id,
                    source_start=0.0,
                    source_end=duration,
                ),
            )
        return cls(assets=(asset,), clips=clips)

    def is_legacy_single_video(self) -> bool:
        """Return whether this is the compatibility sequence for ``video``.

        A sequence created by the pre-sequence project schema has stable
        ``asset-video``/``clip-video`` identities.  A trimmed singleton is no
        longer treated as compatibility state, so an explicit edit cannot be
        silently overwritten by a legacy source update.
        """

        if len(self.assets) != 1 or self.assets[0].id != "asset-video":
            return False
        if not self.clips:
            return self.assets[0].duration_seconds <= _TIME_EPSILON
        if len(self.clips) != 1:
            return False
        clip = self.clips[0]
        return (
            clip.id == "clip-video"
            and clip.asset_id == self.assets[0].id
            and abs(clip.source_start) <= _TIME_EPSILON
            and abs(clip.source_end - self.assets[0].duration_seconds) <= _TIME_EPSILON
            and clip.transition.type == "cut"
            and clip.transition.duration <= _TIME_EPSILON
        )

    def sync_legacy_video(self, video: Mapping[str, Any]) -> "VideoSequence":
        """Synchronize compatibility asset/clip fields with ``project.video``."""

        if not self.is_legacy_single_video():
            raise VideoSequenceError("explicit sequence cannot be synchronized as a legacy video")
        if not isinstance(video, Mapping):
            raise VideoSequenceError("legacy video must be an object")
        path = str(video.get("path", "")).strip()
        if not path:
            raise VideoSequenceError("legacy video.path is required")
        duration = _finite_seconds(video.get("duration_seconds", 0.0) or 0.0, "video.duration_seconds")
        if duration < 0.0:
            raise VideoSequenceError("video.duration_seconds must be non-negative")

        asset = replace(
            self.assets[0],
            path=path,
            duration_seconds=duration,
        )
        if duration <= _TIME_EPSILON:
            clips: tuple[SequenceClip, ...] = ()
        elif self.clips:
            clips = (replace(self.clips[0], source_start=0.0, source_end=duration),)
        else:
            clips = (
                SequenceClip(
                    id="clip-video",
                    asset_id=asset.id,
                    source_start=0.0,
                    source_end=duration,
                ),
            )
        return VideoSequence(
            assets=(asset,),
            clips=clips,
            schema_version=self.schema_version,
            extras=deepcopy(self.extras),
        )

    def to_json(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "assets": [asset.to_json() for asset in self.assets],
            "clips": [clip.to_json() for clip in self.clips],
        }
        payload.update(deepcopy(self.extras))
        return payload

    @property
    def timeline(self) -> SequenceTimeline:
        return build_sequence_timeline(self.clips)

    @property
    def output_duration(self) -> float:
        return self.timeline.total_duration

    def as_view(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "assets": [
                {
                    "id": asset.id,
                    "path": asset.path,
                    "duration": asset.duration_seconds,
                }
                for asset in self.assets
            ],
            **self.timeline.as_view(),
        }

    def output_to_source_seconds(self, output_seconds: Any) -> SequencePosition:
        return self.timeline.output_to_source_seconds(output_seconds)

    def source_to_output_seconds(self, clip_id: str, source_seconds: Any) -> float:
        return self.timeline.source_to_output_seconds(clip_id, source_seconds)

    def _replace(
        self,
        *,
        assets: tuple[SequenceAsset, ...] | None = None,
        clips: tuple[SequenceClip, ...] | None = None,
    ) -> "VideoSequence":
        return VideoSequence(
            assets=self.assets if assets is None else assets,
            clips=self.clips if clips is None else clips,
            schema_version=self.schema_version,
            extras=deepcopy(self.extras),
        )

    def _asset(self, asset_id: str) -> SequenceAsset:
        identifier = _required_id(asset_id, "asset_id")
        asset = next((item for item in self.assets if item.id == identifier), None)
        if asset is None:
            raise VideoSequenceError(f"unknown asset: {identifier}")
        return asset

    def _clip_index(self, clip_id: str) -> int:
        identifier = _required_id(clip_id, "clip_id")
        for index, clip in enumerate(self.clips):
            if clip.id == identifier:
                return index
        raise VideoSequenceError(f"unknown clip: {identifier}")

    @staticmethod
    def _index(value: Any, field_name: str, *, maximum: int) -> int:
        try:
            index = int(value)
        except (TypeError, ValueError) as error:
            raise VideoSequenceError(f"{field_name} must be an integer") from error
        if index != value or index < 0 or index > maximum:
            raise VideoSequenceError(f"{field_name} must be between 0 and {maximum}")
        return index

    @staticmethod
    def _transition(value: SequenceTransition | Mapping[str, Any] | None) -> SequenceTransition:
        if value is None:
            return SequenceTransition()
        if isinstance(value, SequenceTransition):
            return value
        return SequenceTransition.from_json(value)

    def add_asset(
        self,
        path: str,
        duration_seconds: Any = 0.0,
        *,
        asset_id: str | None = None,
        extras: Mapping[str, Any] | None = None,
    ) -> "VideoSequence":
        identifier = str(asset_id or "").strip() or _new_id("asset")
        if any(asset.id == identifier for asset in self.assets):
            raise VideoSequenceError(f"asset id already exists: {identifier}")
        asset = SequenceAsset(
            id=identifier,
            path=path,
            duration_seconds=duration_seconds,
            extras=deepcopy(dict(extras or {})),
        )
        return self._replace(assets=(*self.assets, asset))

    def remove_asset(self, asset_id: str, *, remove_clips: bool = False) -> "VideoSequence":
        asset = self._asset(asset_id)
        if len(self.assets) <= 1:
            raise VideoSequenceError("cannot remove the last asset")
        references = [clip for clip in self.clips if clip.asset_id == asset.id]
        if references and not remove_clips:
            raise VideoSequenceError("asset is still referenced by clips")
        remaining_assets = tuple(item for item in self.assets if item.id != asset.id)
        remaining_clips = tuple(item for item in self.clips if item.asset_id != asset.id)
        if self.clips and not remaining_clips:
            raise VideoSequenceError("cannot remove all sequence clips")
        return self._replace(assets=remaining_assets, clips=remaining_clips)

    def add_clip(
        self,
        asset_id: str,
        source_start: Any,
        source_end: Any,
        *,
        clip_id: str | None = None,
        index: int | None = None,
        transition: SequenceTransition | Mapping[str, Any] | None = None,
        audio_linked: bool = True,
        volume: Any = 1.0,
        audio_offset_seconds: Any = 0.0,
        muted: bool = False,
        extras: Mapping[str, Any] | None = None,
    ) -> "VideoSequence":
        asset = self._asset(asset_id)
        start, end = _validate_range(
            source_start,
            source_end,
            "clip",
            asset_duration=asset.duration_seconds,
        )
        identifier = str(clip_id or "").strip() or _new_id("clip")
        if any(clip.id == identifier for clip in self.clips):
            raise VideoSequenceError(f"clip id already exists: {identifier}")
        insertion_index = len(self.clips) if index is None else self._index(index, "index", maximum=len(self.clips))
        clip = SequenceClip(
            id=identifier,
            asset_id=asset.id,
            source_start=start,
            source_end=end,
            transition=self._transition(transition),
            audio_linked=audio_linked,
            volume=volume,
            audio_offset_seconds=audio_offset_seconds,
            muted=muted,
            extras=deepcopy(dict(extras or {})),
        )
        clips = list(self.clips)
        clips.insert(insertion_index, clip)
        return self._replace(clips=tuple(clips))

    def remove_clip(self, clip_id: str) -> "VideoSequence":
        index = self._clip_index(clip_id)
        if len(self.clips) <= 1:
            raise VideoSequenceError("cannot remove the last sequence clip")
        clips = list(self.clips)
        clips.pop(index)
        if index < len(clips):
            # The next clip no longer has the same incoming boundary.  Reset
            # it to a safe cut instead of silently carrying stale transition
            # intent across a changed neighbour.
            clips[index] = replace(clips[index], transition=SequenceTransition())
        return self._replace(clips=tuple(clips))

    def reorder_clip(self, clip_id: str, index: int) -> "VideoSequence":
        old_index = self._clip_index(clip_id)
        target_index = self._index(index, "index", maximum=len(self.clips) - 1)
        if old_index == target_index:
            return self
        clips = list(self.clips)
        moved = clips.pop(old_index)
        clips.insert(target_index, moved)
        if clips:
            # The first clip has no incoming boundary.  Resetting this one
            # field keeps reorder fail-closed while preserving stable IDs and
            # every other clip's edit state.
            clips[0] = replace(clips[0], transition=SequenceTransition())
        return self._replace(clips=tuple(clips))

    move_clip = reorder_clip

    def trim_clip(self, clip_id: str, source_start: Any, source_end: Any) -> "VideoSequence":
        index = self._clip_index(clip_id)
        clip = self.clips[index]
        asset = self._asset(clip.asset_id)
        start, end = _validate_range(
            source_start,
            source_end,
            f"clip {clip.id!r}",
            asset_duration=asset.duration_seconds,
        )
        clips = list(self.clips)
        clips[index] = replace(clip, source_start=start, source_end=end)
        return self._replace(clips=tuple(clips))

    def set_transition(
        self,
        clip_id: str,
        transition: SequenceTransition | Mapping[str, Any] | str,
        duration: Any | None = None,
    ) -> "VideoSequence":
        index = self._clip_index(clip_id)
        if isinstance(transition, str):
            next_transition = SequenceTransition(
                type=transition,
                duration=0.0 if duration is None else duration,
            )
        else:
            next_transition = self._transition(transition)
            if duration is not None:
                next_transition = replace(next_transition, duration=duration)
        clips = list(self.clips)
        clips[index] = replace(clips[index], transition=next_transition)
        return self._replace(clips=tuple(clips))

    set_clip_transition = set_transition

    def set_clip_audio(
        self,
        clip_id: str,
        *,
        audio_linked: bool | None = None,
        volume: Any | None = None,
        audio_offset_seconds: Any | None = None,
        muted: bool | None = None,
    ) -> "VideoSequence":
        index = self._clip_index(clip_id)
        clip = self.clips[index]
        updated = replace(
            clip,
            audio_linked=clip.audio_linked if audio_linked is None else audio_linked,
            volume=clip.volume if volume is None else volume,
            audio_offset_seconds=(
                clip.audio_offset_seconds
                if audio_offset_seconds is None
                else audio_offset_seconds
            ),
            muted=clip.muted if muted is None else muted,
        )
        clips = list(self.clips)
        clips[index] = updated
        return self._replace(clips=tuple(clips))

    update_clip_audio = set_clip_audio

    def apply(self, mutation: Callable[["VideoSequence"], "VideoSequence"]) -> "VideoSequence":
        """Apply a pure mutation and verify that it returns this domain type."""

        updated = mutation(self)
        if not isinstance(updated, VideoSequence):
            raise VideoSequenceError("sequence mutation must return a VideoSequence")
        return updated


# More explicit aliases make the boundary discoverable to render/UI slices
# without forcing them to know whether the compact Sequence* names are used.
VideoSequenceAsset = SequenceAsset
VideoSequenceTransition = SequenceTransition
VideoSequenceClip = SequenceClip
VideoSequenceTimeline = SequenceTimeline
VideoSequenceTimelineClip = SequenceTimelineClip
