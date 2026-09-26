from __future__ import annotations

import subprocess
from collections.abc import Sequence

from .data_boundary import decode_json, is_object_mapping, is_object_sequence
from .process_utils import hidden_subprocess_kwargs


def _decode_streams(output: str) -> Sequence[object]:
    payload = decode_json(output or "{}")
    if not is_object_mapping(payload):
        raise ValueError("ffprobe result must be an object")
    streams = payload.get("streams", [])
    if not is_object_sequence(streams) or isinstance(streams, (str, bytes, bytearray)):
        raise ValueError("ffprobe streams must be an array")
    return streams


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
        **hidden_subprocess_kwargs(),
    )
    return max(0.0, float((result.stdout or "0").strip()))


def probe_media_stream_types(input_path: str) -> set[str]:
    """Return media stream types present in the input file."""
    command = [
        "ffprobe",
        "-v",
        "error",
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
        **hidden_subprocess_kwargs(),
    )

    streams = _decode_streams(result.stdout)
    media_types = {str(stream.get("codec_type", "")).lower() for stream in streams if is_object_mapping(stream)}
    return {value for value in media_types if value in {"audio", "video"}}


def probe_video_stream(input_path: str) -> dict[str, object]:
    """Return the first video stream's dimensions and sample aspect ratio."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,sample_aspect_ratio",
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
        **hidden_subprocess_kwargs(),
    )

    streams = _decode_streams(result.stdout)
    if not streams or not is_object_mapping(streams[0]):
        raise ValueError(f"No video stream found: {input_path}")
    return {key: value for key, value in streams[0].items() if isinstance(key, str)}
