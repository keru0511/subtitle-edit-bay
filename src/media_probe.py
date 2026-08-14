from __future__ import annotations

import json
import subprocess
from typing import Any


def probe_media_duration(input_path: str) -> float:
    """Return the non-negative container duration reported by ffprobe."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        input_path,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return max(0.0, float((result.stdout or "0").strip()))


def probe_media_stream_types(input_path: str) -> set[str]:
    """Return media stream types present in the input file."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v,a",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "json",
        input_path,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )

    payload: dict[str, Any] = json.loads(result.stdout or "{}")
    streams = payload.get("streams", [])
    media_types = {
        str(stream.get("codec_type", "")).lower()
        for stream in streams
        if isinstance(stream, dict)
    }
    return {value for value in media_types if value in {"audio", "video"}}
