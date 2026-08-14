from __future__ import annotations

import argparse
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .ass_template import DEFAULT_SUBTITLE_FONT_SIZE
from .assemble_video import build_loudnorm_filter
from .burn_subs import build_ass_filter, run_ffmpeg_burn
from .craig_transcription_execution import (
    CraigTranscriptionHint,
    resolve_craig_transcription_hint,
    transcribe_craig_audio_file_with_cache,
)
from .merge_transcripts import is_short_reaction, max_width_for_speaker, refine_segments
from .pipeline import build_ass_from_transcript
from .render_ass import parse_track_color_args
from .runtime_config import load_command_runtime_config, resolve_bool_option, resolve_list_option, resolve_option
from .runtime_dependencies import check_runtime_dependencies, format_dependency_error
from .silence_cut import (
    build_no_speech_plan,
    cut_media_ranges,
    detect_speech_ranges,
    probe_media_duration,
    retime_segments_for_keep_ranges,
)
from .transcribe import expected_transcript_path, probe_audio_streams
from .video_encoding import DEFAULT_NVENC_CQ, DEFAULT_X264_CRF

DEFAULT_STYLE_SEQUENCE = ["Oz", "A", "B", "C"]
DEFAULT_ALIGNMENT_SAMPLE_RATE = 120
DEFAULT_ALIGNMENT_OFFSET_ADJUSTMENT = 0.0
DEFAULT_POSTPROCESS_WORKERS = max(1, min(4, os.cpu_count() or 1))
DEFAULT_OUTPUT_DIR = "out"
DEFAULT_MODEL = "large-v3"
DEFAULT_DEVICE = "cpu"
DEFAULT_COMPUTE_TYPE = "int8"
DEFAULT_LANGUAGE = "ja"
DEFAULT_VAD_ONSET = 0.35
DEFAULT_VAD_OFFSET = 0.2
DEFAULT_VIDEO_CODEC = "libx264"
DEFAULT_AUDIO_CODEC = "copy"
DEFAULT_OUTPUT_AUDIO_TRACK = "0:a:0"
DEFAULT_FILTERED_AUDIO_CODEC = "aac"
DEFAULT_NVENC_PRESET = "p5"
DEFAULT_AUDIO_NORMALIZE = True
DEFAULT_AUDIO_TARGET_LUFS = -16.0
DEFAULT_AUDIO_LOUDNESS_RANGE = 11.0
DEFAULT_AUDIO_TRUE_PEAK_DB = -1.5
DEFAULT_CUT_NO_SPEECH = False
DEFAULT_NO_SPEECH_MIN_SECONDS = 1.2
DEFAULT_SPEECH_PADDING_SECONDS = 0.25
DEFAULT_SPEECH_THRESHOLD_DB = "-40dB"
DEFAULT_SPEECH_MIN_CLIP_SECONDS = 0.25
DEFAULT_SPEECH_DETECT_SILENCE_SECONDS = 0.1
DEFAULT_SUBTITLE_MAX_GAP_SECONDS = 0.32
DEFAULT_SUBTITLE_END_PADDING_SECONDS = 0.08
DEFAULT_SUBTITLE_MIN_DURATION_SECONDS = 0.35
DEFAULT_SUBTITLE_VOLUME_SCALE_PERCENT = 20.0
SUBTITLE_VOLUME_SAMPLE_RATE = 1000
SUBTITLE_VOLUME_RANGE_DB = 12.0
DEFAULT_INPUT_ROOT = "video_import"
DEFAULT_EXPORT_ROOT = "video_export"
SUPPORTED_VIDEO_EXTENSIONS = {".mkv", ".mp4", ".mov", ".webm"}
SUPPORTED_CRAIG_EXTENSIONS = {".aac", ".flac", ".wav", ".m4a"}
EMPHASIS_MARKERS = ["!", "?", "?", "?"]


@dataclass(frozen=True)
class PipelineInputs:
    video_path: str
    audio_files: list[Path]
    reference_audio: Path
    output_dir: Path
    style_map: dict[str, str]


@dataclass(frozen=True)
class AlignmentResult:
    matched_track: str
    offset_seconds: float
    score: float
    reference_audio: str


@dataclass(frozen=True)
class TranscriptionResult:
    transcript_map: dict[str, str]
    segments: list[dict]


@dataclass(frozen=True)
class SegmentRefinementResult:
    merged_segments: list[dict]
    filtered_segments: list[dict]


@dataclass(frozen=True)
class SegmentArtifacts:
    merged_json: Path
    filtered_json: Path
    ass_path: Path


@dataclass(frozen=True)
class RenderResult:
    final_video: Path
    no_speech_report: Path | None
    cut_merged_json: Path | None
    cut_ass_path: Path | None


def resolve_pipeline_inputs(
    video_path: str,
    audio_dir: str | None,
    output_dir: str,
    reference_audio_name: str | None = None,
    selected_audio_files: list[str] | None = None,
) -> PipelineInputs:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    audio_files = resolve_craig_audio_files(audio_dir, selected_audio_files)
    if not audio_files:
        raise SystemExit("No Craig speaker audio files were selected.")

    reference_audio = resolve_reference_audio_path(audio_files, reference_audio_name, audio_dir)
    if reference_audio is None or not reference_audio.exists():
        raise SystemExit("Reference audio file was not found.")

    style_map = build_speaker_style_map(audio_files)
    return PipelineInputs(
        video_path=str(video_path),
        audio_files=audio_files,
        reference_audio=reference_audio,
        output_dir=output_path,
        style_map=style_map,
    )


def run_alignment_stage(
    video_path: str,
    reference_audio: Path,
    reference_track: str | None,
    alignment_sample_rate: int,
    alignment_offset_adjustment: float,
) -> AlignmentResult:
    matched_track, offset_seconds, score = resolve_alignment(
        video_path,
        str(reference_audio),
        reference_track,
        alignment_sample_rate,
    )
    offset_seconds += alignment_offset_adjustment
    return AlignmentResult(
        matched_track=matched_track,
        offset_seconds=offset_seconds,
        score=score,
        reference_audio=str(reference_audio),
    )


