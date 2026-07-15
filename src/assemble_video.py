from __future__ import annotations

import argparse
import json
import subprocess
from fractions import Fraction
from pathlib import Path

from .video_encoding import DEFAULT_NVENC_CQ, DEFAULT_X264_CRF, build_video_encoding_args

VIDEO_CODEC = "libx264"
AUDIO_CODEC = "aac"
AUDIO_BITRATE = "192k"
AUDIO_RATE = "48000"
AUDIO_CHANNELS = "2"
PIX_FMT = "yuv420p"
DEFAULT_FRAME_RATE = "60"
VIDEO_TRACK_TIMESCALE = "60000"
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


def probe_video_frame_rate(input_path: str) -> str:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=avg_frame_rate,r_frame_rate",
        "-of",
        "json",
        input_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    streams = json.loads(result.stdout or "{}").get("streams", [])
    if not streams:
        raise ValueError(f"No video stream found: {input_path}")
    for key in ("avg_frame_rate", "r_frame_rate"):
        value = str(streams[0].get(key, "")).strip()
        try:
            if value and Fraction(value) > 0:
                return value
        except (ValueError, ZeroDivisionError):
            continue
    raise ValueError(f"Could not determine video frame rate: {input_path}")


def probe_has_audio(input_path: str) -> bool:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=index",
        "-of",
        "csv=p=0",
        input_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    return bool(result.stdout.strip())


def probe_media_duration(input_path: str) -> float:
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
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    return max(0.0, float(result.stdout.strip()))


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
    frame_rate: str | float = DEFAULT_FRAME_RATE,
    has_audio: bool = True,
) -> list[str]:
    resolved_frame_rate = str(frame_rate)
    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={resolved_frame_rate},setsar=1,setpts=PTS-STARTPTS"
    )
    audio_filters = [LOUDNORM_FILTER] if audio_normalize else []
    audio_filters.extend([f"aresample={AUDIO_RATE}", "apad", "asetpts=PTS-STARTPTS"])
    audio_filter = ",".join(audio_filters)
    command = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
    ]
    if not has_audio:
        command.extend([
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_RATE}",
        ])
    command.extend([
        "-map",
        "0:v:0",
        "-map",
        "0:a:0" if has_audio else "1:a:0",
        "-vf",
        video_filter,
        "-af",
        audio_filter,
        "-c:v",
        video_codec,
    ])
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
        "-r",
        resolved_frame_rate,
        "-fps_mode",
        "cfr",
        "-video_track_timescale",
        VIDEO_TRACK_TIMESCALE,
        "-shortest",
        "-movflags",
        "+faststart",
        output_path,
    ])
    return command


def build_concat_command(manifest_path: str, output_path: str) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-fflags",
        "+genpts",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        manifest_path,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        "-movflags",
        "+faststart",
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
    frame_rate: str | float | None = None,
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

    resolved_frame_rate = str(frame_rate or probe_video_frame_rate(main_video))
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
                frame_rate=resolved_frame_rate,
                has_audio=probe_has_audio(str(clip_path)),
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
    parser.add_argument("--frame-rate", help="Target frame rate. Defaults to the main video's frame rate.")
    parser.add_argument("--no-audio-normalize", action="store_true", help="Disable loudnorm audio normalization.")
    parser.add_argument("--run", action="store_true", help="Execute instead of printing commands.")
    args = parser.parse_args()

    clips = [clip for clip in [optional_clip(args.op_file), Path(args.main_video), optional_clip(args.ed_file)] if clip]
    frame_rate = args.frame_rate or probe_video_frame_rate(args.main_video)
    if not args.run:
        for index, clip in enumerate(clips):
            normalized_path = Path(args.output).parent / f"{Path(args.output).stem}.{index}.normalized.mp4"
            print(" ".join(build_normalize_command(str(clip), str(normalized_path), args.width, args.height, not args.no_audio_normalize, video_codec=args.video_codec, audio_codec=args.audio_codec, nvenc_preset=args.nvenc_preset, nvenc_cq=args.nvenc_cq, x264_crf=args.x264_crf, frame_rate=frame_rate, has_audio=probe_has_audio(str(clip)))))
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
            frame_rate=frame_rate,
        )
    )


if __name__ == "__main__":
    main()
