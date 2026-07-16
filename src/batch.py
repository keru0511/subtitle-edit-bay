from __future__ import annotations

import argparse
from pathlib import Path

from .ass_template import DEFAULT_SUBTITLE_FONT_SIZE
from .assemble_video import assemble_video, optional_clip, probe_media_duration
from .burn_subs import run_ffmpeg_burn
from .pipeline import run_media_to_merged_ass
from .render_ass import parse_track_color_args
from .runtime_config import load_command_runtime_config, resolve_bool_option, resolve_list_option, resolve_option
from .video_encoding import DEFAULT_NVENC_CQ, DEFAULT_X264_CRF

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".mov", ".webm"}
RESERVED_INPUT_NAMES = {"op.mp4", "ed.mp4"}
DEFAULT_AUDIO_TRACKS = ["0:a:1", "0:a:3"]
DEFAULT_INPUT_DIR = "video_import"
DEFAULT_OUTPUT_DIR = "video_export"
DEFAULT_MODEL = "large-v3"
DEFAULT_DEVICE = "cpu"
DEFAULT_COMPUTE_TYPE = "int8"
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_MIN_SPEAKERS = 3
DEFAULT_MAX_SPEAKERS = 3
DEFAULT_LANGUAGE = "ja"
DEFAULT_VAD_ONSET = 0.35
DEFAULT_VAD_OFFSET = 0.2
DEFAULT_OP_FILE = "video_import/op.mp4"
DEFAULT_ED_FILE = "video_import/ed.mp4"
DEFAULT_VIDEO_CODEC = "libx264"
DEFAULT_AUDIO_CODEC = "aac"
DEFAULT_OUTPUT_AUDIO_TRACK = "0:a:0"
DEFAULT_NVENC_PRESET = "p5"
DEFAULT_SUBTITLE_MAX_GAP_SECONDS = 0.32
DEFAULT_SUBTITLE_END_PADDING_SECONDS = 0.08
DEFAULT_SUBTITLE_MIN_DURATION_SECONDS = 0.35


def iter_video_files(input_dir: str) -> list[Path]:
    base = Path(input_dir)
    return sorted(
        path
        for path in base.iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS and path.name.lower() not in RESERVED_INPUT_NAMES
    )


def derive_export_paths(video_path: str, export_root: str, audio_track: str) -> tuple[Path, Path]:
    video = Path(video_path)
    track_suffix = audio_track.replace(":", "_")
    work_dir = Path(export_root) / video.stem
    final_video = work_dir / f"{video.stem}.{track_suffix}.subtitled.mp4"
    return work_dir, final_video


def derive_merged_export_paths(video_path: str, export_root: str) -> tuple[Path, Path]:
    video = Path(video_path)
    work_dir = Path(export_root) / video.stem
    final_video = work_dir / f"{video.stem}.merged.subtitled.mp4"
    return work_dir, final_video


