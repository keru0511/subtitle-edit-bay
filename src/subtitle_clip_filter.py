"""Source-scoped subtitle filtering for a video sequence.

Subtitle segments currently belong to the project's primary video. Filtering a
different sequence asset must show no subtitles rather than silently attributing
the primary video's speech to that asset. Times always refer to source media,
not the potentially reordered sequence output timeline.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def is_primary_video_asset(project: Mapping[str, object] | None, asset_path: str) -> bool:
    """Whether a sequence asset is the primary video's transcript source."""

    if not project or not str(asset_path).strip():
        return False
    video = project.get("video")
    if not isinstance(video, Mapping):
        return False
    primary = video.get("path")
    if not isinstance(primary, str) or not primary.strip():
        return False
    try:
        primary_path = os.path.normcase(str(Path(primary).expanduser().resolve(strict=False)))
        asset = os.path.normcase(str(Path(asset_path).expanduser().resolve(strict=False)))
    except (OSError, RuntimeError, ValueError):
        return False
    return primary_path == asset


def overlaps_source_window(
    segment_start: float,
    segment_end: float,
    clip_start: float,
    clip_end: float,
) -> bool:
    """Intersection of source-time half-open intervals [start, end)."""

    return segment_start < clip_end and segment_end > clip_start
