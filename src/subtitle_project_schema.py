"""字幕プロジェクトの構成モデルと入力検証。保存・組版・波形生成には依存しない。"""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass, field

from .data_boundary import coerce_float, coerce_int, is_object_mapping, is_object_iterable, is_object_sequence
from .subtitle_line_count_config import normalize_subtitle_line_count

MIN_SEGMENT_DURATION_SECONDS = 0.05
DEFAULT_WAVEFORM_SAMPLE_RATE = 400


class SubtitleProjectError(ValueError):
    """Raised when an editable subtitle project is malformed."""


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
    words: list[dict[object, object]] = field(default_factory=list)
    extras: dict[object, object] = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: object, index: int = 0) -> "SubtitleSegment":
        normalized = normalize_segment(payload, index)
        return cls(
            id=str(normalized["id"]),
            start=coerce_float(normalized["start"]),
            end=coerce_float(normalized["end"]),
            text=str(normalized["text"]),
            speaker=str(normalized["speaker"]),
            emphasis=str(normalized["emphasis"]),
            position=str(normalized["position"]),
            layout_row=coerce_int(normalized["layout_row"]),
            layout_row_span=max(1, coerce_int(normalized.get("layout_row_span", 1))),
            max_width=max(4, coerce_int(normalized["max_width"])),
            subtitle_line_count=str(normalized["subtitle_line_count"]),
            subtitle_font_scale=coerce_float(normalized["subtitle_font_scale"]),
            subtitle_font_family=str(normalized["subtitle_font_family"]),
            subtitle_volume_level=coerce_float(normalized["subtitle_volume_level"]),
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
            words=_word_payloads(normalized.get("words", [])),
            extras=deepcopy(
                {
                    key: value
                    for key, value in normalized.items()
                    if key
                    not in {
                        "id",
                        "start",
                        "end",
                        "text",
                        "speaker",
                        "emphasis",
                        "position",
                        "layout_row",
                        "layout_row_span",
                        "max_width",
                        "subtitle_line_count",
                        "subtitle_font_scale",
                        "subtitle_font_family",
                        "subtitle_volume_level",
                        "layout_packed",
                        "manual_text",
                        "manual_timing",
                        "manual_speaker",
                        "manual_line_count",
                        "manual_font_scale",
                        "manual_font_family",
                        "source_speaker",
                        "source_track",
                        "source_file",
                        "words",
                    }
                }
            ),
        )

    def to_json(self) -> dict[object, object]:
        payload: dict[object, object] = {
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
    extras: dict[object, object] = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: object) -> "SpeakerInfo":
        if not is_object_mapping(payload):
            raise SubtitleProjectError("SpeakerInfo must be an object")
        return cls(
            name=str(payload.get("name", "Oz")),
            style=str(payload.get("style", payload.get("speaker", "Oz"))),
            track_key=str(payload.get("track_key", "")),
            file_name=str(payload.get("file_name", "")),
            path=str(payload.get("path", "")),
            color=str(payload.get("color", "#7FD957")),
            extras=deepcopy(
                {
                    key: value
                    for key, value in payload.items()
                    if key
                    not in {
                        "name",
                        "style",
                        "track_key",
                        "file_name",
                        "path",
                        "color",
                    }
                }
            ),
        )

    def to_json(self) -> dict[object, object]:
        payload: dict[object, object] = {
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
    extras: dict[object, object] = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: object) -> "WaveformInfo":
        if not is_object_mapping(payload):
            raise SubtitleProjectError("WaveformInfo must be an object")
        return cls(
            speaker=str(payload.get("speaker", "")),
            style=str(payload.get("style", "")),
            color=str(payload.get("color", "#7FD957")),
            source_path=str(payload.get("source_path", "")),
            offset_seconds=coerce_float(payload.get("offset_seconds", 0.0)),
            duration_seconds=coerce_float(payload.get("duration_seconds", 0.0)),
            sample_rate=coerce_int(payload.get("sample_rate", DEFAULT_WAVEFORM_SAMPLE_RATE)),
            peaks=_waveform_peaks(payload.get("peaks", [])),
            extras=deepcopy(
                {
                    key: value
                    for key, value in payload.items()
                    if key
                    not in {
                        "speaker",
                        "style",
                        "color",
                        "source_path",
                        "offset_seconds",
                        "duration_seconds",
                        "sample_rate",
                        "peaks",
                    }
                }
            ),
        )

    def to_json(self) -> dict[object, object]:
        payload: dict[object, object] = {
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
    extras: dict[object, object] = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: object) -> "AudioMixChannel":
        if not is_object_mapping(payload):
            raise SubtitleProjectError("AudioMixChannel must be an object")
        return cls(
            id=str(payload.get("id", "")),
            kind=str(payload.get("kind", "external")),
            label=str(payload.get("label", "")),
            enabled=bool(payload.get("enabled", False)),
            muted=bool(payload.get("muted", False)),
            solo=bool(payload.get("solo", False)),
            volume_percent=coerce_float(payload.get("volume_percent", 100.0)),
            selector=str(payload.get("selector")) if payload.get("selector") is not None else None,
            path=str(payload.get("path")) if payload.get("path") is not None else None,
            extras={},
        )

    def to_json(self) -> dict[object, object]:
        payload: dict[object, object] = {
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
    extras: dict[object, object] = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: object) -> "AudioMix":
        if not is_object_mapping(payload):
            raise SubtitleProjectError("AudioMix must be an object")
        raw_channels = payload.get("channels", [])
        if not is_object_iterable(raw_channels):
            raise SubtitleProjectError("audio_mix.channels must be iterable")
        channels = [AudioMixChannel.from_json(channel) for channel in raw_channels if isinstance(channel, dict)]
        return cls(
            version=coerce_int(payload.get("version", 1)),
            customized=bool(payload.get("customized", False)),
            channels=channels,
            extras={},
        )

    def to_json(self) -> dict[object, object]:
        payload: dict[object, object] = {
            "version": self.version,
            "customized": self.customized,
            "channels": [channel.to_json() for channel in self.channels],
        }
        payload.update(deepcopy(self.extras))
        return payload


