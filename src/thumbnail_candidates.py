"""Local thumbnail candidate ranking and FFmpeg command generation."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any


def rank_thumbnail_candidates(candidates: Sequence[Mapping[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    """Rank local frame metadata, penalizing dark, duplicate, or low-signal frames."""

    ranked: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        item = copy.deepcopy(dict(candidate))
        highlight = float(item.get("highlight_score", 0.0))
        brightness = float(item.get("brightness", 0.5))
        duplicate_distance = float(item.get("duplicate_distance", 1.0))
        brightness_score = max(0.0, 1.0 - abs(brightness - 0.55) / 0.55)
        duplicate_penalty = max(0.0, 1.0 - duplicate_distance)
        item["thumbnail_score"] = round(highlight * 0.6 + brightness_score * 0.4 - duplicate_penalty * 0.35, 6)
        item["candidate_id"] = str(item.get("candidate_id", item.get("id", f"thumbnail-{index + 1}")))
        ranked.append(item)
    ranked.sort(key=lambda item: (-item["thumbnail_score"], item["candidate_id"]))
    return ranked[: max(0, limit)]


def build_thumbnail_command(
    video_path: str, timestamp: float, output_path: str, *, width: int = 1280, height: int = 720
) -> list[str]:
    if timestamp < 0:
        raise ValueError("timestamp must not be negative")
    if width <= 0 or height <= 0:
        raise ValueError("thumbnail dimensions must be positive")
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        video_path,
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        output_path,
    ]


def build_contact_sheet_command(input_pattern: str, output_path: str, *, columns: int = 3, tile_duration: int = 1) -> list[str]:
    if columns <= 0 or tile_duration <= 0:
        raise ValueError("contact sheet options must be positive")
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-pattern_type",
        "glob",
        "-i",
        input_pattern,
        "-vf",
        f"tile={columns}x{columns}:padding=4:margin=4",
        "-frames:v",
        "1",
        output_path,
    ]
