from __future__ import annotations

from typing import Any

from .models import SubtitleEvent
from .subtitle_layout.packer import (
    DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
    normalize_text,
    pack_segment_pages,
    text_width,
)

SUBTITLE_LINE_COUNT_AUTO = "auto"
SUPPORTED_SUBTITLE_LINE_COUNTS = {SUBTITLE_LINE_COUNT_AUTO, "1", "2"}
ELLIPSIS = "\u2026"
UNTRUNCATED_MAX_LINES = 999


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
    text_without_newlines = "\n" not in text and "\r" not in text
    preserve_full_text = bool(segment.get("manual_text", False)) and text_without_newlines and max_lines is None
    visible_lines = UNTRUNCATED_MAX_LINES if preserve_full_text else max_lines
    if visible_lines is None:
        return normalize_text(text, max_width=max_width, display_duration=display_duration)
    return normalize_text(
        text,
        max_width=max_width,
        max_lines=visible_lines,
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
        return source
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


def _manual_segment_lines(segment: dict[str, Any], max_width: int, display_duration: float) -> list[str]:
    text = " ".join(str(segment.get("text", "")).split())
    if not text:
        return []
    if " " not in text:
        wrapped = normalize_text(
            text,
            max_width=max_width,
            max_lines=UNTRUNCATED_MAX_LINES,
            display_duration=display_duration,
        )
        return [line for line in wrapped.split(r"\N") if line]

    lines: list[str] = []
    current: list[str] = []
    current_width = 0
    for word in text.split():
        if not current:
            candidate = word
        else:
            candidate = " ".join(( *current, word))
        if text_width(candidate) > max_width and current:
            lines.append(" ".join(current))
            current = [word]
            current_width = text_width(word)
            continue
        if text_width(candidate) > max_width:
            for segment_text in normalize_text(
                word,
                max_width=max_width,
                max_lines=UNTRUNCATED_MAX_LINES,
                display_duration=display_duration,
            ).split(r"\N"):
                if segment_text:
                    lines.append(segment_text)
            current = []
            current_width = 0
            continue
        current.append(word)
        current_width = text_width(candidate)
    if current:
        lines.append(" ".join(current))
    return [line for line in lines if line.strip()]


def _repack_manual_segment_by_duration(
    segment: dict[str, Any],
    lines: list[str],
) -> list[dict[str, Any]]:
    if not lines:
        return [segment]
    if len(lines) <= 2:
        return [segment]

    start = float(segment["start"])
    end = float(segment["end"])
    duration = max(0.01, end - start)
    spans = [max(1, text_width(line)) for line in lines]
    total = sum(spans)
    events: list[dict[str, Any]] = []
    cursor = start
    accumulated = 0
    for index in range(0, len(lines), 2):
        chunk = lines[index : index + 2]
        chunk_weight = sum(spans[index + offset] for offset in range(len(chunk)))
        accumulated += chunk_weight
        next_end = start + duration * (accumulated / max(1, total))
        if index + 2 >= len(lines):
            next_end = end
        events.append(
            {
                **segment,
                "start": round(cursor, 3),
                "end": round(min(next_end, end), 3),
                "text": r"\N".join(chunk),
            }
        )
        cursor = events[-1]["end"]
        if cursor >= end:
            break
    events[-1]["end"] = end
    return events


def pack_segments_with_line_count(
    data: dict[str, Any],
    default_max_width: int = 24,
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
) -> list[SubtitleEvent]:
    def _repack_segment(segment: dict[str, Any]) -> list[dict[str, Any]]:
        text = str(segment.get("text", "")).strip()
        if (
            bool(segment.get("manual_text", False))
            and "\n" not in text
            and "\r" not in text
            and "\\N" not in text
        ):
            max_width = int(segment.get("max_width", default_max_width))
            duration = max(0.01, float(segment["end"]) - float(segment["start"]))
            try:
                pages = pack_segment_pages(
                    segment,
                    subtitle_max_gap_seconds=subtitle_max_gap_seconds,
                    subtitle_end_padding_seconds=subtitle_end_padding_seconds,
                    subtitle_min_duration_seconds=subtitle_min_duration_seconds,
                )
            except RuntimeError:
                return _repack_manual_segment_by_duration(
                    segment,
                    _manual_segment_lines(segment, max_width, duration),
                )
            if len(pages) > 1:
                return pages
            return _repack_manual_segment_by_duration(
                segment,
                _manual_segment_lines(segment, max_width, duration),
            )
        return [segment]

    events: list[SubtitleEvent] = []
    for segment in data.get("segments", []):
        if segment.get("layout_packed"):
            pages = _repack_segment(segment)
        else:
            pages = pack_segment_pages(
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
