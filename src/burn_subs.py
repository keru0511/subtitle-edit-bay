from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from .video_encoding import DEFAULT_NVENC_CQ, DEFAULT_X264_CRF, build_video_encoding_args

DEFAULT_VIDEO_CODEC = "libx264"
DEFAULT_AUDIO_CODEC = "copy"
DEFAULT_AUDIO_TRACK = "0:a:0"
DEFAULT_NVENC_PRESET = "p5"
DEFAULT_FILTERED_AUDIO_RATE = "48000"


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
) -> list[str]:
    command = [
        "ffmpeg",
        "-y",
        "-i",
        video,
        "-map",
        "0:v:0",
        "-map",
        audio_track,
        "-vf",
        build_ass_filter(subtitle),
        "-c:v",
        video_codec,
    ]
    command.extend(build_video_encoding_args(video_codec, nvenc_preset, nvenc_cq, x264_crf))
    if audio_filter:
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
) -> Path:
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        build_ffmpeg_command(
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
        ),
        check=True,
    )
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
