from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections import deque
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
    message = " ".join(
        part
        for part in [str(error.stdout or ""), str(error.stderr or ""), str(error.output or "")]
        if part
    ).lower()
    return any(hint in message for hint in _NVENC_FALLBACK_HINTS)


def run_ffmpeg_command(
    command: list[str],
    *,
    progress_callback: Callable[[str], None] | None = None,
) -> None:
    """Run FFmpeg and retain its output for diagnostics and fallback decisions."""
    if progress_callback is None:
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
            )
        except subprocess.CalledProcessError as error:
            output = error.output if error.output is not None else error.stderr
            if output:
                _emit_ffmpeg_output(output)
            raise
        if completed.stdout:
            _emit_ffmpeg_output(completed.stdout)
        return

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    tail: deque[str] = deque(maxlen=80)
    stdout = process.stdout
    try:
        if stdout is not None:
            for raw_line in stdout:
                line = raw_line.rstrip()
                if line:
                    tail.append(line)
                    progress_callback(line)
    finally:
        close = getattr(stdout, "close", None)
        if close is not None:
            close()
    return_code = process.wait()
    if return_code:
        raise subprocess.CalledProcessError(return_code, command, output="\n".join(tail))


def _emit_ffmpeg_output(output: str | bytes) -> None:
    rendered = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else output
    print(rendered, file=sys.stderr, end="" if rendered.endswith("\n") else "\n", flush=True)


def run_atomic_ffmpeg_export(
    command_builder: CommandBuilder,
    output_path: str | Path,
    *,
    video_codec: str,
    progress_callback: Callable[[str], None] | None = None,
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
            run_ffmpeg_command(
                command_builder(selected_codec, str(partial)),
                progress_callback=progress_callback,
            )
        except subprocess.CalledProcessError as error:
            if not selected_codec.endswith("_nvenc"):
                raise
            if not _should_retry_with_cpu(error):
                raise
            _remove_partial_output(partial)
            selected_codec = "libx264"
            if progress_callback is not None:
                progress_callback(
                    f"[subtitle_workflow] Requested NVENC failed ({error.returncode}); "
                    "retrying with libx264 for compatibility.",
                )
            else:
                print(
                    f"[subtitle_workflow] Requested NVENC failed ({error.returncode}); "
                    "retrying with libx264 for compatibility.",
                    flush=True,
                )
            run_ffmpeg_command(
                command_builder(selected_codec, str(partial)),
                progress_callback=progress_callback,
            )

        if not partial.is_file() or partial.stat().st_size <= 0:
            raise RuntimeError(f"FFmpeg completed without a usable output file: {partial}")
        os.replace(partial, output)
        return output
    finally:
        _remove_partial_output(partial)
