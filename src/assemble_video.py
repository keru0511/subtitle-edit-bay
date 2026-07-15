from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from .video_encoding import DEFAULT_NVENC_CQ, DEFAULT_X264_CRF, build_video_encoding_args

VIDEO_CODEC = "libx264"
AUDIO_CODEC = "aac"
AUDIO_BITRATE = "192k"
AUDIO_RATE = "48000"
AUDIO_CHANNELS = "2"
PIX_FMT = "yuv420p"
DEFAULT_AUDIO_TARGET_LUFS = -16.0
DEFAULT_AUDIO_LOUDNESS_RANGE = 11.0
DEFAULT_AUDIO_TRUE_PEAK_DB = -1.5
DEFAULT_NVENC_PRESET = "p5"


def format_filter_number(value: float) -> str:
    return f"{float(value):g}"


def build_loudnorm_filter(
    target_lufs: float = DEFAULT_AUDIO_TARGET_LUFS,
    loudness_range: float = DEFAULT_AUDIO_LOUDNESS_RANGE,
    true_peak_db: float = DEFAULT_AUDIO_TRUE_PEAK_DB,
) -> str:
    return (
        f"loudnorm=I={format_filter_number(target_lufs)}"
        f":LRA={format_filter_number(loudness_range)}"
        f":TP={format_filter_number(true_peak_db)}"
    )


LOUDNORM_FILTER = build_loudnorm_filter()


def optional_clip(path: str | None) -> Path | None:
    if not path:
        return None
    clip = Path(path)
    if clip.is_file():
        return clip
    return None


def build_normalize_command(
    input_path: str,
    output_path: str,
    width: int,
    height: int,
    audio_normalize: bool = True,
    video_codec: str = VIDEO_CODEC,
    audio_codec: str = AUDIO_CODEC,
    nvenc_preset: str = DEFAULT_NVENC_PRESET,
    nvenc_cq: int = DEFAULT_NVENC_CQ,
    x264_crf: int = DEFAULT_X264_CRF,
) -> list[str]:
    video_filter = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    audio_filter = LOUDNORM_FILTER if audio_normalize else "aresample=48000"
    command = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-vf",
        video_filter,
        "-af",
        audio_filter,
        "-c:v",
        video_codec,
    ]
    command.extend(build_video_encoding_args(video_codec, nvenc_preset, nvenc_cq, x264_crf))
    command.extend([
        "-pix_fmt",
        PIX_FMT,
        "-c:a",
        audio_codec,
        "-b:a",
        AUDIO_BITRATE,
        "-ar",
        AUDIO_RATE,
        "-ac",
        AUDIO_CHANNELS,
        output_path,
    ])
    return command


def build_concat_command(manifest_path: str, output_path: str) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        manifest_path,
        "-c",
        "copy",
        output_path,
    ]


def write_concat_manifest(video_paths: list[str], manifest_path: str) -> Path:
    manifest = Path(manifest_path)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"file '{Path(path).resolve().as_posix()}'" for path in video_paths]
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def assemble_video(
    main_video: str,
    output_path: str,
    width: int,
    height: int,
    op_file: str | None = None,
    ed_file: str | None = None,
    audio_normalize: bool = True,
    video_codec: str = VIDEO_CODEC,
    audio_codec: str = AUDIO_CODEC,
    nvenc_preset: str = DEFAULT_NVENC_PRESET,
    nvenc_cq: int = DEFAULT_NVENC_CQ,
    x264_crf: int = DEFAULT_X264_CRF,
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    clips: list[tuple[str, Path]] = [("main", Path(main_video))]
    op_clip = optional_clip(op_file)
    ed_clip = optional_clip(ed_file)
    if op_clip:
        clips.insert(0, ("op", op_clip))
    if ed_clip:
        clips.append(("ed", ed_clip))

    normalized_paths: list[str] = []
    for label, clip_path in clips:
        normalized_path = output.parent / f"{output.stem}.{label}.normalized.mp4"
        subprocess.run(
            build_normalize_command(
                str(clip_path),
                str(normalized_path),
                width=width,
                height=height,
                audio_normalize=audio_normalize,
                video_codec=video_codec,
                audio_codec=audio_codec,
                nvenc_preset=nvenc_preset,
                nvenc_cq=nvenc_cq,
                x264_crf=x264_crf,
            ),
            check=True,
        )
        normalized_paths.append(str(normalized_path))

    manifest_path = output.parent / f"{output.stem}.concat.txt"
    write_concat_manifest(normalized_paths, str(manifest_path))
    subprocess.run(build_concat_command(str(manifest_path), str(output)), check=True)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize and concatenate OP/main/ED videos with FFmpeg.")
    parser.add_argument("--main-video", required=True, help="Main subtitled video path.")
    parser.add_argument("--output", required=True, help="Output video path.")
    parser.add_argument("--width", type=int, default=1920, help="Target width.")
    parser.add_argument("--height", type=int, default=1080, help="Target height.")
    parser.add_argument("--op-file", help="Optional OP clip path.")
    parser.add_argument("--ed-file", help="Optional ED clip path.")
    parser.add_argument("--video-codec", default=VIDEO_CODEC, help="Video codec such as libx264 or h264_nvenc.")
    parser.add_argument("--audio-codec", default=AUDIO_CODEC, help="Audio codec used for normalized clips.")
    parser.add_argument("--nvenc-preset", default=DEFAULT_NVENC_PRESET, help="NVENC preset used when --video-codec ends with _nvenc.")
    parser.add_argument("--nvenc-cq", type=int, default=DEFAULT_NVENC_CQ, help="NVENC constant quality target; lower is higher quality.")
    parser.add_argument("--x264-crf", type=int, default=DEFAULT_X264_CRF, help="libx264 constant quality target; lower is higher quality.")
    parser.add_argument("--no-audio-normalize", action="store_true", help="Disable loudnorm audio normalization.")
    parser.add_argument("--run", action="store_true", help="Execute instead of printing commands.")
    args = parser.parse_args()

    clips = [clip for clip in [optional_clip(args.op_file), Path(args.main_video), optional_clip(args.ed_file)] if clip]
    if not args.run:
        for index, clip in enumerate(clips):
            normalized_path = Path(args.output).parent / f"{Path(args.output).stem}.{index}.normalized.mp4"
            print(" ".join(build_normalize_command(str(clip), str(normalized_path), args.width, args.height, not args.no_audio_normalize, video_codec=args.video_codec, audio_codec=args.audio_codec, nvenc_preset=args.nvenc_preset, nvenc_cq=args.nvenc_cq, x264_crf=args.x264_crf)))
        print(" ".join(build_concat_command(str(Path(args.output).with_suffix(".concat.txt")), args.output)))
        return

    print(
        assemble_video(
            args.main_video,
            args.output,
            width=args.width,
            height=args.height,
            op_file=args.op_file,
            ed_file=args.ed_file,
            audio_normalize=not args.no_audio_normalize,
            video_codec=args.video_codec,
            audio_codec=args.audio_codec,
            nvenc_preset=args.nvenc_preset,
            nvenc_cq=args.nvenc_cq,
            x264_crf=args.x264_crf,
        )
    )


if __name__ == "__main__":
    main()
