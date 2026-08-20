from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .ass_template import (
    DEFAULT_SUBTITLE_FONT_SIZE,
    DEFAULT_SUBTITLE_OUTLINE_COLOR,
    DEFAULT_SUBTITLE_OUTLINE_THICKNESS,
)
from .render_ass import render_ass
from .short_video_schema import ShortVideo, ShortVideoError
from .short_video_timeline import build_short_video_timeline
from .subtitle_layout.packer import (
    DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
)
from .subtitle_project import load_project


def derive_short_ass_path(project_path: str | Path) -> Path:
    project = Path(project_path)
    suffix = ".subtitle-project.json"
    base_name = project.name[:-len(suffix)] if project.name.endswith(suffix) else project.stem
    return project.with_name(f"{base_name}.short.ass")


def _number(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _remap_words(
    words: object,
    *,
    clip_start: float,
    source_start: float,
    source_end: float,
    output_start: float,
) -> list[dict[str, Any]]:
    remapped: list[dict[str, Any]] = []
    if not isinstance(words, list):
        return remapped
    for word in words:
        if not isinstance(word, dict):
            continue
        word_start = _number(word.get("start"), source_start)
        word_end = _number(word.get("end"), word_start)
        clipped_start = max(source_start, word_start)
        clipped_end = min(source_end, word_end)
        if clipped_end <= clipped_start:
            continue
        item = deepcopy(word)
        item["start"] = round(output_start + clipped_start - clip_start, 6)
        item["end"] = round(output_start + clipped_end - clip_start, 6)
        remapped.append(item)
    return remapped


def remap_short_video_segments(
    segments: list[dict[str, Any]],
    short_video: ShortVideo,
) -> list[dict[str, Any]]:
    """Copy source subtitles onto the rendered short-video timeline."""
    remapped: list[dict[str, Any]] = []
    timeline = build_short_video_timeline(short_video)

    for clip_index, timeline_clip in enumerate(timeline.clips):
        clip = timeline_clip.clip
        for segment_index, segment in enumerate(segments):
            segment_start = _number(segment.get("start"), clip.start)
            segment_end = _number(segment.get("end"), segment_start)
            clipped_start = max(clip.start, segment_start)
            clipped_end = min(clip.end, segment_end)
            if clipped_end <= clipped_start:
                continue

            mapped_start = timeline_clip.output_start + clipped_start - clip.start
            mapped_end = timeline_clip.output_start + clipped_end - clip.start
            item = deepcopy(segment)
            original_id = str(segment.get("id", segment_index))
            item["id"] = f"short-{clip_index}-{original_id}"
            item["start"] = round(mapped_start, 6)
            item["end"] = round(mapped_end, 6)
            item["words"] = _remap_words(
                segment.get("words"),
                clip_start=clip.start,
                source_start=clipped_start,
                source_end=clipped_end,
                output_start=timeline_clip.output_start,
            )
            remapped.append(item)

    return remapped


def build_short_video_ass(
    project_path: str | Path,
    output_path: str | Path | None = None,
    *,
    _project: dict[str, Any] | None = None,
) -> Path:
    project = _project if _project is not None else load_project(project_path)
    short_video = ShortVideo.from_json(project.get("short_video"))
    if not short_video.clips:
        raise ShortVideoError("short_video.clips is empty; no subtitles can be mapped")

    segments = [segment for segment in project.get("segments", []) if isinstance(segment, dict)]
    remapped_segments = remap_short_video_segments(segments, short_video)
    settings = project.get("subtitle_settings", {})
    if not isinstance(settings, dict):
        settings = {}
    scale = max(0.01, short_video.subtitle_scale_percent / 100.0)
    base_font_size = int(settings.get("font_size", DEFAULT_SUBTITLE_FONT_SIZE))
    subtitle_font_size = max(3, round(base_font_size * scale))
    track_colors = {
        str(item.get("track_key", "")): str(item.get("color", ""))
        for item in project.get("speakers", [])
        if isinstance(item, dict) and item.get("track_key") and item.get("color")
    }

    ass_text = render_ass(
        {"segments": remapped_segments},
        width=short_video.output.width,
        height=short_video.output.height,
        track_color_map=track_colors,
        subtitle_font_size=subtitle_font_size,
        subtitle_outline_color=str(
            settings.get("outline_color", DEFAULT_SUBTITLE_OUTLINE_COLOR)
        ),
        subtitle_outline_thickness=int(
            settings.get("outline_thickness", DEFAULT_SUBTITLE_OUTLINE_THICKNESS)
        ),
        subtitle_max_gap_seconds=float(
            settings.get("max_gap_seconds", DEFAULT_SUBTITLE_MAX_GAP_SECONDS)
        ),
        subtitle_end_padding_seconds=float(
            settings.get("end_padding_seconds", DEFAULT_SUBTITLE_END_PADDING_SECONDS)
        ),
        subtitle_min_duration_seconds=float(
            settings.get("min_duration_seconds", DEFAULT_SUBTITLE_MIN_DURATION_SECONDS)
        ),
    )
    output = Path(output_path) if output_path else derive_short_ass_path(project_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(ass_text, encoding="utf-8")
    return output
