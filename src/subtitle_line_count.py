from __future__ import annotations

from typing import Any

from .models import SubtitleEvent
from .subtitle_layout.packer import (
    DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
    normalize_text,
    pack_segment_pages,
)

SUBTITLE_LINE_COUNT_AUTO = "auto"
SUPPORTED_SUBTITLE_LINE_COUNTS = {SUBTITLE_LINE_COUNT_AUTO, "1", "2"}
ELLIPSIS = "\u2026"


def normalize_subtitle_line_count(value: object = SUBTITLE_LINE_COUNT_AUTO) -> str:
    """Normalize the per-segment subtitle line count override.

    ``auto`` preserves the existing wrapping behavior. ``1`` and ``2`` force the
    maximum visible line count used by the existing text normalizer.
    """
    if value is None:
        return SUBTITLE_LINE_COUNT_AUTO
    if isinstance(value, bool):
        raise ValueError("subtitle_line_count must be 'auto', '1', or '2'")
    if isinstance(value, int) and value in (1, 2):
        return str(value)

    normalized = str(value).strip().lower()
    if normalized == "":
        return SUBTITLE_LINE_COUNT_AUTO
    if normalized in SUPPORTED_SUBTITLE_LINE_COUNTS:
        return normalized
    raise ValueError("subtitle_line_count must be 'auto', '1', or '2'")


def subtitle_line_count_max_lines(value: object = SUBTITLE_LINE_COUNT_AUTO) -> int | None:
    normalized = normalize_subtitle_line_count(value)
    if normalized == SUBTITLE_LINE_COUNT_AUTO:
        return None
    return int(normalized)


def format_segment_text(segment: dict[str, Any], default_max_width: int = 24) -> str:
    """Return the exact ASS-ready text after manual or automatic line breaking."""
    text = str(segment.get("text", "")).strip()
    if not text:
        return ""

    max_width = int(segment.get("max_width", default_max_width))
    display_duration = max(0.01, float(segment["end"]) - float(segment["start"]))
    line_count = normalize_subtitle_line_count(segment.get("subtitle_line_count", SUBTITLE_LINE_COUNT_AUTO))
    max_lines = subtitle_line_count_max_lines(line_count)
    if max_lines is None:
        return normalize_text(text, max_width=max_width, display_duration=display_duration)
    return normalize_text(
        text,
        max_width=max_width,
        max_lines=max_lines,
        display_duration=display_duration,
    )


def segment_preview_text(segment: dict[str, Any], default_max_width: int = 24) -> str:
    """Return formatted text with real newlines for Qt preview rendering."""
    return format_segment_text(segment, default_max_width=default_max_width).replace(r"\N", "\n")


def segment_editor_text(segment: dict[str, Any], default_max_width: int = 24) -> str:
    """Show automatic breaks in the editor without hiding truncated source text."""
    source = str(segment.get("text", "")).replace("\r\n", "\n").replace("\r", "\n").replace(r"\N", "\n").strip()
    preview = segment_preview_text(segment, default_max_width=default_max_width)
    if preview.endswith(ELLIPSIS) and not source.endswith(ELLIPSIS):
        max_width = int(segment.get("max_width", default_max_width))
        display_duration = max(0.01, float(segment["end"]) - float(segment["start"]))
        expanded = normalize_text(
            source,
            max_width=max_width,
            max_lines=max(2, len(source) + 1),
            display_duration=display_duration,
        )
        return expanded.replace(r"\N", "\n")
    return preview


def pack_event_with_line_count(segment: dict[str, Any], default_max_width: int = 24) -> SubtitleEvent | None:
    speaker = segment.get("speaker", "Oz")
    text = str(segment.get("text", "")).strip()
    if not text:
        return None

    emphasis = segment.get("emphasis", "normal")
    max_width = int(segment.get("max_width", default_max_width))
    display_duration = max(0.01, float(segment["end"]) - float(segment["start"]))
    line_count = normalize_subtitle_line_count(segment.get("subtitle_line_count", SUBTITLE_LINE_COUNT_AUTO))
    normalized_text = format_segment_text(segment, default_max_width=default_max_width)
    return SubtitleEvent(
        start=float(segment["start"]),
        end=float(segment["end"]),
        speaker=speaker,
        text=normalized_text,
        emphasis=emphasis,
        position="bottom",
        layer=int(segment.get("layout_row", 0)),
        metadata={
            "source_text": text,
            "max_width": max_width,
            "display_duration": display_duration,
            "subtitle_line_count": line_count,
            "subtitle_font_scale": float(segment.get("subtitle_font_scale", 1.0)),
            "subtitle_font_family": str(segment.get("subtitle_font_family", "")),
            "source_track": str(segment.get("source_track", "")),
            "source_speaker": str(segment.get("source_speaker", "")),
            "source_file": str(segment.get("source_file", "")),
        },
    )


def pack_segments_with_line_count(
    data: dict[str, Any],
    default_max_width: int = 24,
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
) -> list[SubtitleEvent]:
    events: list[SubtitleEvent] = []
    for segment in data.get("segments", []):
        pages = [segment] if segment.get("layout_packed") else pack_segment_pages(
            segment,
            subtitle_max_gap_seconds=subtitle_max_gap_seconds,
            subtitle_end_padding_seconds=subtitle_end_padding_seconds,
            subtitle_min_duration_seconds=subtitle_min_duration_seconds,
        )
        for page in pages:
            event = pack_event_with_line_count(page, default_max_width=default_max_width)
            if event is not None:
                events.append(event)
    return events
