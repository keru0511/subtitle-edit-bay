from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


class HighlightCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class SpeechSignal:
    start: float
    end: float
    level: float
    source: str = "subtitle"


def build_speech_signals(
    segments: Iterable[Mapping[str, Any]],
    audio_levels: Iterable[Mapping[str, Any]] | None = None,
) -> list[SpeechSignal]:
    """Create normalized, deterministic speech signals from available inputs."""
    source_segments = list(segments)
    levels = [
        SpeechSignal(
            start=float(item.get("start", 0.0)),
            end=float(item.get("end", 0.0)),
            level=max(0.0, float(item.get("level", item.get("rms", 0.0)))),
            source="waveform",
        )
        for item in (audio_levels or [])
        if float(item.get("end", 0.0)) > float(item.get("start", 0.0))
    ]
    max_level = max((item.level for item in levels), default=0.0)
    normalized: list[SpeechSignal] = []
    for segment in source_segments:
        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", start))
        if end <= start:
            continue
        text = str(segment.get("text", "")).strip()
        text_signal = min(1.0, len(text) / 36.0)
        overlapping = [
            item.level
            for item in levels
            if item.end > start and item.start < end
        ]
        audio_signal = (
            sum(overlapping) / len(overlapping) / max_level
            if overlapping and max_level > 0.0
            else 0.0
        )
        emphasis = 0.25 if any(mark in text for mark in ("!", "！", "?", "？")) else 0.0
        normalized.append(
            SpeechSignal(
                start=max(0.0, start),
                end=max(start, end),
                level=min(1.0, 0.55 * text_signal + 0.35 * audio_signal + emphasis),
                source="subtitle+waveform" if overlapping else "subtitle",
            )
        )
    return normalized