def run_transcription_stage(
    audio_files: list[Path],
    output_dir: Path,
    style_map: dict[str, str],
    offset_seconds: float,
    *,
    model: str = DEFAULT_MODEL,
    device: str = DEFAULT_DEVICE,
    compute_type: str = DEFAULT_COMPUTE_TYPE,
    language: str = DEFAULT_LANGUAGE,
    vad_onset: float | None = DEFAULT_VAD_ONSET,
    vad_offset: float | None = DEFAULT_VAD_OFFSET,
    skip_existing_transcripts: bool = True,
    postprocess_workers: int = DEFAULT_POSTPROCESS_WORKERS,
    subtitle_font_size: int = DEFAULT_SUBTITLE_FONT_SIZE,
    subtitle_volume_scale_percent: float = DEFAULT_SUBTITLE_VOLUME_SCALE_PERCENT,
) -> TranscriptionResult:
    transcript_dir = output_dir / "transcripts"
    batch = transcribe_craig_audio_files(
        audio_files,
        transcript_dir,
        style_map,
        offset_seconds,
        model=model,
        device=device,
        compute_type=compute_type,
        language=language,
        vad_onset=vad_onset,
        vad_offset=vad_offset,
        skip_existing_transcripts=skip_existing_transcripts,
        postprocess_workers=postprocess_workers,
        subtitle_font_size=subtitle_font_size,
        subtitle_volume_scale_percent=subtitle_volume_scale_percent,
    )
    return TranscriptionResult(
        transcript_map=batch.transcript_map,
        segments=batch.segments,
    )


def run_refine_stage(
    segments: list[dict],
    *,
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
) -> SegmentRefinementResult:
    merged_segments, filtered_segments = refine_segments(
        segments,
        subtitle_max_gap_seconds=subtitle_max_gap_seconds,
        subtitle_end_padding_seconds=subtitle_end_padding_seconds,
        subtitle_min_duration_seconds=subtitle_min_duration_seconds,
    )
    return SegmentRefinementResult(
        merged_segments=merged_segments,
        filtered_segments=filtered_segments,
    )


def write_segment_outputs(
    video_path: str,
    output_dir: Path,
    refinement: SegmentRefinementResult,
    *,
    track_color_map: dict[str, str] | None = None,
    subtitle_font_size: int = DEFAULT_SUBTITLE_FONT_SIZE,
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
) -> SegmentArtifacts:
    merged_path = output_dir / f"{Path(video_path).stem}.craig.merged.json"
    filtered_path = output_dir / f"{Path(video_path).stem}.craig.filtered.json"
    ass_path = output_dir / f"{Path(video_path).stem}.craig.ass"
    merged_json = write_json(str(merged_path), {"segments": refinement.merged_segments})
    write_json(str(filtered_path), {"segments": refinement.filtered_segments})
    build_ass_from_transcript(
        str(merged_json),
        str(ass_path),
        track_color_map=track_color_map,
        subtitle_font_size=subtitle_font_size,
        subtitle_max_gap_seconds=subtitle_max_gap_seconds,
        subtitle_end_padding_seconds=subtitle_end_padding_seconds,
        subtitle_min_duration_seconds=subtitle_min_duration_seconds,
    )
    return SegmentArtifacts(
        merged_json=merged_json,
        filtered_json=filtered_path,
        ass_path=ass_path,
    )


def run_render_stage(
    video_path: str,
    output_dir: Path,
    audio_files: list[Path],
    merged_segments: list[dict],
    ass_path: Path,
    alignment: AlignmentResult,
    *,
    track_color_map: dict[str, str] | None = None,
    cut_no_speech: bool = DEFAULT_CUT_NO_SPEECH,
    no_speech_min_seconds: float = DEFAULT_NO_SPEECH_MIN_SECONDS,
    speech_padding_seconds: float = DEFAULT_SPEECH_PADDING_SECONDS,
    speech_threshold_db: str = DEFAULT_SPEECH_THRESHOLD_DB,
    speech_min_clip_seconds: float = DEFAULT_SPEECH_MIN_CLIP_SECONDS,
    video_codec: str = "libx264",
    audio_codec: str = "copy",
    output_audio_track: str = DEFAULT_OUTPUT_AUDIO_TRACK,
    nvenc_preset: str = "p5",
    nvenc_cq: int = DEFAULT_NVENC_CQ,
    x264_crf: int = DEFAULT_X264_CRF,
    audio_normalize: bool = DEFAULT_AUDIO_NORMALIZE,
    audio_target_lufs: float = DEFAULT_AUDIO_TARGET_LUFS,
    audio_loudness_range: float = DEFAULT_AUDIO_LOUDNESS_RANGE,
    audio_true_peak_db: float = DEFAULT_AUDIO_TRUE_PEAK_DB,
    subtitle_font_size: int = DEFAULT_SUBTITLE_FONT_SIZE,
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
) -> RenderResult:
    final_video = output_dir / f"{Path(video_path).stem}.craig.subtitled.mp4"
    loudnorm_filter = (
        build_loudnorm_filter(
            target_lufs=audio_target_lufs,
            loudness_range=audio_loudness_range,
            true_peak_db=audio_true_peak_db,
        )
        if audio_normalize
        else None
    )

    if cut_no_speech:
        video_duration = probe_media_duration(video_path)
        speaker_speech_ranges: list[tuple[float, float]] = []
        for audio_file in audio_files:
            log_progress(f"Detecting speech activity in {audio_file.name} at {speech_threshold_db}")
            speaker_speech_ranges.extend(
                detect_speech_ranges(
                    str(audio_file),
                    noise=speech_threshold_db,
                    duration=DEFAULT_SPEECH_DETECT_SILENCE_SECONDS,
                )
            )

        no_speech_ranges, keep_ranges = build_no_speech_plan(
            video_duration,
            speaker_speech_ranges,
            alignment.offset_seconds,
            min_no_speech_seconds=no_speech_min_seconds,
            padding=speech_padding_seconds,
            min_clip_duration=speech_min_clip_seconds,
        )
        if not keep_ranges:
            raise SystemExit("No speech activity was detected; refusing to cut the entire video.")

        estimated_duration = sum(end - start for start, end in keep_ranges)
        no_speech_report = write_json(
            str(output_dir / f"{Path(video_path).stem}.craig.no_speech.json"),
            {
                "video_duration": video_duration,
                "estimated_output_duration": estimated_duration,
                "offset_seconds": alignment.offset_seconds,
                "speech_threshold_db": speech_threshold_db,
                "no_speech_min_seconds": no_speech_min_seconds,
                "speech_padding_seconds": speech_padding_seconds,
                "no_speech_ranges": no_speech_ranges,
                "keep_ranges": keep_ranges,
            },
        )
        cut_merged_json = write_json(
            str(output_dir / f"{Path(video_path).stem}.craig.cut.merged.json"),
            {"segments": retime_segments_for_keep_ranges(merged_segments, keep_ranges)},
        )
        cut_ass_path = output_dir / f"{Path(video_path).stem}.craig.cut.ass"
        build_ass_from_transcript(
            str(cut_merged_json),
            str(cut_ass_path),
            track_color_map=track_color_map,
            subtitle_font_size=subtitle_font_size,
            subtitle_max_gap_seconds=subtitle_max_gap_seconds,
            subtitle_end_padding_seconds=subtitle_end_padding_seconds,
            subtitle_min_duration_seconds=subtitle_min_duration_seconds,
        )
        removed_duration = max(0.0, video_duration - estimated_duration)
        log_progress(
            f"Rendering subtitles and cutting {len(no_speech_ranges)} no-speech ranges in one pass "
            f"({removed_duration:.1f}s removed, estimated output {estimated_duration:.1f}s)"
        )
        cut_media_ranges(
            video_path,
            str(final_video),
            keep_ranges,
            video_codec=video_codec,
            audio_codec=DEFAULT_FILTERED_AUDIO_CODEC,
            nvenc_preset=nvenc_preset,
            nvenc_cq=nvenc_cq,
            x264_crf=x264_crf,
            audio_filter=loudnorm_filter,
            video_filter=build_ass_filter(str(cut_ass_path)),
            audio_track=output_audio_track,
            progress_callback=log_progress,
        )
        return RenderResult(
            final_video=final_video,
            no_speech_report=no_speech_report,
            cut_merged_json=cut_merged_json,
            cut_ass_path=cut_ass_path,
        )

    burn_audio_codec = DEFAULT_FILTERED_AUDIO_CODEC if loudnorm_filter else audio_codec
    normalize_label = f" with audio normalization to {audio_target_lufs:g} LUFS" if loudnorm_filter else ""
    log_progress(f"Burning subtitles into {final_video.name} with {video_codec}{normalize_label}")
    run_ffmpeg_burn(
        video_path,
        str(ass_path),
        str(final_video),
        video_codec=video_codec,
        audio_codec=burn_audio_codec,
        nvenc_preset=nvenc_preset,
        nvenc_cq=nvenc_cq,
        x264_crf=x264_crf,
        audio_filter=loudnorm_filter,
        audio_track=output_audio_track,
        progress_callback=log_progress,
    )
    return RenderResult(
        final_video=final_video,
        no_speech_report=None,
        cut_merged_json=None,
        cut_ass_path=None,
    )


