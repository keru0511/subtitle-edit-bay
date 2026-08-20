from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

DEFAULT_SHORT_WIDTH = 1080
DEFAULT_SHORT_HEIGHT = 1920
DEFAULT_SHORT_FPS = 30
SHORT_VIDEO_SCHEMA_VERSION = 2
VALID_FIT_MODES = ("cover", "contain", "blur")
VALID_TRANSITION_TYPES = ("crossfade", "fade", "cut")


class ShortVideoError(ValueError):
    """Raised when a short_video project section is malformed."""


@dataclass(frozen=True)
class ShortVideoOutput:
    width: int = DEFAULT_SHORT_WIDTH
    height: int = DEFAULT_SHORT_HEIGHT
    fps: int = DEFAULT_SHORT_FPS

    @classmethod
    def from_json(cls, payload: dict[str, Any] | None) -> "ShortVideoOutput":
        if payload is None:
            return cls()
        if not isinstance(payload, dict):
            raise ShortVideoError("short_video.output must be an object")
        width = int(payload.get("width", DEFAULT_SHORT_WIDTH))
        height = int(payload.get("height", DEFAULT_SHORT_HEIGHT))
        fps = int(payload.get("fps", DEFAULT_SHORT_FPS))
        if width <= 0 or height <= 0 or fps <= 0:
            raise ShortVideoError("short_video.output width/height/fps must be positive")
        return cls(width=width, height=height, fps=fps)

    def to_json(self) -> dict[str, Any]:
        return {"width": self.width, "height": self.height, "fps": self.fps}


@dataclass(frozen=True)
class ShortVideoTransition:
    type: str = "crossfade"
    duration: float = 0.5

    @classmethod
    def from_json(cls, payload: dict[str, Any] | None) -> "ShortVideoTransition":
        if payload is None:
            return cls()
        if not isinstance(payload, dict):
            raise ShortVideoError("short_video.transition must be an object")
        transition_type = str(payload.get("type", "crossfade")).lower()
        if transition_type not in VALID_TRANSITION_TYPES:
            raise ShortVideoError(
                f"short_video.transition.type must be one of {VALID_TRANSITION_TYPES}"
            )
        duration = _finite_number(payload.get("duration", 0.5), "transition.duration")
        if duration < 0.0:
            duration = 0.0
        return cls(type=transition_type, duration=round(duration, 3))

    def to_json(self) -> dict[str, Any]:
        return {"type": self.type, "duration": self.duration}


@dataclass(frozen=True)
class ShortVideoBgm:
    path: str = ""
    in_point: float = 0.0
    out_point: float = 0.0
    start: float = 0.0
    volume: float = 0.3

    @classmethod
    def from_json(cls, payload: dict[str, Any] | None) -> "ShortVideoBgm":
        if payload is None:
            return cls()
        if not isinstance(payload, dict):
            raise ShortVideoError("short_video.bgm must be an object")
        path = str(payload.get("path", ""))
        in_point = _finite_number(payload.get("in", 0.0), "bgm.in")
        out_point = _finite_number(payload.get("out", 0.0), "bgm.out")
        start = _finite_number(payload.get("start", 0.0), "bgm.start")
        volume = _finite_number(payload.get("volume", 0.3), "bgm.volume")
        if in_point < 0.0:
            in_point = 0.0
        if out_point < in_point:
            out_point = in_point
        if start < 0.0:
            start = 0.0
        if volume < 0.0:
            volume = 0.0
        if volume > 1.0:
            volume = 1.0
        return cls(
            path=path,
            in_point=round(in_point, 3),
            out_point=round(out_point, 3),
            start=round(start, 3),
            volume=round(volume, 3),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "in": self.in_point,
            "out": self.out_point,
            "start": self.start,
            "volume": self.volume,
        }