def process_video(
    video_path: str,
    export_root: str,
    audio_tracks: list[str],
    model: str,
    device: str,
    compute_type: str,
    width: int,
    height: int,
    diarize_tracks: set[str],
    min_speakers: int | None,
    max_speakers: int | None,
    language: str,
    vad_onset: float | None,
    vad_offset: float | None,
    op_file: str | None,
    ed_file: str | None,
    audio_normalize: bool,
    video_codec: str,
    audio_codec: str,
    output_audio_track: str,
    nvenc_preset: str,
    nvenc_cq: int,
    x264_crf: int,
    track_color_map: dict[str, str] | None,
    subtitle_font_size: int,
    subtitle_max_gap_seconds: float,
    subtitle_end_padding_seconds: float,
    subtitle_min_duration_seconds: float,
) -> Path:
    work_dir, final_video = derive_merged_export_paths(video_path, export_root)
    op_clip = optional_clip(op_file)
    youtube_timestamp_offset_seconds = probe_media_duration(str(op_clip)) if op_clip else 0.0
    _, merged_ass, _ = run_media_to_merged_ass(
        video_path,
        audio_tracks,
        str(work_dir),
        model=model,
        device=device,
        compute_type=compute_type,
        width=width,
        height=height,
        diarize_tracks=diarize_tracks,
        min_speakers=min_speakers,
        max_speakers=max_speakers,
        language=language,
        vad_onset=vad_onset,
        vad_offset=vad_offset,
        track_color_map=track_color_map,
        subtitle_font_size=subtitle_font_size,
        subtitle_max_gap_seconds=subtitle_max_gap_seconds,
        subtitle_end_padding_seconds=subtitle_end_padding_seconds,
        subtitle_min_duration_seconds=subtitle_min_duration_seconds,
        youtube_timestamp_offset_seconds=youtube_timestamp_offset_seconds,
    )
    main_subtitled = work_dir / f"{Path(video_path).stem}.main.subtitled.mp4"
    run_ffmpeg_burn(
        video_path,
        str(merged_ass),
        str(main_subtitled),
        video_codec=video_codec,
        audio_codec="copy",
        audio_track=output_audio_track,
        nvenc_preset=nvenc_preset,
        nvenc_cq=nvenc_cq,
        x264_crf=x264_crf,
    )
    return assemble_video(
        str(main_subtitled),
        str(final_video),
        width=width,
        height=height,
        op_file=op_file,
        ed_file=ed_file,
        audio_normalize=audio_normalize,
        video_codec=video_codec,
        audio_codec=audio_codec,
        nvenc_preset=nvenc_preset,
        nvenc_cq=nvenc_cq,
        x264_crf=x264_crf,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch process videos from video_import to video_export.")
    parser.add_argument("--config", help="Path to runtime JSON config.")
    parser.add_argument("--input-dir", default=None, help="Directory containing source videos.")
    parser.add_argument("--output-dir", default=None, help="Directory for exported results.")
    parser.add_argument("--audio-track", nargs="+", default=None, help="One or more track selectors such as 0:a:1 0:a:3.")
    parser.add_argument("--model", default=None, help="WhisperX model name.")
    parser.add_argument("--device", default=None, help="WhisperX device, e.g. cpu or cuda.")
    parser.add_argument("--compute-type", default=None, help="WhisperX compute type.")
    parser.add_argument("--width", type=int, default=None, help="Video width for ASS layout.")
    parser.add_argument("--height", type=int, default=None, help="Video height for ASS layout.")
    parser.add_argument("--diarize-track", nargs="*", default=None, help="Tracks that should run diarization when HF_TOKEN is set.")
    parser.add_argument("--min-speakers", type=int, default=None, help="Minimum speaker count for diarized tracks.")
    parser.add_argument("--max-speakers", type=int, default=None, help="Maximum speaker count for diarized tracks.")
    parser.add_argument("--language", default=None, help="Language code passed to WhisperX.")
    parser.add_argument("--vad-onset", type=float, default=None, help="VAD onset threshold passed to WhisperX.")
    parser.add_argument("--vad-offset", type=float, default=None, help="VAD offset threshold passed to WhisperX.")
    parser.add_argument("--track-color", action="append", default=None, help="Per-track subtitle color like 0:a:1=#FFFFFF.")
    parser.add_argument("--subtitle-font-size", type=int, default=None, help="Base ASS subtitle font size.")
    parser.add_argument("--op-file", default=None, help="Optional OP clip path.")
    parser.add_argument("--ed-file", default=None, help="Optional ED clip path.")
    parser.add_argument("--video-codec", default=None, help="FFmpeg video codec such as libx264 or h264_nvenc.")
    parser.add_argument("--audio-codec", default=None, help="FFmpeg audio codec for normalized clips.")
    parser.add_argument("--output-audio-track", default=None, help="Main-video audio track included in the final output, such as 0:a:0.")
    parser.add_argument("--nvenc-preset", default=None, help="NVENC preset used when --video-codec ends with _nvenc.")
    parser.add_argument("--nvenc-cq", type=int, default=None, help="NVENC constant quality target; lower is higher quality.")
    parser.add_argument("--x264-crf", type=int, default=None, help="libx264 constant quality target; lower is higher quality.")
    parser.add_argument("--no-audio-normalize", dest="audio_normalize", action="store_false", default=None, help="Disable loudnorm audio normalization.")
    parser.add_argument("--subtitle-max-gap-seconds", type=float, default=None, help="Split subtitles when the gap between words reaches this many seconds.")
    parser.add_argument("--subtitle-end-padding-seconds", type=float, default=None, help="Extra time to keep a subtitle after the last word ends.")
    parser.add_argument("--subtitle-min-duration-seconds", type=float, default=None, help="Minimum subtitle duration after end trimming.")
    parser.add_argument("--run", action="store_true", default=None, help="Execute processing instead of printing targets.")
    args = parser.parse_args()

    config = load_command_runtime_config("batch", args.config)
    input_dir_value = resolve_option(args.input_dir, config, "input_dir", DEFAULT_INPUT_DIR)
    output_dir_value = resolve_option(args.output_dir, config, "output_dir", DEFAULT_OUTPUT_DIR)
    audio_tracks = resolve_list_option(args.audio_track, config, "audio_track", DEFAULT_AUDIO_TRACKS)
    model = resolve_option(args.model, config, "model", DEFAULT_MODEL)
    device = resolve_option(args.device, config, "device", DEFAULT_DEVICE)
    compute_type = resolve_option(args.compute_type, config, "compute_type", DEFAULT_COMPUTE_TYPE)
    width = int(resolve_option(args.width, config, "width", DEFAULT_WIDTH))
    height = int(resolve_option(args.height, config, "height", DEFAULT_HEIGHT))
    diarize_tracks = set(resolve_list_option(args.diarize_track, config, "diarize_track", []))
    min_speakers = resolve_option(args.min_speakers, config, "min_speakers", DEFAULT_MIN_SPEAKERS)
    max_speakers = resolve_option(args.max_speakers, config, "max_speakers", DEFAULT_MAX_SPEAKERS)
    language = resolve_option(args.language, config, "language", DEFAULT_LANGUAGE)
    vad_onset = resolve_option(args.vad_onset, config, "vad_onset", DEFAULT_VAD_ONSET)
    vad_offset = resolve_option(args.vad_offset, config, "vad_offset", DEFAULT_VAD_OFFSET)
    track_color_map = parse_track_color_args(resolve_list_option(args.track_color, config, "track_color", []))
    subtitle_font_size = int(resolve_option(args.subtitle_font_size, config, "subtitle_font_size", DEFAULT_SUBTITLE_FONT_SIZE))
    op_file = resolve_option(args.op_file, config, "op_file", DEFAULT_OP_FILE)
    ed_file = resolve_option(args.ed_file, config, "ed_file", DEFAULT_ED_FILE)
    video_codec = resolve_option(args.video_codec, config, "video_codec", DEFAULT_VIDEO_CODEC)
    audio_codec = resolve_option(args.audio_codec, config, "audio_codec", DEFAULT_AUDIO_CODEC)
    output_audio_track = resolve_option(args.output_audio_track, config, "output_audio_track", DEFAULT_OUTPUT_AUDIO_TRACK)
    nvenc_preset = resolve_option(args.nvenc_preset, config, "nvenc_preset", DEFAULT_NVENC_PRESET)
    nvenc_cq = int(resolve_option(args.nvenc_cq, config, "nvenc_cq", DEFAULT_NVENC_CQ))
    x264_crf = int(resolve_option(args.x264_crf, config, "x264_crf", DEFAULT_X264_CRF))
    audio_normalize = resolve_bool_option(args.audio_normalize, config, "audio_normalize", True)
    subtitle_max_gap_seconds = float(resolve_option(args.subtitle_max_gap_seconds, config, "subtitle_max_gap_seconds", DEFAULT_SUBTITLE_MAX_GAP_SECONDS))
    subtitle_end_padding_seconds = float(resolve_option(args.subtitle_end_padding_seconds, config, "subtitle_end_padding_seconds", DEFAULT_SUBTITLE_END_PADDING_SECONDS))
    subtitle_min_duration_seconds = float(resolve_option(args.subtitle_min_duration_seconds, config, "subtitle_min_duration_seconds", DEFAULT_SUBTITLE_MIN_DURATION_SECONDS))
    run = resolve_bool_option(args.run, config, "run", False)

    input_dir = Path(input_dir_value)
    output_dir = Path(output_dir_value)
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    videos = iter_video_files(str(input_dir))
    if not videos:
        print(f"No videos found in {input_dir}")
        return

    for video in videos:
        if not run:
            work_dir, final_video = derive_merged_export_paths(str(video), str(output_dir))
            print(f"INPUT  : {video}")
            print(f"TRACKS : {' '.join(audio_tracks)}")
            print(f"AUDIO  : {output_audio_track}")
            print(f"OP     : {optional_clip(op_file) or 'disabled'}")
            print(f"ED     : {optional_clip(ed_file) or 'disabled'}")
            print(f"WORKDIR: {work_dir}")
            print(f"OUTPUT : {final_video}")
            print(f"TITLE  : {work_dir / f'{video.stem}.youtube_title.txt'}")
            print(f"DESC   : {work_dir / f'{video.stem}.youtube_description.txt'}")
            print()
            continue

        result = process_video(
            str(video),
            str(output_dir),
            audio_tracks,
            model=model,
            device=device,
            compute_type=compute_type,
            width=width,
            height=height,
            diarize_tracks=diarize_tracks,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            language=language,
            vad_onset=vad_onset,
            vad_offset=vad_offset,
            op_file=op_file,
            ed_file=ed_file,
            audio_normalize=audio_normalize,
            video_codec=video_codec,
            audio_codec=audio_codec,
            output_audio_track=output_audio_track,
            nvenc_preset=nvenc_preset,
            nvenc_cq=nvenc_cq,
            x264_crf=x264_crf,
            track_color_map=track_color_map,
            subtitle_font_size=subtitle_font_size,
            subtitle_max_gap_seconds=subtitle_max_gap_seconds,
            subtitle_end_padding_seconds=subtitle_end_padding_seconds,
            subtitle_min_duration_seconds=subtitle_min_duration_seconds,
        )
        print(result)


if __name__ == "__main__":
    main()
