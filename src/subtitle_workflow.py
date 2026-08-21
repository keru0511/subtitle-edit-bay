from __future__ import annotations

import argparse
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from dataclasses import dataclass

from .audio_mixer import active_audio_mix_channels, reconcile_audio_mix, video_track_entries
from .ass_template import (
    DEFAULT_SUBTITLE_OUTLINE_COLOR,
    DEFAULT_SUBTITLE_OUTLINE_THICKNESS,
)
from .assemble_video import build_loudnorm_filter
from .burn_subs import run_ffmpeg_burn
from .craig_pipeline import (
    DEFAULT_ALIGNMENT_SAMPLE_RATE,
    DEFAULT_AUDIO_LOUDNESS_RANGE,
    DEFAULT_AUDIO_NORMALIZE,
    DEFAULT_AUDIO_TARGET_LUFS,
    DEFAULT_AUDIO_TRUE_PEAK_DB,
    DEFAULT_COMPUTE_TYPE,
    DEFAULT_DEVICE,
    DEFAULT_FILTERED_AUDIO_CODEC,
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
    DEFAULT_NO_SPEECH_MIN_SECONDS,
    DEFAULT_OUTPUT_AUDIO_TRACK,
    DEFAULT_POSTPROCESS_WORKERS,
    DEFAULT_SPEECH_DETECT_SILENCE_SECONDS,
    DEFAULT_SPEECH_MIN_CLIP_SECONDS,
    DEFAULT_SPEECH_PADDING_SECONDS,
    DEFAULT_SPEECH_THRESHOLD_DB,
    DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
    DEFAULT_SUBTITLE_VOLUME_SCALE_PERCENT,
    DEFAULT_VAD_OFFSET,
    DEFAULT_VAD_ONSET,
    build_speaker_style_map,
    decode_audio_samples,
    normalize_db_threshold,
    parse_craig_speaker_name,
    resolve_alignment,
    resolve_craig_audio_files,
    resolve_reference_audio_path,
    transcribe_craig_audio_files,
    write_json,
)
from .merge_transcripts import refine_segments
from .pipeline import build_ass_from_data
from .processing_progress import progress_event_line
from .render_ass import parse_track_color_args
from .runtime_config import load_command_runtime_config, resolve_list_option
from .runtime_dependencies import check_runtime_dependencies, format_dependency_error
from .runtime_settings import (
    RuntimeSettings,
    configured_render_settings,
    render_runtime_options,
    settings_from_config,
    transcribe_runtime_options,
)
from .silence_cut import (
    build_no_speech_plan,
    cut_media_ranges,
    detect_speech_ranges,
    probe_media_duration,
    retime_segments_for_keep_ranges,
)
from .short_video import render_short_video
from .short_video_ass import build_short_video_ass
from .subtitle_project import (
    DEFAULT_WAVEFORM_SAMPLE_RATE,
    build_waveform,
    create_project,
    derive_ass_path,
    derive_project_path,
    derive_render_path,
    derive_short_render_path,
    load_project,
    project_to_transcript,
    save_project,
)
from .subtitle_workflow_transcription import transcribe_to_project_with_context
from .transcribe import probe_audio_streams
from .transcription_context_config import transcription_context_from_runtime_config
from .video_encoding import DEFAULT_NVENC_CQ, DEFAULT_X264_CRF


DEFAULT_SPEAKER_COLORS = ["#FFD966", "#F6B26B", "#93C47D", "#6FA8DC", "#E78284", "#81C8BE"]


@dataclass(frozen=True)
class SubtitlePipelineInputs:
    video_path: str
    output_dir: Path
    project_path: Path
    reference_path: Path
    audio_files: list[Path]
    style_map: dict[str, str]
    speakers: list[dict[str, Any]]


@dataclass(frozen=True)
class SubtitleAlignmentResult:
    matched_track: str
    offset_seconds: float
    score: float
    reference_audio: str


@dataclass(frozen=True)
class SubtitleRefineResult:
    merged_segments: list[dict]
    filtered_segments: list[dict]


@dataclass(frozen=True)
class SubtitleTranscriptionResult:
    transcript_map: dict[str, str]
    segments: list[dict]


@dataclass(frozen=True)
class SubtitleProjectResult:
    project_path: Path
    merged_path: Path
    filtered_path: Path
    project: dict[str, Any]