@dataclass(frozen=True)
class ShortVideoClip:
    segment_id: str = ""
    start: float = 0.0
    end: float = 0.0
    fit: str | None = None
    background_color: str | None = None

    @classmethod
    def from_json(cls, payload: dict[str, Any] | None) -> "ShortVideoClip":
        if payload is None:
            return cls()
        if not isinstance(payload, dict):
            raise ShortVideoError("clip must be an object")
        segment_id = str(payload.get("segment_id", ""))
        start = _finite_number(payload.get("start", 0.0), "clip.start")
        end = _finite_number(payload.get("end", start), "clip.end")
        raw_fit = payload.get("fit")
        fit = str(raw_fit).lower() if raw_fit not in (None, "") else None
        if fit is not None and fit not in VALID_FIT_MODES:
            raise ShortVideoError(f"clip.fit must be one of {VALID_FIT_MODES}")
        raw_background_color = payload.get("background_color")
        background_color = (
            str(raw_background_color) if raw_background_color not in (None, "") else None
        )
        if start < 0.0:
            start = 0.0
        if end < start:
            end = start
        return cls(
            segment_id=segment_id,
            start=round(start, 3),
            end=round(end, 3),
            fit=fit,
            background_color=background_color,
        )

    def to_json(self) -> dict[str, Any]:
        payload = {
            "segment_id": self.segment_id,
            "start": self.start,
            "end": self.end,
        }
        if self.fit:
            payload["fit"] = self.fit
        if self.background_color:
            payload["background_color"] = self.background_color
        return payload


@dataclass(frozen=True)
class ShortVideo:
    enabled: bool = False
    output: ShortVideoOutput = field(default_factory=ShortVideoOutput)
    global_fit: str = "cover"
    global_background_color: str = "000000"
    subtitle_scale_percent: float = 150.0
    transition: ShortVideoTransition = field(default_factory=ShortVideoTransition)
    bgm: ShortVideoBgm = field(default_factory=ShortVideoBgm)
    clips: list[ShortVideoClip] = field(default_factory=list)

    @classmethod
    def from_json(cls, payload: dict[str, Any] | None) -> "ShortVideo":
        if payload is None:
            return cls()
        if not isinstance(payload, dict):
            raise ShortVideoError("short_video must be an object")
        enabled = bool(payload.get("enabled", False))
        try:
            schema_version = int(payload.get("schema_version", 1))
        except (TypeError, ValueError) as error:
            raise ShortVideoError("short_video.schema_version must be an integer") from error
        if schema_version > SHORT_VIDEO_SCHEMA_VERSION:
            raise ShortVideoError(
                f"unsupported short_video schema_version: {payload.get('schema_version')!r}"
            )
        output = ShortVideoOutput.from_json(payload.get("output"))
        global_fit = str(payload.get("global_fit", "cover")).lower()
        if global_fit not in VALID_FIT_MODES:
            raise ShortVideoError(
                f"short_video.global_fit must be one of {VALID_FIT_MODES}"
            )
        global_background_color = str(payload.get("global_background_color", "000000"))
        subtitle_scale_percent = _finite_number(
            payload.get("subtitle_scale_percent", 150.0), "subtitle_scale_percent"
        )
        if subtitle_scale_percent < 0.0:
            subtitle_scale_percent = 0.0
        transition = ShortVideoTransition.from_json(payload.get("transition"))
        bgm = ShortVideoBgm.from_json(payload.get("bgm"))
        clips = []
        for index, raw_clip in enumerate(payload.get("clips", [])):
            if isinstance(raw_clip, dict):
                clip_payload = raw_clip
                # The pre-schema-version serializer wrote default values for every
                # clip, so those values represented inheritance rather than an
                # explicit override. New serializers include schema_version=2 and
                # therefore preserve an explicit default override.
                if schema_version < SHORT_VIDEO_SCHEMA_VERSION:
                    clip_payload = dict(raw_clip)
                    if clip_payload.get("fit") == "cover":
                        clip_payload.pop("fit", None)
                    if clip_payload.get("background_color", "").lower() == "000000":
                        clip_payload.pop("background_color", None)
                clips.append(ShortVideoClip.from_json(clip_payload))
            else:
                raise ShortVideoError(f"short_video.clips[{index}] must be an object")
        return cls(
            enabled=enabled,
            output=output,
            global_fit=global_fit,
            global_background_color=global_background_color,
            subtitle_scale_percent=round(subtitle_scale_percent, 3),
            transition=transition,
            bgm=bgm,
            clips=clips,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": SHORT_VIDEO_SCHEMA_VERSION,
            "enabled": self.enabled,
            "output": self.output.to_json(),
            "global_fit": self.global_fit,
            "global_background_color": self.global_background_color,
            "subtitle_scale_percent": self.subtitle_scale_percent,
            "transition": self.transition.to_json(),
            "bgm": self.bgm.to_json(),
            "clips": [clip.to_json() for clip in self.clips],
        }

    def clip_for_segment(self, segment_id: str) -> ShortVideoClip | None:
        for clip in self.clips:
            if clip.segment_id == segment_id:
                return clip
        return None


def _finite_number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ShortVideoError(f"{field} must be a number") from error
    if not math.isfinite(result):
        raise ShortVideoError(f"{field} must be finite")
    return result
