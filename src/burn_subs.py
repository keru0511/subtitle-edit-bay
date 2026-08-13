from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from .audio_mixer import build_audio_mix_filter
from .video_encoding import DEFAULT_NVENC_CQ, DEFAULT_X264_CRF, build_video_encoding_args

DEFAULT_VIDEO_CODEC = "libx264"
DEFAULT_AUDIO_CODEC = "copy"
DEFAULT_AUDIO_TRACK = "0:a:0"
DEFAULT_NVENC_PRESET = "p5"
DEFAULT_FILTERED_AUDIO_RATE = "48000"
NVENC_ERROR_HINTS = (
    "driver does not support the required nvenc api version",
    "could not open encoder",
    "function not implemented",
    "unknown encoder",
)


def _is_nvenc_fallback_candidate(stderr: str | None, stdout: str | None) -> bool:
    combined = " ".join(part for part in (stderr, stdout) if part).lower()
    if not combined:
        return False
    return any(hint in combined for hint in NVENC_ERROR_HINTS)


def _run_ffmpeg_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            command,
            output=result.stdout,
            stderr=result.stderr,
        )
    return result


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
    command.extend(["-pix_fmt", "yuv420p"])
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
) -> Path:
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_ffmpeg_command(
        video,
        subtitle,
        output,
        video_codec=video_codec,
        audio_codec=audio_codec,
        nvenc_preset=nvenc_preset,
        nvenc_cq=nvenc_cq,
        x264_crf=x264_crf,
        audio_filter=audio_filter,
        audio_track=audio_track,
        audio_mix=audio_mix,
        audio_offset_seconds=audio_offset_seconds,
    )
    try:
        _run_ffmpeg_command(command)
    except subprocess.CalledProcessError as error:
        if video_codec.endswith("_nvenc") and _is_nvenc_fallback_candidate(error.stderr, error.output):
            fallback_command = build_ffmpeg_command(
                video,
                subtitle,
                output,
                video_codec="libx264",
                audio_codec=audio_codec,
                nvenc_preset=nvenc_preset,
                nvenc_cq=nvenc_cq,
                x264_crf=x264_crf,
                audio_filter=audio_filter,
                audio_track=audio_track,
                audio_mix=audio_mix,
                audio_offset_seconds=audio_offset_seconds,
            )
            print(
                f"[subtitle_workflow] Requested NVENC failed ({error.returncode}); "
                "retrying with libx264 for compatibility.",
            )
            _run_ffmpeg_command(fallback_command)
        else:
            raise
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
