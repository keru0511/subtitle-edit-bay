from __future__ import annotations

import argparse
from collections import deque
import os
import shutil
import tempfile
import subprocess
import uuid
from contextlib import contextmanager
from collections.abc import Callable, Iterator
from pathlib import Path

from .audio_mixer import build_audio_mix_filter
from .video_encoding import DEFAULT_NVENC_CQ, DEFAULT_X264_CRF, build_video_encoding_args

DEFAULT_VIDEO_CODEC = "libx264"
DEFAULT_AUDIO_CODEC = "copy"
DEFAULT_AUDIO_TRACK = "0:a:0"
DEFAULT_NVENC_PRESET = "p5"
DEFAULT_FILTERED_AUDIO_RATE = "48000"
PIX_FMT = "yuv420p"


def _copy_ass_for_filter_compatibility(subtitle: str) -> tuple[str, str | None]:
    subtitle_path = Path(subtitle)
    if "'" not in str(subtitle_path):
        return str(subtitle_path), None

    with tempfile.NamedTemporaryFile(suffix=".ass", delete=False) as temporary_ass:
        shutil.copy2(subtitle_path, temporary_ass.name)
        return temporary_ass.name, temporary_ass.name


@contextmanager
def temporary_ass_path(subtitle: str) -> Iterator[str]:
    safe_path, cleanup_path = _copy_ass_for_filter_compatibility(subtitle)
    try:
        yield safe_path
    finally:
        if cleanup_path is not None:
            Path(cleanup_path).unlink(missing_ok=True)
def _build_temporary_output(output_path: Path) -> Path:
    suffix = output_path.suffix or ".tmp"
    return output_path.with_name(f".{output_path.stem}.{uuid.uuid4().hex}.partial{suffix}")


def run_ffmpeg_command(
    command: list[str],
    *,
    progress_callback: Callable[[str], None] | None = None,
) -> None:
    if progress_callback is None:
        subprocess.run(command, check=True)
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
    if process.stdout is not None:
        for raw_line in process.stdout:
            line = raw_line.rstrip()
            if line:
                tail.append(line)
                progress_callback(line)
    return_code = process.wait()
    if return_code:
        raise subprocess.CalledProcessError(return_code, command, output="\n".join(tail))


def run_ffmpeg_with_nvenc_fallback(
    command_factory: Callable[[str], list[str]],
    video_codec: str,
    *,
    progress_callback: Callable[[str], None] | None = None,
) -> None:
    try:
        run_ffmpeg_command(command_factory(video_codec), progress_callback=progress_callback)
    except (OSError, subprocess.CalledProcessError):
        if not video_codec.lower().endswith("_nvenc"):
            raise
        if progress_callback is not None:
            progress_callback("NVENC failed; retrying subtitle render with libx264")
        run_ffmpeg_command(command_factory("libx264"), progress_callback=progress_callback)


def build_ass_filter(subtitle: str) -> str:
    subtitle_path = subtitle.replace("\\", "/").replace(":", r"\:")
    return f"ass='{subtitle_path}'"


def build_ffmpeg_command(
    video: str,
    subtitle: str,
    output: str,
    video_codec: str = DEFAULT_VIDEO_CODEC,
    audio_codec: str = DEFAULT_AUDIO_CODEC,
    nvenc_preset: str = DEFAULT_NVENC_PRESET,
    nvenc_cq: int = DEFAULT_NVENC_CQ,
    x264_crf: int = DEFAULT_X264_CRF,
    audio_filter: str | None = None,
    audio_track: str = DEFAULT_AUDIO_TRACK,
    audio_mix: dict | None = None,
    audio_offset_seconds: float = 0.0,
) -> list[str]:
    command = ["ffmpeg", "-y", "-i", video]
    if audio_mix is not None:
        input_args, mix_filter = build_audio_mix_filter(
            audio_mix,
            offset_seconds=audio_offset_seconds,
            post_filter=audio_filter,
        )
        command.extend(input_args)
        command.extend(["-filter_complex", mix_filter, "-map", "0:v:0", "-map", "[mixed_audio]"])
    else:
        command.extend(["-map", "0:v:0", "-map", audio_track])
    command.extend(["-vf", build_ass_filter(subtitle), "-c:v", video_codec])
    command.extend(build_video_encoding_args(video_codec, nvenc_preset, nvenc_cq, x264_crf))
    command.extend(["-pix_fmt", PIX_FMT])
    if audio_mix is not None:
        command.extend(["-ar", DEFAULT_FILTERED_AUDIO_RATE, "-shortest"])
        if audio_codec == "copy":
            audio_codec = "aac"
    elif audio_filter:
        command.extend(["-af", audio_filter, "-ar", DEFAULT_FILTERED_AUDIO_RATE])
    command.extend(["-c:a", audio_codec, output])
    return command