def resolve_subtitle_inputs(
    *,
    video_path: str,
    audio_files: list[str],
    output_dir: str,
    reference_audio: str | None = None,
    reference_track: str | None = None,
) -> SubtitlePipelineInputs:
    resolved_audio = resolve_craig_audio_files(None, audio_files)
    if not resolved_audio:
        raise SystemExit("No Craig speaker audio files were selected.")
    reference_path = resolve_reference_audio_path(resolved_audio, reference_audio)
    if reference_path is None:
        reference_path = resolved_audio[0]

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    project_path = derive_project_path(video_path, output)
    style_map = build_speaker_style_map(resolved_audio)
    speakers = _project_speakers(resolved_audio, style_map, {})
    return SubtitlePipelineInputs(
        video_path=video_path,
        output_dir=output,
        project_path=project_path,
        reference_path=reference_path,
        audio_files=resolved_audio,
        style_map=style_map,
        speakers=speakers,
    )


def run_subtitle_alignment_stage(
    video_path: str,
    reference_path: Path,
    reference_track: str | None,
    alignment_sample_rate: int,
) -> SubtitleAlignmentResult:
    matched_track, offset_seconds, score = resolve_alignment(
        video_path,
        str(reference_path),
        reference_track,
        alignment_sample_rate,
    )
    return SubtitleAlignmentResult(
        matched_track=matched_track,
        offset_seconds=offset_seconds,
        score=score,
        reference_audio=str(reference_path),
    )