def log_progress(message: str) -> None:
    print(f"[craig_pipeline] {message}")


def normalize_db_threshold(value: str | float | int) -> str:
    threshold = str(value).strip()
    if threshold.lower().endswith("db"):
        return threshold
    try:
        float(threshold)
    except ValueError as exc:
        raise SystemExit("speech_threshold_db must be a number such as -40 or an FFmpeg value such as -40dB.") from exc
    return f"{threshold}dB"


def list_craig_audio_files(audio_dir: str) -> list[Path]:
    base = Path(audio_dir)
    return sorted(path for path in base.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_CRAIG_EXTENSIONS)


def resolve_craig_audio_files(audio_dir: str | None, selected_audio_files: list[str] | None = None) -> list[Path]:
    if selected_audio_files:
        files = sorted(
            {Path(path).resolve() for path in selected_audio_files},
            key=lambda path: (path.name.casefold(), str(path).casefold()),
        )
        invalid = [path for path in files if not path.is_file() or path.suffix.lower() not in SUPPORTED_CRAIG_EXTENSIONS]
        if invalid:
            raise SystemExit(f"Invalid Craig speaker audio file: {invalid[0]}")
        return files
    if audio_dir:
        return list_craig_audio_files(audio_dir)
    return []


def resolve_reference_audio_path(
    audio_files: list[Path],
    reference_audio: str | None,
    audio_dir: str | None = None,
) -> Path | None:
    if reference_audio:
        candidate = Path(reference_audio)
        if candidate.is_absolute() and candidate.exists():
            return candidate.resolve()
        match = next((path for path in audio_files if path.name == candidate.name), None)
        if match:
            return match
        if audio_dir:
            directory_candidate = Path(audio_dir) / candidate
            if directory_candidate.exists():
                return directory_candidate.resolve()
        return None
    return next((path for path in audio_files if path.name.startswith("1-")), None)


def has_craig_audio_files(path: Path) -> bool:
    return path.is_dir() and any(child.is_file() and child.suffix.lower() in SUPPORTED_CRAIG_EXTENSIONS for child in path.iterdir())


def select_single_candidate(candidates: list[Path], label: str, base_dir: Path, override_arg: str) -> Path:
    if not candidates:
        raise SystemExit(f"No {label} found in {base_dir}. Pass {override_arg} explicitly.")
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        raise SystemExit(f"Multiple {label} found in {base_dir}: {names}. Pass {override_arg} explicitly.")
    return candidates[0]


def resolve_craig_target_paths(
    target: str | None,
    video_path: str | None,
    audio_dir: str | None,
    output_dir: str | None,
    input_root: str = DEFAULT_INPUT_ROOT,
    export_root: str = DEFAULT_EXPORT_ROOT,
) -> tuple[str | None, str | None, str | None]:
    if not target:
        return video_path, audio_dir, output_dir

    target_path = Path(target)
    target_dir = target_path if target_path.exists() else Path(input_root) / target
    if target_path.is_file():
        target_dir = target_path.parent
        if video_path is None:
            video_path = str(target_path)

    if not target_dir.is_dir():
        raise SystemExit(f"Craig target directory was not found: {target_dir}")

    if video_path is None:
        video_candidates = sorted(path for path in target_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS)
        video_path = str(select_single_candidate(video_candidates, "video file", target_dir, "--video"))

    if audio_dir is None:
        audio_candidates = sorted(path for path in target_dir.iterdir() if path.name.lower().startswith("craig-") and has_craig_audio_files(path))
        audio_dir = str(select_single_candidate(audio_candidates, "Craig audio directory", target_dir, "--audio-dir"))

    if output_dir is None:
        output_dir = str(Path(export_root) / target_dir.name)

    return video_path, audio_dir, output_dir


def parse_craig_speaker_name(audio_path: str) -> str:
    stem = Path(audio_path).stem
    parts = stem.split("-", 1)
    return parts[1] if len(parts) == 2 else stem


def build_speaker_style_map(audio_files: list[Path]) -> dict[str, str]:
    ordered_files = sorted(audio_files, key=lambda path: (path.name.casefold(), str(path).casefold()))
    speaker_names = [parse_craig_speaker_name(str(path)) for path in ordered_files]
    style_map: dict[str, str] = {}
    for index, speaker_name in enumerate(speaker_names):
        if index < len(DEFAULT_STYLE_SEQUENCE):
            style_map[speaker_name] = DEFAULT_STYLE_SEQUENCE[index]
        else:
            style_map[speaker_name] = "UNKNOWN"
    return style_map


