from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path


CommandBuilder = Callable[[str, str], list[str]]


def _partial_output_path(output: Path) -> Path:
    suffix = output.suffix or ".tmp"
    return output.with_name(f".{output.stem}.{uuid.uuid4().hex}.partial{suffix}")


def _remove_partial_output(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


_NVENC_FALLBACK_HINTS = (
    "does not support the required nvenc api version",
    "no nvenc capable devices",
    "could not find encoder h264_nvenc",
    "unknown encoder 'h264_nvenc'",
    "could not initialize nvenc",
    "failed to initialize nvenc",
    "nvenc initialization",
    "nvenc initialization failed",
)


def _should_retry_with_cpu(error: subprocess.CalledProcessError) -> bool:
    message = " ".join(part for part in [str(error.stdout or ""), str(error.stderr or ""), str(error.output or "")] if part).lower()
    return any(hint in message for hint in _NVENC_FALLBACK_HINTS)


def run_ffmpeg_command(command: list[str]) -> None:
    """Run FFmpeg with inherited streams so GUI and console logs stay live."""
    subprocess.run(command, check=True)


def run_atomic_ffmpeg_export(
    command_builder: CommandBuilder,
    output_path: str | Path,
    *,
    video_codec: str,
) -> Path:
    """Encode to a sibling temporary file and replace the final output on success.

    NVENC failures are retried with libx264. The original completed output remains
    untouched until a non-empty replacement has been produced successfully.
    """
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = _partial_output_path(output)
    selected_codec = video_codec
    try:
        try:
            run_ffmpeg_command(command_builder(selected_codec, str(partial)))
        except subprocess.CalledProcessError as error:
            if not selected_codec.endswith("_nvenc"):
                raise
            if not _should_retry_with_cpu(error):
                raise
            _remove_partial_output(partial)
            selected_codec = "libx264"
            print(
                f"[subtitle_workflow] Requested NVENC failed ({error.returncode}); "
                "retrying with libx264 for compatibility.",
                flush=True,
            )
            run_ffmpeg_command(command_builder(selected_codec, str(partial)))

        if not partial.is_file() or partial.stat().st_size <= 0:
            raise RuntimeError(f"FFmpeg completed without a usable output file: {partial}")
        os.replace(partial, output)
        return output
    finally:
        _remove_partial_output(partial)