def run_ffmpeg_burn(
    video: str,
    subtitle: str,
    output: str,
    video_codec: str = DEFAULT_VIDEO_CODEC,
    audio_codec: str = DEFAULT_AUDIO_CODEC,
    nvenc_preset: str = DEFAULT_NVENC_PRESET,
    nvenc_cq: int = DEFAULT_NVENC_CQ,
    x264_crf: int = DEFAULT_X264_CRF,
    audio_filter: str | None = None,
    audio_track: str = DEFAULT_AUDIO_TRACK,
    audio_mix: dict | None = None,
    audio_offset_seconds: float = 0.0,
    progress_callback: Callable[[str], None] | None = None,
) -> Path:
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = _build_temporary_output(output_path)
    try:
        with temporary_ass_path(subtitle) as subtitle_path:
            def command_factory(codec: str) -> list[str]:
                return build_ffmpeg_command(
                    video,
                    subtitle_path,
                    str(temporary_output),
                    video_codec=codec,
                    audio_codec=audio_codec,
                    nvenc_preset=nvenc_preset,
                    nvenc_cq=nvenc_cq,
                    x264_crf=x264_crf,
                    audio_filter=audio_filter,
                    audio_track=audio_track,
                    audio_mix=audio_mix,
                    audio_offset_seconds=audio_offset_seconds,
                )

            run_ffmpeg_with_nvenc_fallback(
                command_factory,
                video_codec,
                progress_callback=progress_callback,
            )

        if not temporary_output.exists() or temporary_output.stat().st_size == 0:
            raise RuntimeError(f"FFmpeg completed without a usable output file: {temporary_output}")

        os.replace(temporary_output, output_path)
    finally:
        temporary_output.unlink(missing_ok=True)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Burn ASS subtitles into a video with FFmpeg.")
    parser.add_argument("--video", required=True, help="Input video path.")
    parser.add_argument("--subtitle", required=True, help="ASS subtitle path.")
    parser.add_argument("--output", required=True, help="Output video path.")
    parser.add_argument("--video-codec", default=DEFAULT_VIDEO_CODEC, help="Video codec such as libx264 or h264_nvenc.")
    parser.add_argument("--audio-codec", default=DEFAULT_AUDIO_CODEC, help="Audio codec such as copy or aac.")
    parser.add_argument("--audio-track", default=DEFAULT_AUDIO_TRACK, help="Audio track included in the output, such as 0:a:0.")
    parser.add_argument("--nvenc-preset", default=DEFAULT_NVENC_PRESET, help="NVENC preset used when --video-codec ends with _nvenc.")
    parser.add_argument("--nvenc-cq", type=int, default=DEFAULT_NVENC_CQ, help="NVENC constant quality target; lower is higher quality.")
    parser.add_argument("--x264-crf", type=int, default=DEFAULT_X264_CRF, help="libx264 constant quality target; lower is higher quality.")
    parser.add_argument("--run", action="store_true", help="Execute instead of printing the command.")
    args = parser.parse_args()

    command = build_ffmpeg_command(args.video, args.subtitle, args.output, video_codec=args.video_codec, audio_codec=args.audio_codec, nvenc_preset=args.nvenc_preset, nvenc_cq=args.nvenc_cq, x264_crf=args.x264_crf, audio_track=args.audio_track)
    if args.run:
        print(run_ffmpeg_burn(args.video, args.subtitle, args.output, video_codec=args.video_codec, audio_codec=args.audio_codec, nvenc_preset=args.nvenc_preset, nvenc_cq=args.nvenc_cq, x264_crf=args.x264_crf, audio_track=args.audio_track))
        return
    print(" ".join(f'"{part}"' if " " in part else part for part in command))


if __name__ == "__main__":
    main()