def decode_audio_samples(input_path: str, sample_rate: int = DEFAULT_ALIGNMENT_SAMPLE_RATE, stream_selector: str | None = None) -> np.ndarray:
    command = ["ffmpeg", "-v", "error", "-i", input_path]
    if stream_selector:
        command.extend(["-map", stream_selector])
    command.extend(["-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "-"])
    result = subprocess.run(command, capture_output=True, check=True)
    return np.frombuffer(result.stdout, dtype=np.float32)


def calculate_segment_volume_levels(
    audio_path: str,
    segments: list[dict],
    sample_rate: int = SUBTITLE_VOLUME_SAMPLE_RATE,
) -> list[float]:
    if not segments:
        return []
    try:
        samples = decode_audio_samples(audio_path, sample_rate=sample_rate)
    except (OSError, subprocess.CalledProcessError):
        return [0.0] * len(segments)

    loudness_db: list[float] = []
    for segment in segments:
        start_sample = max(0, round(float(segment.get("start", 0.0)) * sample_rate))
        end_sample = min(samples.size, max(start_sample + 1, round(float(segment.get("end", 0.0)) * sample_rate)))
        window = samples[start_sample:end_sample]
        if window.size == 0:
            loudness_db.append(-120.0)
            continue
        window_float = window.astype(np.float64, copy=False)
        rms = float(np.sqrt(np.mean(window_float * window_float)))
        loudness_db.append(20.0 * float(np.log10(max(rms, 1e-6))))

    median_db = float(np.median(loudness_db))
    return [
        float(np.clip((value - median_db) / SUBTITLE_VOLUME_RANGE_DB, -1.0, 1.0))
        for value in loudness_db
    ]


def prepare_alignment_signal(samples: np.ndarray) -> np.ndarray:
    if samples.size == 0:
        return samples
    signal = np.abs(samples.astype(np.float32, copy=False))
    signal = signal - float(signal.mean())
    std = float(signal.std())
    if std > 1e-6:
        signal = signal / std
    return signal


def _estimate_offset_with_reference_fft(
    reference_signal: np.ndarray,
    candidate_signal: np.ndarray,
    sample_rate: int,
    reference_fft_cache: dict[int, np.ndarray],
) -> tuple[float, float]:
    if reference_signal.size == 0 or candidate_signal.size == 0:
        raise ValueError("Alignment signals must be non-empty.")

    fft_size = 1 << (reference_signal.size + candidate_signal.size - 2).bit_length()
    reference_fft = reference_fft_cache.get(fft_size)
    if reference_fft is None:
        reference_fft = np.fft.rfft(reference_signal[::-1], fft_size)
        reference_fft_cache[fft_size] = reference_fft
    correlation = np.fft.irfft(
        np.fft.rfft(candidate_signal, fft_size) * reference_fft,
        fft_size,
    )[: reference_signal.size + candidate_signal.size - 1]
    best_index = int(np.argmax(correlation))
    lag_samples = best_index - (reference_signal.size - 1)
    peak = float(correlation[best_index])
    normalized_score = peak / max(float(reference_signal.size), 1.0)
    return lag_samples / sample_rate, normalized_score


def estimate_offset(reference_signal: np.ndarray, candidate_signal: np.ndarray, sample_rate: int) -> tuple[float, float]:
    return _estimate_offset_with_reference_fft(reference_signal, candidate_signal, sample_rate, {})


def find_best_reference_track(video_path: str, reference_audio_path: str, sample_rate: int = DEFAULT_ALIGNMENT_SAMPLE_RATE) -> tuple[str, float, float]:
    streams = probe_audio_streams(video_path)
    reference_signal = prepare_alignment_signal(decode_audio_samples(reference_audio_path, sample_rate=sample_rate))
    best_track = ""
    best_offset = 0.0
    best_score = float("-inf")
    reference_fft_cache: dict[int, np.ndarray] = {}

    for order, _stream in enumerate(streams):
        track_selector = f"0:a:{order}"
        candidate_signal = prepare_alignment_signal(decode_audio_samples(video_path, sample_rate=sample_rate, stream_selector=track_selector))
        offset_seconds, score = _estimate_offset_with_reference_fft(
            reference_signal,
            candidate_signal,
            sample_rate,
            reference_fft_cache,
        )
        if score > best_score:
            best_track = track_selector
            best_offset = offset_seconds
            best_score = score

    if not best_track:
        raise SystemExit("No audio tracks found in the video for alignment.")
    return best_track, best_offset, best_score


expected_audio_transcript_path = expected_transcript_path

def transcribe_audio_file(
    audio_path: str,
    output_dir: str,
    model: str = "large-v3",
    device: str = "cpu",
    compute_type: str = "int8",
    language: str = "ja",
    vad_onset: float | None = 0.35,
    vad_offset: float | None = 0.2,
    skip_existing: bool = True,
    *,
    hint: CraigTranscriptionHint | None = None,
) -> Path:
    result = transcribe_craig_audio_file_with_cache(
        audio_path,
        output_dir,
        model=model,
        device=device,
        compute_type=compute_type,
        language=language,
        vad_onset=vad_onset,
        vad_offset=vad_offset,
        skip_existing_transcripts=skip_existing,
        hint=hint,
    )
    return result.transcript_path


def resolve_alignment(
    video_path: str,
    reference_audio_path: str,
    reference_track: str | None,
    sample_rate: int,
) -> tuple[str, float, float]:
    if reference_track:
        candidate_signal = prepare_alignment_signal(decode_audio_samples(video_path, sample_rate=sample_rate, stream_selector=reference_track))
        reference_signal = prepare_alignment_signal(decode_audio_samples(reference_audio_path, sample_rate=sample_rate))
        offset_seconds, score = estimate_offset(reference_signal, candidate_signal, sample_rate)
        return reference_track, offset_seconds, score
    return find_best_reference_track(video_path, reference_audio_path, sample_rate=sample_rate)


@dataclass(frozen=True)
class CraigTranscriptionBatch:
    transcript_map: dict[str, str]
    segments: list[dict]


def transcribe_craig_audio_files(
    audio_files: list[Path],
    transcript_dir: Path,
    style_map: dict[str, str],
    offset_seconds: float,
    *,
    model: str = DEFAULT_MODEL,
    device: str = DEFAULT_DEVICE,
    compute_type: str = DEFAULT_COMPUTE_TYPE,
    language: str = DEFAULT_LANGUAGE,
    vad_onset: float | None = DEFAULT_VAD_ONSET,
    vad_offset: float | None = DEFAULT_VAD_OFFSET,
    skip_existing_transcripts: bool = True,
    postprocess_workers: int = DEFAULT_POSTPROCESS_WORKERS,
    subtitle_font_size: int = DEFAULT_SUBTITLE_FONT_SIZE,
    subtitle_volume_scale_percent: float = DEFAULT_SUBTITLE_VOLUME_SCALE_PERCENT,
    transcription_hints_by_audio: dict[str, CraigTranscriptionHint] | None = None,
    default_transcription_hint: CraigTranscriptionHint | None = None,
) -> CraigTranscriptionBatch:
    """Run WhisperX serially while overlapping CPU-only caption postprocessing."""
    transcript_map: dict[str, str] = {}
    segment_futures: dict[str, object] = {}
    merged_segments: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, postprocess_workers)) as executor:
        for audio_file in audio_files:
            expected_path = expected_audio_transcript_path(str(audio_file), str(transcript_dir))
            hint = resolve_craig_transcription_hint(
                audio_file,
                transcription_hints_by_audio,
                default_hint=default_transcription_hint,
            )
            if hint.cache_fingerprint:
                log_progress(f"Checking fingerprinted transcript cache for {audio_file.name}")
            elif skip_existing_transcripts and expected_path.exists():
                log_progress(f"Cache hit for {audio_file.name}; reusing {expected_path.name}")
            else:
                log_progress(f"Starting WhisperX for {audio_file.name} on {device}/{compute_type}")
            transcript_path = transcribe_audio_file(
                str(audio_file),
                str(transcript_dir),
                model=model,
                device=device,
                compute_type=compute_type,
                language=language,
                vad_onset=vad_onset,
                vad_offset=vad_offset,
                skip_existing=skip_existing_transcripts,
                hint=hint,
            )
            transcript_map[str(audio_file.resolve())] = str(transcript_path.resolve())
            segment_futures[str(audio_file)] = executor.submit(
                build_craig_segments_for_transcript,
                str(audio_file),
                str(transcript_path),
                style_map,
                offset_seconds,
                subtitle_font_size,
                subtitle_volume_scale_percent,
            )

        for audio_file in audio_files:
            built_segments = segment_futures[str(audio_file)].result()
            merged_segments.extend(built_segments)
            log_progress(
                f"Finished CPU postprocess for {audio_file.name} "
                f"({len(built_segments)} segments)"
            )
    return CraigTranscriptionBatch(transcript_map, merged_segments)