def run_subtitle_transcription_stage(
    audio_files: list[Path],
    transcript_dir: Path,
    style_map: dict[str, str],
    offset_seconds: float,
    *,
    model: str,
    device: str,
    compute_type: str,
    language: str,
    vad_onset: float | None,
    vad_offset: float | None,
    skip_existing_transcripts: bool,
    postprocess_workers: int,
    subtitle_font_size: int,
    subtitle_volume_scale_percent: float,
) -> SubtitleTranscriptionResult:
    result = transcribe_craig_audio_files(
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
    return SubtitleTranscriptionResult(
        transcript_map=result.transcript_map,
        segments=result.segments,
    )


def run_subtitle_refine_stage(
    merged_segments: list[dict],
    *,
    subtitle_max_gap_seconds: float,
    subtitle_end_padding_seconds: float,
    subtitle_min_duration_seconds: float,
) -> SubtitleRefineResult:
    refined, filtered = refine_segments(
        merged_segments,
        subtitle_max_gap_seconds=subtitle_max_gap_seconds,
        subtitle_end_padding_seconds=subtitle_end_padding_seconds,
        subtitle_min_duration_seconds=subtitle_min_duration_seconds,
    )
    return SubtitleRefineResult(
        merged_segments=refined,
        filtered_segments=filtered,
    )


def build_project_stage(
    inputs: SubtitlePipelineInputs,
    alignment: SubtitleAlignmentResult,
    transcript_map: dict[str, str],
    refine_result: SubtitleRefineResult,
    waveforms: list[dict[str, Any]],
    *,
    model: str,
    device: str,
    compute_type: str,
    language: str,
    subtitle_font_size: int,
    subtitle_outline_color: str,
    subtitle_outline_thickness: int,
    subtitle_max_gap_seconds: float,
    subtitle_end_padding_seconds: float,
    subtitle_min_duration_seconds: float,
    volume_scale_percent: float,
    duration_seconds: float,
    render_settings: dict[str, Any] | None = None,
) -> SubtitleProjectResult:
    merged_path = write_json(str(inputs.output_dir / f"{Path(inputs.video_path).stem}.craig.merged.json"), {"segments": refine_result.merged_segments})
    filtered_path = write_json(str(inputs.output_dir / f"{Path(inputs.video_path).stem}.craig.filtered.json"), {"segments": refine_result.filtered_segments})
    project = create_project(
        video_path=inputs.video_path,
        output_dir=inputs.output_dir,
        duration_seconds=duration_seconds,
        segments=refine_result.merged_segments,
        audio_sources=inputs.speakers,
        speakers=inputs.speakers,
        waveforms=waveforms,
        subtitle_settings={
            "font_size": subtitle_font_size,
            "outline_color": subtitle_outline_color,
            "outline_thickness": subtitle_outline_thickness,
            "volume_scale_percent": volume_scale_percent,
            "max_gap_seconds": subtitle_max_gap_seconds,
            "end_padding_seconds": subtitle_end_padding_seconds,
            "min_duration_seconds": subtitle_min_duration_seconds,
        },
        render_settings=render_settings,
        transcription={
            "model": model,
            "device": device,
            "compute_type": compute_type,
            "language": language,
            "reference_audio": str(alignment.reference_audio),
            "matched_track": alignment.matched_track,
            "offset_seconds": alignment.offset_seconds,
            "alignment_score": alignment.score,
            "transcripts": transcript_map,
            "merged_json": str(merged_path.resolve()),
            "filtered_json": str(filtered_path.resolve()),
        },
    )
    try:
        video_tracks = video_track_entries(probe_audio_streams(inputs.video_path))
    except (OSError, subprocess.SubprocessError, ValueError):
        video_tracks = None
    reconcile_audio_mix(project, video_tracks)
    save_project(inputs.project_path, project)
    log_progress(f"Project ready: {inputs.project_path}")
    return SubtitleProjectResult(
        project_path=inputs.project_path,
        merged_path=merged_path,
        filtered_path=filtered_path,
        project=project,
    )


def log_progress(message: str) -> None:
    print(f"[subtitle_workflow] {message}", flush=True)


def emit_progress_event(
    job: str,
    step: str,
    *,
    phase: str = "progress",
    progress: float = 0.0,
    duration: float | None = None,
) -> None:
    """Emit a stable, path-free progress protocol line for the GUI."""
    print(
        progress_event_line(job, step, phase=phase, progress=progress, duration=duration),
        flush=True,
    )


def _project_speakers(
    audio_files: list[Path],
    style_map: dict[str, str],
    track_color_map: dict[str, str],
) -> list[dict[str, Any]]:
    speakers: list[dict[str, Any]] = []
    for index, audio_file in enumerate(audio_files):
        name = parse_craig_speaker_name(str(audio_file))
        track_key = f"craig:{name}"
        speakers.append(
            {
                "name": name,
                "style": style_map[name],
                "track_key": track_key,
                "file_name": audio_file.name,
                "path": str(audio_file.resolve()),
                "color": track_color_map.get(track_key, DEFAULT_SPEAKER_COLORS[index % len(DEFAULT_SPEAKER_COLORS)]).upper(),
            }
        )
    return speakers


def _build_waveforms(
    audio_files: list[Path],
    speakers: list[dict[str, Any]],
    offset_seconds: float,
) -> list[dict[str, Any]]:
    waveforms: list[dict[str, Any]] = []
    speaker_by_path = {str(Path(item["path"]).resolve()): item for item in speakers}
    for audio_file in audio_files:
        speaker = speaker_by_path[str(audio_file.resolve())]
        log_progress(f"Building waveform for {audio_file.name}")
        try:
            samples = decode_audio_samples(str(audio_file), sample_rate=DEFAULT_WAVEFORM_SAMPLE_RATE)
        except (OSError, subprocess.CalledProcessError):
            continue
        waveforms.append(
            build_waveform(
                audio_file,
                speaker=speaker["name"],
                style=speaker["style"],
                color=speaker["color"],
                offset_seconds=offset_seconds,
                samples=samples,
            )
        )
    return waveforms


def transcribe_to_project(
    *,
    video_path: str,
    audio_files: list[str],
    output_dir: str,
    reference_audio: str | None = None,
    reference_track: str | None = None,
    alignment_offset_adjustment: float = 0.0,
    alignment_sample_rate: int = DEFAULT_ALIGNMENT_SAMPLE_RATE,
    model: str = DEFAULT_MODEL,
    device: str = DEFAULT_DEVICE,
    compute_type: str = DEFAULT_COMPUTE_TYPE,
    language: str = DEFAULT_LANGUAGE,
    vad_onset: float = DEFAULT_VAD_ONSET,
    vad_offset: float = DEFAULT_VAD_OFFSET,
    skip_existing_transcripts: bool = True,
    postprocess_workers: int = DEFAULT_POSTPROCESS_WORKERS,
    track_color_map: dict[str, str] | None = None,
    subtitle_font_size: int = 50,
    subtitle_outline_color: str = DEFAULT_SUBTITLE_OUTLINE_COLOR,
    subtitle_outline_thickness: int = DEFAULT_SUBTITLE_OUTLINE_THICKNESS,
    subtitle_volume_scale_percent: float = DEFAULT_SUBTITLE_VOLUME_SCALE_PERCENT,
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
    render_settings: dict[str, Any] | None = None,
    overwrite_project: bool = False,
) -> Path:
    inputs = resolve_subtitle_inputs(
        video_path=video_path,
        audio_files=audio_files,
        output_dir=output_dir,
        reference_audio=reference_audio,
        reference_track=reference_track,
    )
    if inputs.project_path.exists() and not overwrite_project:
        raise SystemExit(
            f"Editable project already exists: {inputs.project_path}. "
            "Move it or pass --overwrite-project to replace it explicitly."
        )

    colors = dict(track_color_map or {})
    speakers = _project_speakers(inputs.audio_files, inputs.style_map, colors)
    inputs = SubtitlePipelineInputs(
        video_path=inputs.video_path,
        output_dir=inputs.output_dir,
        project_path=inputs.project_path,
        reference_path=inputs.reference_path,
        audio_files=inputs.audio_files,
        style_map=inputs.style_map,
        speakers=speakers,
    )
    emit_progress_event("transcribe", "prepare", phase="complete", progress=1.0)
    emit_progress_event("transcribe", "alignment", phase="start")
    log_progress(f"Resolving alignment from {inputs.reference_path.name}")
    alignment = run_subtitle_alignment_stage(
        inputs.video_path,
        inputs.reference_path,
        reference_track,
        alignment_sample_rate,
    )
    alignment = SubtitleAlignmentResult(
        matched_track=alignment.matched_track,
        offset_seconds=alignment.offset_seconds + alignment_offset_adjustment,
        score=alignment.score,
        reference_audio=alignment.reference_audio,
    )
    emit_progress_event("transcribe", "alignment", phase="complete", progress=1.0)
    log_progress(f"Alignment ready at {alignment.offset_seconds:+.3f}s on {alignment.matched_track}")

    transcript_dir = inputs.output_dir / "transcripts"
    with ThreadPoolExecutor(max_workers=1) as waveform_executor:
        emit_progress_event("transcribe", "transcription", phase="start")
        waveform_future = waveform_executor.submit(
            _build_waveforms,
            inputs.audio_files,
            inputs.speakers,
            alignment.offset_seconds,
        )
        transcription_result = run_subtitle_transcription_stage(
            inputs.audio_files,
            transcript_dir,
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
        emit_progress_event("transcribe", "transcription", phase="complete", progress=1.0)
        emit_progress_event("transcribe", "refine", phase="start")
        log_progress("Refining merged subtitle segments")
        refine_result = run_subtitle_refine_stage(
            transcription_result.segments,
            subtitle_max_gap_seconds=subtitle_max_gap_seconds,
            subtitle_end_padding_seconds=subtitle_end_padding_seconds,
            subtitle_min_duration_seconds=subtitle_min_duration_seconds,
        )
        emit_progress_event("transcribe", "refine", phase="complete", progress=1.0)
        emit_progress_event("transcribe", "waveform", phase="start")
        waveforms = waveform_future.result()
        emit_progress_event("transcribe", "waveform", phase="complete", progress=1.0)

    emit_progress_event("transcribe", "project", phase="start")
    try:
        duration_seconds = probe_media_duration(video_path)
    except (OSError, subprocess.CalledProcessError, ValueError):
        duration_seconds = max((float(segment["end"]) for segment in refine_result.merged_segments), default=0.0)

    project_result = build_project_stage(
        inputs=inputs,
        alignment=alignment,
        transcript_map=transcription_result.transcript_map,
        refine_result=refine_result,
        waveforms=waveforms,
        model=model,
        device=device,
        compute_type=compute_type,
        language=language,
        subtitle_font_size=subtitle_font_size,
        subtitle_outline_color=subtitle_outline_color,
        subtitle_outline_thickness=subtitle_outline_thickness,
        subtitle_max_gap_seconds=subtitle_max_gap_seconds,
        subtitle_end_padding_seconds=subtitle_end_padding_seconds,
        subtitle_min_duration_seconds=subtitle_min_duration_seconds,
        volume_scale_percent=subtitle_volume_scale_percent,
        duration_seconds=duration_seconds,
        render_settings=render_settings,
    )
    emit_progress_event("transcribe", "project", phase="complete", progress=1.0)
    return project_result.project_path


def _ass_build_options(
    project: dict[str, Any],
    subtitle_font_size: int | None = None,
) -> dict[str, Any]:
    settings = project.get("subtitle_settings", {})
    colors = {
        str(item.get("track_key", "")): str(item.get("color", ""))
        for item in project.get("speakers", [])
        if item.get("track_key") and item.get("color")
    }
    return {
        "track_color_map": colors,
        "subtitle_font_size": int(subtitle_font_size or settings.get("font_size", 50)),
        "subtitle_outline_color": str(settings.get("outline_color", DEFAULT_SUBTITLE_OUTLINE_COLOR)),
        "subtitle_outline_thickness": int(settings.get("outline_thickness", DEFAULT_SUBTITLE_OUTLINE_THICKNESS)),
        "subtitle_max_gap_seconds": float(settings.get("max_gap_seconds", DEFAULT_SUBTITLE_MAX_GAP_SECONDS)),
        "subtitle_end_padding_seconds": float(settings.get("end_padding_seconds", DEFAULT_SUBTITLE_END_PADDING_SECONDS)),
        "subtitle_min_duration_seconds": float(settings.get("min_duration_seconds", DEFAULT_SUBTITLE_MIN_DURATION_SECONDS)),
    }


def _is_mp4_output(output_path: str | Path) -> bool:
    return Path(output_path).suffix.lower() in {".mp4", ".m4v", ".mov"}


def build_project_ass(
    project_path: str | Path,
    output_path: str | Path | None = None,
    *,
    subtitle_font_size: int | None = None,
    _project: dict[str, Any] | None = None,
) -> Path:
    project = _project if _project is not None else load_project(project_path)
    output = Path(output_path) if output_path else derive_ass_path(project_path)
    build_ass_from_data(
        project_to_transcript(project, project_is_validated=True),
        str(output),
        **_ass_build_options(project, subtitle_font_size),
    )
    log_progress(f"ASS preview ready: {output}")
    return output


def render_project_video(
    project_path: str | Path,
    output_path: str | Path | None = None,
    *,
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
    cut_no_speech: bool = False,
    no_speech_min_seconds: float = DEFAULT_NO_SPEECH_MIN_SECONDS,
    speech_padding_seconds: float = DEFAULT_SPEECH_PADDING_SECONDS,
    speech_threshold_db: str = DEFAULT_SPEECH_THRESHOLD_DB,
    speech_min_clip_seconds: float = DEFAULT_SPEECH_MIN_CLIP_SECONDS,
) -> Path:
    project = load_project(project_path)
    video_path = str(project["video"]["path"])
    if not Path(video_path).is_file():
        raise SystemExit(f"Project video was not found: {video_path}")
    emit_progress_event("render", "prepare", phase="complete", progress=1.0)
    emit_progress_event("render", "subtitle", phase="start")
    has_subtitles = any(
        isinstance(segment, dict) and str(segment.get("text", "")).strip()
        for segment in project.get("segments", [])
    )
    ass_path = build_project_ass(project_path, _project=project) if has_subtitles else None
    emit_progress_event("render", "subtitle", phase="complete", progress=1.0)
    emit_progress_event("render", "audio", phase="start")
    output = Path(output_path) if output_path else derive_render_path(project_path)
    loudnorm_filter = build_loudnorm_filter(audio_target_lufs, audio_loudness_range, audio_true_peak_db) if audio_normalize else None
    audio_mix = project.get("audio_mix", {})
    use_audio_mix = bool(audio_mix.get("customized", False))
    offset_seconds = float(project.get("transcription", {}).get("offset_seconds", 0.0))
    if use_audio_mix:
        for channel in active_audio_mix_channels(audio_mix):
            if channel.get("kind") == "external" and not Path(str(channel.get("path", ""))).is_file():
                raise SystemExit(f"Mixer audio source was not found: {channel.get('path', '')}")
    has_audio_stream = True
    if not use_audio_mix:
        try:
            probed_audio_streams = probe_audio_streams(video_path)
            has_audio_stream = bool(probed_audio_streams)
            actual_video_track_selectors = {
                entry["selector"] for entry in video_track_entries(probed_audio_streams)
            }
            has_known_video_tracks = True
        except (OSError, subprocess.CalledProcessError, ValueError):
            has_known_video_tracks = False
            actual_video_track_selectors = set()
            has_audio_stream = True
        has_real_video_track = any(
            isinstance(channel, dict)
            and channel.get("kind") == "video"
            and str(channel.get("selector", "")).strip()
            and (
                has_known_video_tracks
                and str(channel.get("selector", "")) in actual_video_track_selectors
            )
            for channel in (audio_mix.get("channels") if isinstance(audio_mix.get("channels"), list) else [])
        )
        has_enabled_external = any(
            isinstance(channel, dict)
            and channel.get("kind") == "external"
            and bool(channel.get("enabled"))
            for channel in audio_mix.get("channels", [])
        )
        if has_enabled_external:
            use_audio_mix = True
        elif not has_real_video_track:
            for channel in audio_mix.get("channels", []):
                if (
                    isinstance(channel, dict)
                    and channel.get("kind") == "external"
                    and Path(str(channel.get("path", ""))).is_file()
                ):
                    channel["enabled"] = True
                    use_audio_mix = True
                    break

    cut_output: Path | None = None
    if cut_no_speech:
        source_paths = [
            str(source.get("path", ""))
            for source in project.get("audio_sources", [])
            if Path(str(source.get("path", ""))).is_file()
        ]
        detection_sources: list[tuple[str, str | None]] = [(path, None) for path in source_paths]
        if not detection_sources:
            try:
                probed_audio_streams = probe_audio_streams(video_path)
            except (OSError, subprocess.CalledProcessError, ValueError):
                probed_audio_streams = []
            available_video_tracks = {
                entry["selector"] for entry in video_track_entries(probed_audio_streams)
            }
            selected_video_track = next(
                (
                    str(channel.get("selector"))
                    for channel in active_audio_mix_channels(audio_mix)
                    if channel.get("kind") == "video"
                    and str(channel.get("selector", "")) in available_video_tracks
                ),
                "",
            )
            if not selected_video_track:
                candidate = str(output_audio_track or "")
                selected_video_track = (
                    candidate
                    if candidate in available_video_tracks
                    else next(iter(available_video_tracks), "")
                )
            if selected_video_track:
                detection_sources = [(video_path, selected_video_track)]

        if not detection_sources:
            log_progress("No audio source is available for silence detection; disabling silence cut")
            cut_no_speech = False

    if cut_no_speech:
        speech_ranges: list[tuple[float, float]] = []
        for source_path, _audio_track in detection_sources:
            log_progress(f"Detecting speech in {Path(source_path).name}")

        def detect_source(source: tuple[str, str | None]) -> list[tuple[float, float]]:
            source_path, audio_track = source
            options: dict[str, Any] = {
                "noise": normalize_db_threshold(speech_threshold_db),
                "duration": DEFAULT_SPEECH_DETECT_SILENCE_SECONDS,
            }
            if audio_track:
                options["audio_track"] = audio_track
            return detect_speech_ranges(source_path, **options)

        with ThreadPoolExecutor(max_workers=max(1, min(4, len(detection_sources)))) as executor:
            for source_ranges in executor.map(detect_source, detection_sources):
                speech_ranges.extend(source_ranges)
        duration = float(project.get("video", {}).get("duration_seconds", 0.0)) or probe_media_duration(video_path)
        no_speech_ranges, keep_ranges = build_no_speech_plan(
            duration,
            speech_ranges,
            offset_seconds,
            min_no_speech_seconds=no_speech_min_seconds,
            padding=speech_padding_seconds,
            min_clip_duration=speech_min_clip_seconds,
        )
        if not keep_ranges:
            raise SystemExit("No speech activity was detected; refusing to cut the entire video.")
        if has_subtitles:
            cut_ass = Path(project_path).with_name(f".{Path(project_path).stem}.cut.ass")
            transcript = project_to_transcript(project, project_is_validated=True)
            cut_transcript = {"segments": retime_segments_for_keep_ranges(transcript["segments"], keep_ranges)}
            try:
                build_ass_from_data(
                    cut_transcript,
                    str(cut_ass),
                    **_ass_build_options(project),
                )
                cut_output = output.with_name(f"{output.stem}.silence-cut{output.suffix or '.mp4'}")
                log_progress(f"Cutting {len(no_speech_ranges)} silent ranges to {cut_output.name}")
                cut_media_ranges(
                    video_path,
                    str(cut_output),
                    keep_ranges,
                    video_codec=video_codec,
                    audio_codec=DEFAULT_FILTERED_AUDIO_CODEC,
                    nvenc_preset=nvenc_preset,
                    nvenc_cq=nvenc_cq,
                    x264_crf=x264_crf,
                    audio_filter=loudnorm_filter,
                    audio_track=output_audio_track,
                    audio_mix=audio_mix if use_audio_mix else None,
                    audio_offset_seconds=offset_seconds,
                    progress_callback=log_progress,
                )
                emit_progress_event("render", "audio", phase="complete", progress=1.0)
                emit_progress_event(
                    "render",
                    "encode",
                    phase="metadata",
                    duration=sum(max(0.0, end - start) for start, end in keep_ranges),
                )
                emit_progress_event("render", "encode", phase="start")
                log_progress(f"Rendering edited subtitles to {output.name}")
                run_ffmpeg_burn(
                    str(cut_output),
                    str(cut_ass),
                    str(output),
                    video_codec=video_codec,
                    audio_codec="copy",
                    nvenc_preset=nvenc_preset,
                    nvenc_cq=nvenc_cq,
                    x264_crf=x264_crf,
                    progress_callback=log_progress,
                )
            finally:
                cut_ass.unlink(missing_ok=True)
        else:
            cut_output = output
            emit_progress_event("render", "audio", phase="complete", progress=1.0)
            emit_progress_event(
                "render",
                "encode",
                phase="metadata",
                duration=sum(max(0.0, end - start) for start, end in keep_ranges),
            )
            emit_progress_event("render", "encode", phase="start")
            log_progress(f"Cutting {len(no_speech_ranges)} silent ranges to {output.name}")
            cut_media_ranges(
                video_path,
                str(output),
                keep_ranges,
                video_codec=video_codec,
                audio_codec=DEFAULT_FILTERED_AUDIO_CODEC,
                nvenc_preset=nvenc_preset,
                nvenc_cq=nvenc_cq,
                x264_crf=x264_crf,
                audio_filter=loudnorm_filter,
                audio_track=output_audio_track,
                audio_mix=audio_mix if use_audio_mix else None,
                audio_offset_seconds=offset_seconds,
                progress_callback=log_progress,
            )
    else:
        emit_progress_event("render", "audio", phase="complete", progress=1.0)
        output_duration = float(project.get("video", {}).get("duration_seconds", 0.0))
        if output_duration > 0.0:
            emit_progress_event("render", "encode", phase="metadata", duration=output_duration)
        emit_progress_event("render", "encode", phase="start")
        log_progress(f"Rendering edited subtitles to {output.name}")
        burn_audio_codec = DEFAULT_FILTERED_AUDIO_CODEC if loudnorm_filter or use_audio_mix else audio_codec
        if _is_mp4_output(output) and burn_audio_codec == "copy":
            burn_audio_codec = DEFAULT_FILTERED_AUDIO_CODEC
        run_ffmpeg_burn(
            video_path,
            str(ass_path) if ass_path is not None else None,
            str(output),
            video_codec=video_codec,
            audio_codec=burn_audio_codec,
            nvenc_preset=nvenc_preset,
            nvenc_cq=nvenc_cq,
            x264_crf=x264_crf,
            audio_filter=loudnorm_filter,
            audio_track=output_audio_track,
            audio_mix=audio_mix if use_audio_mix else None,
            audio_offset_seconds=offset_seconds,
            include_audio=has_audio_stream,
            progress_callback=log_progress,
        )
    emit_progress_event("render", "encode", phase="complete", progress=1.0)
    emit_progress_event("render", "finalize", phase="start")
    project["render_settings"] = {
        **project.get("render_settings", {}),
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "output_audio_track": output_audio_track,
        "audio_normalize": audio_normalize,
        "audio_target_lufs": audio_target_lufs,
        "cut_no_speech": cut_no_speech,
        "no_speech_min_seconds": no_speech_min_seconds,
        "speech_padding_seconds": speech_padding_seconds,
        "speech_threshold_db": speech_threshold_db,
        "speech_min_clip_seconds": speech_min_clip_seconds,
        "last_output": str(output.resolve()),
    }
    if cut_output is not None:
        project["render_settings"]["last_cut_output"] = str(cut_output.resolve())
    save_project(project_path, project)
    emit_progress_event("render", "finalize", phase="complete", progress=1.0)
    log_progress(f"Render complete: {output}")
    return output


def render_project_short_video(
    project_path: str | Path,
    output_path: str | Path | None = None,
    *,
    video_codec: str = "libx264",
    audio_codec: str = "aac",
    nvenc_preset: str = "p5",
    nvenc_cq: int = DEFAULT_NVENC_CQ,
    x264_crf: int = DEFAULT_X264_CRF,
    progress_callback: Callable[[str], None] | None = None,
    **_kwargs: Any,
) -> Path:
    emit_progress_event("render_short", "prepare", phase="complete", progress=1.0)
    project = load_project(project_path)
    short_video = project.get("short_video", {})
    if not short_video.get("enabled") or not short_video.get("clips"):
        raise SystemExit("short_video is not enabled or has no clips")

    video_path = str(project.get("video", {}).get("path", ""))
    if not video_path or not Path(video_path).is_file():
        raise SystemExit(f"Project video was not found: {video_path}")

    output = Path(output_path) if output_path else derive_short_render_path(project_path)
    if audio_codec == "copy":
        audio_codec = "aac"
    has_subtitles = any(
        isinstance(segment, dict) and str(segment.get("text", "")).strip()
        for segment in project.get("segments", [])
    )
    ass_path = build_short_video_ass(project_path, _project=project) if has_subtitles else None
    result = render_short_video(
        project_path,
        output,
        video_codec=video_codec,
        audio_codec=audio_codec,
        nvenc_preset=nvenc_preset,
        nvenc_cq=nvenc_cq,
        x264_crf=x264_crf,
        ass_path=ass_path,
        progress_callback=progress_callback,
        _project=project,
    )
    emit_progress_event("render_short", "encode", phase="complete", progress=1.0)
    emit_progress_event("render_short", "finalize", phase="start")
    render_settings = {
        **project.get("render_settings", {}),
        "short_video_codec": video_codec,
        "short_audio_codec": audio_codec,
        "short_last_output": str(result.resolve()),
    }
    if ass_path is not None:
        render_settings["short_last_ass"] = str(ass_path.resolve())
    else:
        render_settings.pop("short_last_ass", None)
    project["render_settings"] = render_settings
    save_project(project_path, project)
    emit_progress_event("render_short", "finalize", phase="complete", progress=1.0)
    log_progress(f"Short render complete: {result}")
    return result


def _add_shared_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="Runtime JSON config path.")


def _transcribe_options_with_cli_overrides(
    settings: RuntimeSettings,
    *,
    alignment_offset_adjustment: float | None,
    skip_existing_transcripts: bool | None,
) -> dict[str, Any]:
    options = transcribe_runtime_options(settings)
    if alignment_offset_adjustment is not None:
        options["alignment_offset_adjustment"] = alignment_offset_adjustment
    if skip_existing_transcripts is not None:
        options["skip_existing_transcripts"] = skip_existing_transcripts
    return options


def main() -> None:
    parser = argparse.ArgumentParser(description="Editable subtitle project workflow.")
    subparsers = parser.add_subparsers(dest="phase", required=True)

    transcribe = subparsers.add_parser("transcribe", help="Transcribe sources and create an editable project.")
    _add_shared_options(transcribe)
    transcribe.add_argument("--video", required=True)
    transcribe.add_argument("--audio-file", action="append")
    transcribe.add_argument("--video-audio-track")
    transcribe.add_argument("--output-dir", required=True)
    transcribe.add_argument("--project-path", help="Explicit editable project output path.")
    transcribe.add_argument("--reference-audio")
    transcribe.add_argument("--reference-track")
    transcribe.add_argument("--alignment-offset-adjustment", type=float, default=None)
    transcribe.add_argument("--skip-existing-transcripts", action=argparse.BooleanOptionalAction, default=None)
    transcribe.add_argument("--transcription-context-file", help="Path to a transcription context JSON file.")
    transcribe.add_argument("--overwrite-project", action="store_true", help="Explicitly replace an existing editable project.")
    transcribe.add_argument("--run", action="store_true")

    ass = subparsers.add_parser("ass", help="Generate ASS from an edited project.")
    _add_shared_options(ass)
    ass.add_argument("--project", required=True)
    ass.add_argument("--output")
    ass.add_argument("--subtitle-font-size", type=int)

    render = subparsers.add_parser("render", help="Burn an edited project into video.")
    _add_shared_options(render)
    render.add_argument("--project", required=True)
    render.add_argument("--output")
    render.add_argument("--run", action="store_true")

    render_short = subparsers.add_parser("render-short", help="Render a 9:16 short video from project clips.")
    _add_shared_options(render_short)
    render_short.add_argument("--project", required=True)
    render_short.add_argument("--output")
    render_short.add_argument("--run", action="store_true")

    args = parser.parse_args()
    config = load_command_runtime_config("craig_pipeline", args.config)

    if args.phase == "transcribe":
        if not args.run:
            print(derive_project_path(args.video, args.output_dir))
            return
        settings = settings_from_config(config)
        transcribe_options = _transcribe_options_with_cli_overrides(
            settings,
            alignment_offset_adjustment=args.alignment_offset_adjustment,
            skip_existing_transcripts=args.skip_existing_transcripts,
        )
        device = str(transcribe_options["device"])
        dependency_error = format_dependency_error(
            check_runtime_dependencies(),
            require_whisperx=True,
            device=device,
        )
        if dependency_error:
            raise SystemExit(dependency_error)
        track_colors = parse_track_color_args(resolve_list_option(None, config, "track_color", []))
        context_base_dir = Path(args.config).resolve().parent if args.config else Path.cwd()
        transcription_context = transcription_context_from_runtime_config(
            config,
            cli_context_file=args.transcription_context_file,
            base_dir=context_base_dir,
        )
        transcribe_options.update(
            {
                "postprocess_workers": int(config.get("postprocess_workers", DEFAULT_POSTPROCESS_WORKERS)),
                "track_color_map": track_colors,
                "render_settings": configured_render_settings(settings, config),
                "overwrite_project": args.overwrite_project,
            }
        )
        result = transcribe_to_project_with_context(
            video_path=args.video,
            audio_files=args.audio_file or [],
            output_dir=args.output_dir,
            project_path=args.project_path,
            reference_audio=args.reference_audio,
            reference_track=args.reference_track,
            video_audio_track=args.video_audio_track,
            transcription_context=transcription_context,
            **transcribe_options,
        )
        print(f"project_path: {result}")
        return

    if args.phase == "ass":
        result = build_project_ass(args.project, args.output, subtitle_font_size=args.subtitle_font_size)
        print(f"ass_path: {result}")
        return

    if args.phase == "render-short":
        if not args.run:
            print(Path(args.output) if args.output else derive_short_render_path(args.project))
            return
        settings = settings_from_config(config)
        dependency_error = format_dependency_error(check_runtime_dependencies(), require_whisperx=False)
        if dependency_error:
            raise SystemExit(dependency_error)
        render_options = render_runtime_options(settings)
        render_options["speech_threshold_db"] = normalize_db_threshold(render_options["speech_threshold_db"])
        result = render_project_short_video(args.project, args.output, **render_options)
        print(f"final_video: {result}")
        return

    if not args.run:
        print(Path(args.output) if args.output else derive_render_path(args.project))
        return
    settings = settings_from_config(config)
    dependency_error = format_dependency_error(check_runtime_dependencies(), require_whisperx=False)
    if dependency_error:
        raise SystemExit(dependency_error)
    render_options = render_runtime_options(settings)
    render_options["speech_threshold_db"] = normalize_db_threshold(render_options["speech_threshold_db"])
    result = render_project_video(args.project, args.output, **render_options)
    print(f"final_video: {result}")


if __name__ == "__main__":
    main()
