from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from .merge_transcripts import write_merged_transcript
from .render_ass import parse_track_color_args, render_ass
from .runtime_config import load_command_runtime_config, resolve_bool_option, resolve_list_option, resolve_option
from .transcribe import (
    build_extract_audio_command,
    build_whisperx_command,
    expected_log_path,
    expected_transcript_path,
    run_command_with_utf8_log,
    validate_hf_token,
)
from .youtube_text import write_youtube_texts

DEFAULT_DIARIZE_TRACKS: set[str] = set()
DEFAULT_SUBTITLE_MAX_GAP_SECONDS = 0.32
DEFAULT_SUBTITLE_END_PADDING_SECONDS = 0.08
DEFAULT_SUBTITLE_MIN_DURATION_SECONDS = 0.35
DEFAULT_AUDIO_TRACKS = ["0:a:1", "0:a:3"]
DEFAULT_OUTPUT_DIR = "out"
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


def build_ass_from_transcript(
    transcript_path: str,
    ass_output: str,
    width: int = 1920,
    height: int = 1080,
    track_color_map: dict[str, str] | None = None,
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
) -> Path:
    data = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
    ass_text = render_ass(
        data,
        width=width,
        height=height,
        track_color_map=track_color_map,
        subtitle_max_gap_seconds=subtitle_max_gap_seconds,
        subtitle_end_padding_seconds=subtitle_end_padding_seconds,
        subtitle_min_duration_seconds=subtitle_min_duration_seconds,
    )
    output_path = Path(ass_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(ass_text, encoding="utf-8")
    return output_path


def derive_pipeline_paths(input_media: str, output_dir: str, audio_track: str) -> tuple[Path, Path, Path]:
    media_stem = Path(input_media).stem
    track_suffix = audio_track.replace(":", "_")
    work_dir = Path(output_dir)
    extracted_audio = work_dir / f"{media_stem}.{track_suffix}.wav"
    transcript_json = expected_transcript_path(str(extracted_audio), str(work_dir))
    ass_path = work_dir / f"{media_stem}.{track_suffix}.ass"
    return extracted_audio, transcript_json, ass_path


def normalize_diarize_tracks(diarize_tracks: set[str] | None) -> set[str]:
    tracks = diarize_tracks or set()
    validate_hf_token(diarize=bool(tracks))
    return tracks


def run_media_to_ass(
    input_media: str,
    audio_track: str,
    output_dir: str,
    model: str = "large-v3",
    device: str = "cpu",
    compute_type: str = "int8",
    width: int = 1920,
    height: int = 1080,
    diarize: bool = False,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    language: str = "ja",
    vad_onset: float | None = 0.35,
    vad_offset: float | None = 0.2,
    track_color_map: dict[str, str] | None = None,
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
) -> Path:
    work_dir = Path(output_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    extracted_audio, transcript_json, ass_output = derive_pipeline_paths(input_media, str(work_dir), audio_track)

    extract_command = build_extract_audio_command(input_media, str(extracted_audio), audio_track)
    whisperx_command = build_whisperx_command(
        str(extracted_audio),
        str(work_dir),
        model=model,
        device=device,
        compute_type=compute_type,
        diarize=diarize,
        min_speakers=min_speakers,
        max_speakers=max_speakers,
        language=language,
        vad_onset=vad_onset,
        vad_offset=vad_offset,
    )

    subprocess.run(extract_command, check=True)
    run_command_with_utf8_log(whisperx_command, str(expected_log_path(str(extracted_audio), str(work_dir))))
    return build_ass_from_transcript(
        str(transcript_json),
        str(ass_output),
        width=width,
        height=height,
        track_color_map=track_color_map,
        subtitle_max_gap_seconds=subtitle_max_gap_seconds,
        subtitle_end_padding_seconds=subtitle_end_padding_seconds,
        subtitle_min_duration_seconds=subtitle_min_duration_seconds,
    )


def run_media_to_ass_many(
    input_media: str,
    audio_tracks: list[str],
    output_dir: str,
    model: str = "large-v3",
    device: str = "cpu",
    compute_type: str = "int8",
    width: int = 1920,
    height: int = 1080,
    diarize_tracks: set[str] | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    language: str = "ja",
    vad_onset: float | None = 0.35,
    vad_offset: float | None = 0.2,
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
) -> list[Path]:
    diarize_tracks = normalize_diarize_tracks(diarize_tracks)
    return [
        run_media_to_ass(
            input_media,
            audio_track,
            output_dir,
            model=model,
            device=device,
            compute_type=compute_type,
            width=width,
            height=height,
            diarize=audio_track in diarize_tracks,
            min_speakers=min_speakers if audio_track in diarize_tracks else None,
            max_speakers=max_speakers if audio_track in diarize_tracks else None,
            language=language,
            vad_onset=vad_onset,
            vad_offset=vad_offset,
            subtitle_max_gap_seconds=subtitle_max_gap_seconds,
            subtitle_end_padding_seconds=subtitle_end_padding_seconds,
            subtitle_min_duration_seconds=subtitle_min_duration_seconds,
        )
        for audio_track in audio_tracks
    ]


def run_media_to_merged_ass(
    input_media: str,
    audio_tracks: list[str],
    output_dir: str,
    model: str = "large-v3",
    device: str = "cpu",
    compute_type: str = "int8",
    width: int = 1920,
    height: int = 1080,
    diarize_tracks: set[str] | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    language: str = "ja",
    vad_onset: float | None = 0.35,
    vad_offset: float | None = 0.2,
    track_color_map: dict[str, str] | None = None,
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
) -> tuple[Path, Path, Path | None]:
    work_dir = Path(output_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    diarize_tracks = normalize_diarize_tracks(diarize_tracks)

    transcript_map: dict[str, str] = {}
    for audio_track in audio_tracks:
        extracted_audio, transcript_json, _ = derive_pipeline_paths(input_media, str(work_dir), audio_track)
        extract_command = build_extract_audio_command(input_media, str(extracted_audio), audio_track)
        whisperx_command = build_whisperx_command(
            str(extracted_audio),
            str(work_dir),
            model=model,
            device=device,
            compute_type=compute_type,
            diarize=audio_track in diarize_tracks,
            min_speakers=min_speakers if audio_track in diarize_tracks else None,
            max_speakers=max_speakers if audio_track in diarize_tracks else None,
            language=language,
            vad_onset=vad_onset,
            vad_offset=vad_offset,
        )
        subprocess.run(extract_command, check=True)
        run_command_with_utf8_log(whisperx_command, str(expected_log_path(str(extracted_audio), str(work_dir))))
        transcript_map[audio_track] = str(transcript_json)

    media_stem = Path(input_media).stem
    merged_json = work_dir / f"{media_stem}.merged.json"
    filtered_json = work_dir / f"{media_stem}.filtered_segments.json"
    merged_ass = work_dir / f"{media_stem}.merged.ass"
    write_merged_transcript(
        transcript_map,
        str(merged_json),
        str(filtered_json),
        subtitle_max_gap_seconds=subtitle_max_gap_seconds,
        subtitle_end_padding_seconds=subtitle_end_padding_seconds,
        subtitle_min_duration_seconds=subtitle_min_duration_seconds,
    )
    build_ass_from_transcript(
        str(merged_json),
        str(merged_ass),
        width=width,
        height=height,
        track_color_map=track_color_map,
        subtitle_max_gap_seconds=subtitle_max_gap_seconds,
        subtitle_end_padding_seconds=subtitle_end_padding_seconds,
        subtitle_min_duration_seconds=subtitle_min_duration_seconds,
    )
    write_youtube_texts(str(merged_json))
    return merged_json, merged_ass, filtered_json


def print_dry_run(
    input_media: str,
    audio_tracks: list[str],
    output_dir: str,
    model: str,
    device: str,
    compute_type: str,
    diarize_tracks: set[str],
    min_speakers: int | None,
    max_speakers: int | None,
    language: str,
    vad_onset: float | None,
    vad_offset: float | None,
) -> None:
    work_dir = Path(output_dir)
    diarize_tracks = normalize_diarize_tracks(diarize_tracks)
    for audio_track in audio_tracks:
        extracted_audio, transcript_json, ass_output = derive_pipeline_paths(input_media, str(work_dir), audio_track)
        extract_command = build_extract_audio_command(input_media, str(extracted_audio), audio_track)
        whisperx_command = build_whisperx_command(
            str(extracted_audio),
            str(work_dir),
            model=model,
            device=device,
            compute_type=compute_type,
            diarize=audio_track in diarize_tracks,
            min_speakers=min_speakers if audio_track in diarize_tracks else None,
            max_speakers=max_speakers if audio_track in diarize_tracks else None,
            language=language,
            vad_onset=vad_onset,
            vad_offset=vad_offset,
        )
        print(f"Track: {audio_track}")
        print("Extract command:")
        print(" ".join(extract_command))
        print()
        print("WhisperX command:")
        print(" ".join(whisperx_command))
        print()
        print(f"Expected transcript: {transcript_json}")
        print(f"Track ASS output: {ass_output}")
        print(f"WhisperX log: {expected_log_path(str(extracted_audio), str(work_dir))}")
        if audio_track in diarize_tracks:
            expected_speaker_mode = "Diarized"
        elif audio_track == "0:a:1":
            expected_speaker_mode = "Oz"
        elif audio_track == "0:a:3":
            expected_speaker_mode = "Guest"
        else:
            expected_speaker_mode = "Custom"
        print(f"Expected speaker mode: {expected_speaker_mode}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the subtitle pipeline from chosen audio tracks to a merged ASS.")
    parser.add_argument("--config", help="Path to runtime JSON config.")
    parser.add_argument("--input", help="Input media path such as MKV or MP4.")
    parser.add_argument("--audio-track", nargs="+", default=None, help="One or more track selectors such as 0:a:1 0:a:3.")
    parser.add_argument("--output-dir", default=None, help="Working directory for WAV, JSON, and ASS.")
    parser.add_argument("--transcript", help="Existing WhisperX transcript JSON. Skips extraction and WhisperX.")
    parser.add_argument("--output", help="Output ASS path. For media input, this is the merged ASS path.")
    parser.add_argument("--model", default=None, help="WhisperX model name.")
    parser.add_argument("--device", default=None, help="WhisperX device, e.g. cpu or cuda.")
    parser.add_argument("--compute-type", default=None, help="WhisperX compute type.")
    parser.add_argument("--width", type=int, default=None, help="Video width.")
    parser.add_argument("--height", type=int, default=None, help="Video height.")
    parser.add_argument("--diarize-track", nargs="*", default=None, help="Tracks that should run diarization when HF_TOKEN is set.")
    parser.add_argument("--min-speakers", type=int, default=None, help="Minimum speaker count for diarized tracks.")
    parser.add_argument("--max-speakers", type=int, default=None, help="Maximum speaker count for diarized tracks.")
    parser.add_argument("--language", default=None, help="Language code passed to WhisperX.")
    parser.add_argument("--vad-onset", type=float, default=None, help="VAD onset threshold passed to WhisperX.")
    parser.add_argument("--vad-offset", type=float, default=None, help="VAD offset threshold passed to WhisperX.")
    parser.add_argument("--track-color", action="append", default=None, help="Per-track subtitle color like 0:a:1=#FFFFFF.")
    parser.add_argument("--subtitle-max-gap-seconds", type=float, default=None, help="Split subtitles when the gap between words reaches this many seconds.")
    parser.add_argument("--subtitle-end-padding-seconds", type=float, default=None, help="Extra time to keep a subtitle after the last word ends.")
    parser.add_argument("--subtitle-min-duration-seconds", type=float, default=None, help="Minimum subtitle duration after end trimming.")
    parser.add_argument("--run", action="store_true", default=None, help="Execute extraction and WhisperX instead of printing commands.")
    args = parser.parse_args()

    config = load_command_runtime_config("pipeline", args.config)
    input_media = resolve_option(args.input, config, "input")
    audio_tracks = resolve_list_option(args.audio_track, config, "audio_track", DEFAULT_AUDIO_TRACKS)
    output_dir_value = resolve_option(args.output_dir, config, "output_dir", DEFAULT_OUTPUT_DIR)
    transcript = resolve_option(args.transcript, config, "transcript")
    output = resolve_option(args.output, config, "output")
    model = resolve_option(args.model, config, "model", DEFAULT_MODEL)
    device = resolve_option(args.device, config, "device", DEFAULT_DEVICE)
    compute_type = resolve_option(args.compute_type, config, "compute_type", DEFAULT_COMPUTE_TYPE)
    width = int(resolve_option(args.width, config, "width", DEFAULT_WIDTH))
    height = int(resolve_option(args.height, config, "height", DEFAULT_HEIGHT))
    diarize_tracks = set(resolve_list_option(args.diarize_track, config, "diarize_track", list(DEFAULT_DIARIZE_TRACKS)))
    min_speakers = resolve_option(args.min_speakers, config, "min_speakers", DEFAULT_MIN_SPEAKERS)
    max_speakers = resolve_option(args.max_speakers, config, "max_speakers", DEFAULT_MAX_SPEAKERS)
    language = resolve_option(args.language, config, "language", DEFAULT_LANGUAGE)
    vad_onset = resolve_option(args.vad_onset, config, "vad_onset", DEFAULT_VAD_ONSET)
    vad_offset = resolve_option(args.vad_offset, config, "vad_offset", DEFAULT_VAD_OFFSET)
    track_color_map = parse_track_color_args(resolve_list_option(args.track_color, config, "track_color", []))
    subtitle_max_gap_seconds = float(resolve_option(args.subtitle_max_gap_seconds, config, "subtitle_max_gap_seconds", DEFAULT_SUBTITLE_MAX_GAP_SECONDS))
    subtitle_end_padding_seconds = float(resolve_option(args.subtitle_end_padding_seconds, config, "subtitle_end_padding_seconds", DEFAULT_SUBTITLE_END_PADDING_SECONDS))
    subtitle_min_duration_seconds = float(resolve_option(args.subtitle_min_duration_seconds, config, "subtitle_min_duration_seconds", DEFAULT_SUBTITLE_MIN_DURATION_SECONDS))
    run = resolve_bool_option(args.run, config, "run", False)

    if transcript:
        ass_output = output or str(Path(output_dir_value) / f"{Path(transcript).stem}.ass")
        result = build_ass_from_transcript(
            transcript,
            ass_output,
            width=width,
            height=height,
            track_color_map=track_color_map,
            subtitle_max_gap_seconds=subtitle_max_gap_seconds,
            subtitle_end_padding_seconds=subtitle_end_padding_seconds,
            subtitle_min_duration_seconds=subtitle_min_duration_seconds,
        )
        print(result)
        return

    if not input_media:
        raise SystemExit("Use --transcript or provide --input, or set them in --config.")

    output_dir = Path(output_dir_value)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not run:
        print_dry_run(
            input_media,
            audio_tracks,
            str(output_dir),
            model,
            device,
            compute_type,
            diarize_tracks,
            min_speakers,
            max_speakers,
            language,
            vad_onset,
            vad_offset,
        )
        stem = Path(input_media).stem
        print(f"Merged transcript: {output_dir / f'{stem}.merged.json'}")
        print(f"Filtered transcript: {output_dir / f'{stem}.filtered_segments.json'}")
        print(f"Merged ASS output: {Path(output) if output else output_dir / f'{stem}.merged.ass'}")
        print(f"YouTube title draft: {output_dir / f'{stem}.youtube_title.txt'}")
        print(f"YouTube description draft: {output_dir / f'{stem}.youtube_description.txt'}")
        return

    merged_json, merged_ass, filtered_json = run_media_to_merged_ass(
        input_media,
        audio_tracks,
        str(output_dir),
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
        subtitle_max_gap_seconds=subtitle_max_gap_seconds,
        subtitle_end_padding_seconds=subtitle_end_padding_seconds,
        subtitle_min_duration_seconds=subtitle_min_duration_seconds,
    )
    if output and Path(output) != merged_ass:
        Path(output).write_text(merged_ass.read_text(encoding="utf-8"), encoding="utf-8")
        print(merged_json)
        if filtered_json:
            print(filtered_json)
        print(Path(output))
        return
    print(merged_json)
    if filtered_json:
        print(filtered_json)
    print(merged_ass)


if __name__ == "__main__":
    main()