def build_craig_segments_for_transcript(
    audio_path: str,
    transcript_path: str,
    style_map: dict[str, str],
    offset_seconds: float,
    subtitle_font_size: int = DEFAULT_SUBTITLE_FONT_SIZE,
    subtitle_volume_scale_percent: float = 0.0,
) -> list[dict]:
    if subtitle_font_size < 3:
        raise ValueError("subtitle_font_size must be at least 3")
    if not 0.0 <= subtitle_volume_scale_percent <= 80.0:
        raise ValueError("subtitle_volume_scale_percent must be between 0 and 80")

    speaker_name = parse_craig_speaker_name(audio_path)
    speaker_style = style_map[speaker_name]
    data = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
    prepared_segments: list[tuple[dict, dict, str]] = []
    for segment in data.get("segments", []):
        text = segment.get("text", "").strip()
        if not text:
            continue
        shifted = shift_segment(segment, offset_seconds)
        if shifted is None:
            continue
        prepared_segments.append((segment, shifted, text))

    volume_levels = (
        calculate_segment_volume_levels(audio_path, [segment for segment, _shifted, _text in prepared_segments])
        if subtitle_volume_scale_percent > 0.0
        else [0.0] * len(prepared_segments)
    )
    segments: list[dict] = []
    for (segment, shifted, text), volume_level in zip(prepared_segments, volume_levels):
        font_scale = 1.0 + volume_level * subtitle_volume_scale_percent / 100.0
        effective_font_scale = subtitle_font_size / DEFAULT_SUBTITLE_FONT_SIZE * font_scale
        segments.append(
            {
                "start": float(shifted["start"]),
                "end": float(shifted["end"]),
                "speaker": speaker_style,
                "text": text,
                "emphasis": "shout" if is_short_reaction(text) and any(mark in text for mark in EMPHASIS_MARKERS) else segment.get("emphasis", "normal"),
                "position": "bottom",
                "layout_row": 0,
                "max_width": max(8, round(max_width_for_speaker(speaker_style) / effective_font_scale)),
                "subtitle_volume_level": volume_level,
                "subtitle_font_scale": font_scale,
                "source_track": f"craig:{speaker_name}",
                "source_speaker": speaker_name,
                "source_file": Path(audio_path).name,
                "words": shifted.get("words", []),
            }
        )
    return segments


def shift_segment(segment: dict, offset_seconds: float) -> dict | None:
    start = float(segment.get("start", 0.0)) + offset_seconds
    end = float(segment.get("end", 0.0)) + offset_seconds
    if end <= 0:
        return None
    return {
        **segment,
        "start": max(0.0, start),
        "end": max(max(0.0, start), end),
        "words": [
            {
                **word,
                "start": max(0.0, float(word["start"]) + offset_seconds) if word.get("start") is not None else word.get("start"),
                "end": max(0.0, float(word["end"]) + offset_seconds) if word.get("end") is not None else word.get("end"),
            }
            for word in segment.get("words", [])
        ],
    }


def merge_craig_transcripts(
    transcript_map: dict[str, str],
    style_map: dict[str, str],
    offset_seconds: float,
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
) -> tuple[dict, dict]:
    merged_segments: list[dict] = []
    for audio_path, transcript_path in transcript_map.items():
        merged_segments.extend(build_craig_segments_for_transcript(audio_path, transcript_path, style_map, offset_seconds))
    refined_result = run_refine_stage(
        merged_segments,
        subtitle_max_gap_seconds=subtitle_max_gap_seconds,
        subtitle_end_padding_seconds=subtitle_end_padding_seconds,
        subtitle_min_duration_seconds=subtitle_min_duration_seconds,
    )
    return {"segments": refined_result.merged_segments}, {"segments": refined_result.filtered_segments}