def _finite_number(value: object, field: str) -> float:
    try:
        result = coerce_float(value)
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


def normalize_segment(segment: object, index: int) -> dict[object, object]:
    if not is_object_mapping(segment):
        raise SubtitleProjectError(f"segments[{index}] must be an object")
    start = max(0.0, _finite_number(segment.get("start", 0.0), "segment.start"))
    end = _finite_number(segment.get("end", start + MIN_SEGMENT_DURATION_SECONDS), "segment.end")
    if end < start + MIN_SEGMENT_DURATION_SECONDS:
        end = start + MIN_SEGMENT_DURATION_SECONDS
    text = str(segment.get("text", "")).replace("\r\n", "\n").replace("\r", "\n").replace(r"\N", "\n").strip()
    speaker = str(segment.get("speaker", "Oz")).strip() or "Oz"
    font_scale = max(
        0.1, min(4.0, _finite_number(segment.get("subtitle_font_scale", 1.0), "segment.subtitle_font_scale"))
    )
    font_family = "".join(
        char for char in str(segment.get("subtitle_font_family", "")).strip() if char >= " " and char != "\x7f"
    )[:256]
    raw_line_count = segment.get("subtitle_line_count", segment.get("line_count_override", "auto"))
    subtitle_line_count = _subtitle_line_count(raw_line_count)
    explicit_line_count = "subtitle_line_count" in segment or "line_count_override" in segment
    manual_line_count = bool(segment.get("manual_line_count", False)) or (
        explicit_line_count and subtitle_line_count != "auto"
    )
    normalized = deepcopy(dict(segment))
    normalized.update(
        {
            "id": str(segment.get("id") or f"subtitle-{index + 1:06d}"),
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
            "speaker": speaker,
            "emphasis": str(segment.get("emphasis", "normal")),
            "position": str(segment.get("position", "bottom")),
            "layout_row": max(0, coerce_int(segment.get("layout_row", 0))),
            "max_width": max(4, coerce_int(segment.get("max_width", 24))),
            "subtitle_line_count": subtitle_line_count,
            "subtitle_font_scale": round(font_scale, 4),
            "subtitle_font_family": font_family,
            "subtitle_volume_level": coerce_float(segment.get("subtitle_volume_level", 0.0)),
            "layout_packed": True,
            "manual_text": bool(segment.get("manual_text", False)),
            "manual_timing": bool(segment.get("manual_timing", False)),
            "manual_speaker": bool(segment.get("manual_speaker", False)),
            "manual_line_count": manual_line_count,
            "manual_font_scale": bool(segment.get("manual_font_scale", False)),
            "manual_font_family": bool(segment.get("manual_font_family", False)),
        }
    )
    return normalized


def _word_payloads(value: object) -> list[dict[object, object]]:
    """単語の拡張データは保持し、モデルが必要とする配列と辞書の形だけを検証する。"""
    if not isinstance(value, list) or not is_object_sequence(value):
        raise SubtitleProjectError("segment.words must be an array")
    words: list[dict[object, object]] = []
    for index, word in enumerate(value):
        if not is_object_mapping(word):
            raise SubtitleProjectError(f"segment.words[{index}] must be an object")
        words.append(deepcopy(dict(word)))
    return words


def _waveform_peaks(value: object) -> list[float]:
    """数値配列をコピーし、描画側へ不正な要素を持ち込まない。"""
    if not is_object_iterable(value):
        raise SubtitleProjectError("waveform.peaks must be iterable")
    peaks: list[float] = []
    for index, peak in enumerate(value):
        if not isinstance(peak, (int, float)):
            raise SubtitleProjectError(f"waveform.peaks[{index}] must be a number")
        peaks.append(peak)
    return peaks
