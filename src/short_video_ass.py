from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path

from .ass_template import (
    DEFAULT_SUBTITLE_FONT_SIZE,
    DEFAULT_SUBTITLE_OUTLINE_COLOR,
    DEFAULT_SUBTITLE_OUTLINE_THICKNESS,
)
from .data_boundary import coerce_float, coerce_int, is_object_dict, is_object_iterable, is_object_list
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
    base_name = project.name[: -len(suffix)] if project.name.endswith(suffix) else project.stem
    return project.with_name(f"{base_name}.short.ass")


def _number(value: object, default: float) -> float:
    try:
        return coerce_float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _remap_words(
    words: object,
    *,
    clip_start: float,
    source_start: float,
    source_end: float,
    output_start: float,
) -> list[dict[object, object]]:
    remapped: list[dict[object, object]] = []
    if not is_object_list(words):
        return remapped
    for word in words:
        if not is_object_dict(word):
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
    segments: Sequence[Mapping[object, object]],
    short_video: ShortVideo,
) -> list[dict[object, object]]:
    """Copy source subtitles onto the rendered short-video timeline."""
    remapped: list[dict[object, object]] = []
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
            item = deepcopy(dict(segment))
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
    _project: Mapping[str, object] | Mapping[object, object] | None = None,
) -> Path:
    project = _project if _project is not None else load_project(project_path)
    short_video = ShortVideo.from_json(project.get("short_video"))
    if not short_video.clips:
        raise ShortVideoError("short_video.clips is empty; no subtitles can be mapped")

    raw_segments = project.get("segments", [])
    if not is_object_iterable(raw_segments):
        raise TypeError("project segments must be iterable")
    segments = [segment for segment in raw_segments if is_object_dict(segment)]
    remapped_segments = remap_short_video_segments(segments, short_video)
    settings = project.get("subtitle_settings", {})
    if not is_object_dict(settings):
        settings = {}
    scale = max(0.01, short_video.subtitle_scale_percent / 100.0)
    base_font_size = coerce_int(settings.get("font_size", DEFAULT_SUBTITLE_FONT_SIZE))
    subtitle_font_size = max(3, round(base_font_size * scale))
    raw_speakers = project.get("speakers", [])
    if not is_object_iterable(raw_speakers):
        raise TypeError("project speakers must be iterable")
    track_colors: dict[str, str] = {}
    for speaker in raw_speakers:
        if is_object_dict(speaker) and speaker.get("track_key") and speaker.get("color"):
            track_colors[str(speaker.get("track_key", ""))] = str(speaker.get("color", ""))

    ass_text = render_ass(
        {"segments": remapped_segments},
        width=short_video.output.width,
        height=short_video.output.height,
        track_color_map=track_colors,
        subtitle_font_size=subtitle_font_size,
        subtitle_outline_color=str(settings.get("outline_color", DEFAULT_SUBTITLE_OUTLINE_COLOR)),
        subtitle_outline_thickness=coerce_int(settings.get("outline_thickness", DEFAULT_SUBTITLE_OUTLINE_THICKNESS)),
        subtitle_max_gap_seconds=coerce_float(settings.get("max_gap_seconds", DEFAULT_SUBTITLE_MAX_GAP_SECONDS)),
        subtitle_end_padding_seconds=coerce_float(
            settings.get("end_padding_seconds", DEFAULT_SUBTITLE_END_PADDING_SECONDS)
        ),
        subtitle_min_duration_seconds=coerce_float(
            settings.get("min_duration_seconds", DEFAULT_SUBTITLE_MIN_DURATION_SECONDS)
        ),
    )
    output = Path(output_path) if output_path else derive_short_ass_path(project_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(ass_text, encoding="utf-8")
    return output