def write_json(path: str, payload: dict) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def run_craig_pipeline(
    video_path: str,
    audio_dir: str | None,
    output_dir: str,
    reference_audio_name: str | None = None,
    reference_track: str | None = None,
    model: str = "large-v3",
    device: str = "cpu",
    compute_type: str = "int8",
    language: str = "ja",
    vad_onset: float | None = 0.35,
    vad_offset: float | None = 0.2,
    alignment_sample_rate: int = DEFAULT_ALIGNMENT_SAMPLE_RATE,
    video_codec: str = "libx264",
    audio_codec: str = "copy",
    output_audio_track: str = DEFAULT_OUTPUT_AUDIO_TRACK,
    nvenc_preset: str = "p5",
    nvenc_cq: int = DEFAULT_NVENC_CQ,
    x264_crf: int = DEFAULT_X264_CRF,
    audio_normalize: bool = DEFAULT_AUDIO_NORMALIZE,
    audio_target_lufs: float = DEFAULT_AUDIO_TARGET_LUFS,
    audio_loudness_range: float = DEFAULT_AUDIO_LOUDNESS_RANGE,
    audio_true_peak_db: float = DEFAULT_AUDIO_TRUE_PEAK_DB,
    cut_no_speech: bool = DEFAULT_CUT_NO_SPEECH,
    no_speech_min_seconds: float = DEFAULT_NO_SPEECH_MIN_SECONDS,
    speech_padding_seconds: float = DEFAULT_SPEECH_PADDING_SECONDS,
    speech_threshold_db: str = DEFAULT_SPEECH_THRESHOLD_DB,
    speech_min_clip_seconds: float = DEFAULT_SPEECH_MIN_CLIP_SECONDS,
    skip_existing_transcripts: bool = True,
    postprocess_workers: int = DEFAULT_POSTPROCESS_WORKERS,
    track_color_map: dict[str, str] | None = None,
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
    subtitle_font_size: int = DEFAULT_SUBTITLE_FONT_SIZE,
    subtitle_volume_scale_percent: float = DEFAULT_SUBTITLE_VOLUME_SCALE_PERCENT,
    selected_audio_files: list[str] | None = None,
    alignment_offset_adjustment: float = DEFAULT_ALIGNMENT_OFFSET_ADJUSTMENT,
) -> dict[str, Path | str | float | None]:
    inputs = resolve_pipeline_inputs(
        video_path,
        audio_dir,
        output_dir,
        reference_audio_name=reference_audio_name,
        selected_audio_files=selected_audio_files,
    )
    log_progress(f"Resolving alignment from {inputs.reference_audio.name} against video audio tracks")
    alignment = run_alignment_stage(
        inputs.video_path,
        inputs.reference_audio,
        reference_track,
        alignment_sample_rate,
        alignment_offset_adjustment=alignment_offset_adjustment,
    )
    log_progress(f"Matched {alignment.matched_track} with offset {alignment.offset_seconds:.3f}s (score={alignment.score:.3f})")

    transcription = run_transcription_stage(
        inputs.audio_files,
        inputs.output_dir,
        inputs.style_map,
        alignment.offset_seconds,
        model=model,
        device=device,
        compute_type=compute_type,
        language=language,
        vad_onset=vad_onset,
        vad_offset=vad_offset,
        skip_existing_transcripts=skip_existing_transcripts,
        postprocess_workers=postprocess_workers,
        subtitle_font_size=subtitle_font_size,
        subtitle_volume_scale_percent=subtitle_volume_scale_percent,
    )
    log_progress("Refining merged subtitle segments")
    refinement = run_refine_stage(
        transcription.segments,
        subtitle_max_gap_seconds=subtitle_max_gap_seconds,
        subtitle_end_padding_seconds=subtitle_end_padding_seconds,
        subtitle_min_duration_seconds=subtitle_min_duration_seconds,
    )
    artifacts = write_segment_outputs(
        inputs.video_path,
        inputs.output_dir,
        refinement,
        track_color_map=track_color_map,
        subtitle_font_size=subtitle_font_size,
        subtitle_max_gap_seconds=subtitle_max_gap_seconds,
        subtitle_end_padding_seconds=subtitle_end_padding_seconds,
        subtitle_min_duration_seconds=subtitle_min_duration_seconds,
    )
    render = run_render_stage(
        inputs.video_path,
        inputs.output_dir,
        inputs.audio_files,
        refinement.merged_segments,
        artifacts.ass_path,
        alignment,
        track_color_map=track_color_map,
        cut_no_speech=cut_no_speech,
        no_speech_min_seconds=no_speech_min_seconds,
        speech_padding_seconds=speech_padding_seconds,
        speech_threshold_db=speech_threshold_db,
        speech_min_clip_seconds=speech_min_clip_seconds,
        video_codec=video_codec,
        audio_codec=audio_codec,
        output_audio_track=output_audio_track,
        nvenc_preset=nvenc_preset,
        nvenc_cq=nvenc_cq,
        x264_crf=x264_crf,
        audio_normalize=audio_normalize,
        audio_target_lufs=audio_target_lufs,
        audio_loudness_range=audio_loudness_range,
        audio_true_peak_db=audio_true_peak_db,
        subtitle_font_size=subtitle_font_size,
        subtitle_max_gap_seconds=subtitle_max_gap_seconds,
        subtitle_end_padding_seconds=subtitle_end_padding_seconds,
        subtitle_min_duration_seconds=subtitle_min_duration_seconds,
    )

    return {
        "reference_audio": inputs.reference_audio,
        "matched_track": alignment.matched_track,
        "offset_seconds": alignment.offset_seconds,
        "alignment_score": alignment.score,
        "merged_json": artifacts.merged_json,
        "filtered_json": artifacts.filtered_json,
        "ass_path": artifacts.ass_path,
        "final_video": render.final_video,
        "no_speech_report": render.no_speech_report,
        "cut_merged_json": render.cut_merged_json,
        "cut_ass_path": render.cut_ass_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Transcribe Craig-separated audio files, align them to a video track, and burn merged subtitles.")
    parser.add_argument("target", nargs="?", help="Target folder name under video_import, or a target directory/file path.")
    parser.add_argument("--config", help="Path to runtime JSON config.")
    parser.add_argument("--video", help="Input MKV/MP4 video path.")
    parser.add_argument("--audio-dir", help="Directory containing Craig-separated audio files such as .flac or .aac.")
    parser.add_argument("--audio-file", action="append", default=None, help="Specific Craig speaker audio file. Repeat for multiple files.")
    parser.add_argument("--output-dir", default=None, help="Output directory for transcripts, ASS, and video.")
    parser.add_argument("--input-root", default=None, help="Root directory used when resolving a target folder name.")
    parser.add_argument("--export-root", default=None, help="Root directory used for target-mode output directories.")
    parser.add_argument("--reference-audio", help="Reference .aac file name used to align against the video track.")
    parser.add_argument("--reference-track", help="Optional video audio track selector such as 0:a:1. Auto-detect when omitted.")
    parser.add_argument("--alignment-offset-adjustment", type=float, default=None, help="Seconds added to the detected alignment offset.")
    parser.add_argument("--model", default=None, help="WhisperX model name.")
    parser.add_argument("--device", default=None, help="WhisperX device.")
    parser.add_argument("--compute-type", default=None, help="WhisperX compute type.")
    parser.add_argument("--language", default=None, help="WhisperX language code.")
    parser.add_argument("--vad-onset", type=float, default=None, help="WhisperX VAD onset.")
    parser.add_argument("--vad-offset", type=float, default=None, help="WhisperX VAD offset.")
    parser.add_argument("--alignment-sample-rate", type=int, default=None, help="Resample rate used for audio alignment.")
    parser.add_argument("--video-codec", default=None, help="FFmpeg video codec such as libx264 or h264_nvenc.")
    parser.add_argument("--audio-codec", default=None, help="FFmpeg audio codec for the burned video.")
    parser.add_argument("--output-audio-track", default=None, help="Video audio track included in the final output, such as 0:a:0.")
    parser.add_argument("--nvenc-preset", default=None, help="NVENC preset used when --video-codec ends with _nvenc.")
    parser.add_argument("--nvenc-cq", type=int, default=None, help="NVENC constant quality target; lower is higher quality.")
    parser.add_argument("--x264-crf", type=int, default=None, help="libx264 constant quality target; lower is higher quality.")
    parser.add_argument("--audio-normalize", action=argparse.BooleanOptionalAction, default=None, help="Normalize final audio loudness with FFmpeg loudnorm.")
    parser.add_argument("--audio-target-lufs", type=float, default=None, help="Integrated loudness target used by loudnorm.")
    parser.add_argument("--audio-loudness-range", type=float, default=None, help="Loudness range target used by loudnorm.")
    parser.add_argument("--audio-true-peak-db", type=float, default=None, help="True peak target in dBTP used by loudnorm.")
    parser.add_argument("--cut-no-speech", action=argparse.BooleanOptionalAction, default=None, help="Cut ranges where none of the Craig speaker tracks contain speech.")
    parser.add_argument("--no-speech-min-seconds", type=float, default=None, help="Minimum no-speech duration to cut.")
    parser.add_argument("--speech-padding-seconds", type=float, default=None, help="Audio kept before and after detected speech.")
    parser.add_argument("--speech-threshold-db", type=float, default=None, help="Silence threshold in dB for Craig tracks, such as -40.")
    parser.add_argument("--speech-min-clip-seconds", type=float, default=None, help="Minimum duration of a kept video clip.")
    parser.add_argument("--skip-existing-transcripts", action=argparse.BooleanOptionalAction, default=None, help="Reuse transcript JSON when it already exists.")
    parser.add_argument("--postprocess-workers", type=int, default=None, help="CPU worker count used to postprocess completed transcripts while the next audio is transcribing.")
    parser.add_argument("--track-color", action="append", default=None, help="Per-track subtitle color like craig:speaker-a=#FFFFFF.")
    parser.add_argument("--subtitle-font-size", type=int, default=None, help="Base ASS subtitle font size.")
    parser.add_argument("--subtitle-volume-scale-percent", type=float, default=None, help="Maximum font-size change for quiet and loud speech.")
    parser.add_argument("--subtitle-max-gap-seconds", type=float, default=None, help="Split subtitles when the gap between words reaches this many seconds.")
    parser.add_argument("--subtitle-end-padding-seconds", type=float, default=None, help="Extra time to keep a subtitle after the last word ends.")
    parser.add_argument("--subtitle-min-duration-seconds", type=float, default=None, help="Minimum subtitle duration after end trimming.")
    parser.add_argument("--run", action="store_true", default=None, help="Execute transcription and subtitle burn instead of printing a plan.")
    args = parser.parse_args()

    config = load_command_runtime_config("craig_pipeline", args.config)
    target = resolve_option(args.target, config, "target")
    video_path = resolve_option(args.video, config, "video")
    audio_dir = resolve_option(args.audio_dir, config, "audio_dir")
    selected_audio_files = resolve_list_option(args.audio_file, config, "audio_file", [])
    input_root = resolve_option(args.input_root, config, "input_root", DEFAULT_INPUT_ROOT)
    export_root = resolve_option(args.export_root, config, "export_root", DEFAULT_EXPORT_ROOT)
    if target:
        output_dir = args.output_dir
        video_path, audio_dir, output_dir = resolve_craig_target_paths(target, video_path, audio_dir, output_dir, input_root=input_root, export_root=export_root)
    else:
        output_dir = resolve_option(args.output_dir, config, "output_dir", DEFAULT_OUTPUT_DIR)
    reference_audio = resolve_option(args.reference_audio, config, "reference_audio")
    reference_track = resolve_option(args.reference_track, config, "reference_track")
    alignment_offset_adjustment = float(resolve_option(args.alignment_offset_adjustment, config, "alignment_offset_adjustment", DEFAULT_ALIGNMENT_OFFSET_ADJUSTMENT))
    model = resolve_option(args.model, config, "model", DEFAULT_MODEL)
    device = resolve_option(args.device, config, "device", DEFAULT_DEVICE)
    compute_type = resolve_option(args.compute_type, config, "compute_type", DEFAULT_COMPUTE_TYPE)
    language = resolve_option(args.language, config, "language", DEFAULT_LANGUAGE)
    vad_onset = resolve_option(args.vad_onset, config, "vad_onset", DEFAULT_VAD_ONSET)
    vad_offset = resolve_option(args.vad_offset, config, "vad_offset", DEFAULT_VAD_OFFSET)
    alignment_sample_rate = int(resolve_option(args.alignment_sample_rate, config, "alignment_sample_rate", DEFAULT_ALIGNMENT_SAMPLE_RATE))
    video_codec = resolve_option(args.video_codec, config, "video_codec", DEFAULT_VIDEO_CODEC)
    audio_codec = resolve_option(args.audio_codec, config, "audio_codec", DEFAULT_AUDIO_CODEC)
    output_audio_track = resolve_option(args.output_audio_track, config, "output_audio_track", DEFAULT_OUTPUT_AUDIO_TRACK)
    nvenc_preset = resolve_option(args.nvenc_preset, config, "nvenc_preset", DEFAULT_NVENC_PRESET)
    nvenc_cq = int(resolve_option(args.nvenc_cq, config, "nvenc_cq", DEFAULT_NVENC_CQ))
    x264_crf = int(resolve_option(args.x264_crf, config, "x264_crf", DEFAULT_X264_CRF))
    audio_normalize = resolve_bool_option(args.audio_normalize, config, "audio_normalize", DEFAULT_AUDIO_NORMALIZE)
    audio_target_lufs = float(resolve_option(args.audio_target_lufs, config, "audio_target_lufs", DEFAULT_AUDIO_TARGET_LUFS))
    audio_loudness_range = float(resolve_option(args.audio_loudness_range, config, "audio_loudness_range", DEFAULT_AUDIO_LOUDNESS_RANGE))
    audio_true_peak_db = float(resolve_option(args.audio_true_peak_db, config, "audio_true_peak_db", DEFAULT_AUDIO_TRUE_PEAK_DB))
    cut_no_speech = resolve_bool_option(args.cut_no_speech, config, "cut_no_speech", DEFAULT_CUT_NO_SPEECH)
    no_speech_min_seconds = float(resolve_option(args.no_speech_min_seconds, config, "no_speech_min_seconds", DEFAULT_NO_SPEECH_MIN_SECONDS))
    speech_padding_seconds = float(resolve_option(args.speech_padding_seconds, config, "speech_padding_seconds", DEFAULT_SPEECH_PADDING_SECONDS))
    speech_threshold_db = normalize_db_threshold(resolve_option(args.speech_threshold_db, config, "speech_threshold_db", DEFAULT_SPEECH_THRESHOLD_DB))
    speech_min_clip_seconds = float(resolve_option(args.speech_min_clip_seconds, config, "speech_min_clip_seconds", DEFAULT_SPEECH_MIN_CLIP_SECONDS))
    skip_existing_transcripts = resolve_bool_option(args.skip_existing_transcripts, config, "skip_existing_transcripts", True)
    postprocess_workers = int(resolve_option(args.postprocess_workers, config, "postprocess_workers", DEFAULT_POSTPROCESS_WORKERS))
    track_color_map = parse_track_color_args(resolve_list_option(args.track_color, config, "track_color", []))
    subtitle_font_size = int(resolve_option(args.subtitle_font_size, config, "subtitle_font_size", DEFAULT_SUBTITLE_FONT_SIZE))
    subtitle_volume_scale_percent = float(resolve_option(args.subtitle_volume_scale_percent, config, "subtitle_volume_scale_percent", DEFAULT_SUBTITLE_VOLUME_SCALE_PERCENT))
    subtitle_max_gap_seconds = float(resolve_option(args.subtitle_max_gap_seconds, config, "subtitle_max_gap_seconds", DEFAULT_SUBTITLE_MAX_GAP_SECONDS))
    subtitle_end_padding_seconds = float(resolve_option(args.subtitle_end_padding_seconds, config, "subtitle_end_padding_seconds", DEFAULT_SUBTITLE_END_PADDING_SECONDS))
    subtitle_min_duration_seconds = float(resolve_option(args.subtitle_min_duration_seconds, config, "subtitle_min_duration_seconds", DEFAULT_SUBTITLE_MIN_DURATION_SECONDS))
    run = resolve_bool_option(args.run, config, "run", False)

    dependency_error = format_dependency_error(
        check_runtime_dependencies(),
        require_whisperx=run,
        device=device if run else None,
    )
    if dependency_error:
        raise SystemExit(dependency_error)

    if not video_path or (not audio_dir and not selected_audio_files):
        raise SystemExit("Use --video with --audio-dir or --audio-file, or set them in --config.")

    audio_files = resolve_craig_audio_files(audio_dir, selected_audio_files)
    style_map = build_speaker_style_map(audio_files)
    reference_audio_path = resolve_reference_audio_path(audio_files, reference_audio, audio_dir)
    if reference_audio_path is None:
        raise SystemExit("No reference audio file found. Pass --reference-audio or set reference_audio in --config.")

    matched_track, offset_seconds, score = resolve_alignment(
        video_path,
        str(reference_audio_path),
        reference_track,
        alignment_sample_rate,
    )
    offset_seconds += alignment_offset_adjustment

    if not run:
        print(f"Reference audio: {reference_audio_path.name}")
        print(f"Matched video track: {matched_track}")
        print(f"Offset seconds: {offset_seconds:.3f}")
        print(f"Alignment score: {score:.3f}")
        print("Speaker styles:")
        for speaker_name, style in style_map.items():
            print(f"  {speaker_name} -> {style}")
        print("Transcription commands will be generated under:")
        print(Path(output_dir) / "transcripts")
        print(f"Video quality: {video_codec}, NVENC CQ {nvenc_cq}, x264 CRF {x264_crf}")
        print(f"Output audio track: {output_audio_track}")
        print(f"Audio normalization: {'enabled' if audio_normalize else 'disabled'} ({audio_target_lufs:g} LUFS)")
        print(f"Cut no-speech ranges: {'enabled' if cut_no_speech else 'disabled'}")
        if cut_no_speech:
            print(f"  minimum gap: {no_speech_min_seconds:g}s, padding: {speech_padding_seconds:g}s, threshold: {speech_threshold_db}")
        print("Final outputs:")
        print(Path(output_dir) / f"{Path(video_path).stem}.craig.merged.json")
        print(Path(output_dir) / f"{Path(video_path).stem}.craig.ass")
        if cut_no_speech:
            print(Path(output_dir) / f"{Path(video_path).stem}.craig.cut.ass")
        print(Path(output_dir) / f"{Path(video_path).stem}.craig.subtitled.mp4")
        return

    result = run_craig_pipeline(
        video_path,
        audio_dir,
        output_dir,
        reference_audio_name=str(reference_audio_path),
        reference_track=reference_track,
        model=model,
        device=device,
        compute_type=compute_type,
        language=language,
        vad_onset=vad_onset,
        vad_offset=vad_offset,
        alignment_sample_rate=alignment_sample_rate,
        video_codec=video_codec,
        audio_codec=audio_codec,
        output_audio_track=output_audio_track,
        nvenc_preset=nvenc_preset,
        nvenc_cq=nvenc_cq,
        x264_crf=x264_crf,
        audio_normalize=audio_normalize,
        audio_target_lufs=audio_target_lufs,
        audio_loudness_range=audio_loudness_range,
        audio_true_peak_db=audio_true_peak_db,
        cut_no_speech=cut_no_speech,
        no_speech_min_seconds=no_speech_min_seconds,
        speech_padding_seconds=speech_padding_seconds,
        speech_threshold_db=speech_threshold_db,
        speech_min_clip_seconds=speech_min_clip_seconds,
        skip_existing_transcripts=skip_existing_transcripts,
        postprocess_workers=postprocess_workers,
        track_color_map=track_color_map,
        subtitle_font_size=subtitle_font_size,
        subtitle_volume_scale_percent=subtitle_volume_scale_percent,
        subtitle_max_gap_seconds=subtitle_max_gap_seconds,
        subtitle_end_padding_seconds=subtitle_end_padding_seconds,
        subtitle_min_duration_seconds=subtitle_min_duration_seconds,
        selected_audio_files=[str(path) for path in audio_files],
        alignment_offset_adjustment=alignment_offset_adjustment,
    )
    for key in ["reference_audio", "matched_track", "offset_seconds", "alignment_score", "merged_json", "filtered_json", "ass_path", "cut_merged_json", "cut_ass_path", "final_video", "no_speech_report"]:
        if result[key] is None:
            continue
        print(f"{key}: {result[key]}")


if __name__ == "__main__":
    main()
