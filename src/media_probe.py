from __future__ import annotations

import subprocess


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
