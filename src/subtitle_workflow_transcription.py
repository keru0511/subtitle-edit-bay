from __future__ import annotations

import hashlib
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping

from .audio_mixer import reconcile_audio_mix, video_track_entries
from .ass_template import (
    DEFAULT_SUBTITLE_OUTLINE_COLOR,
    DEFAULT_SUBTITLE_OUTLINE_THICKNESS,
)
from .craig_pipeline import (
    DEFAULT_ALIGNMENT_SAMPLE_RATE,
    DEFAULT_COMPUTE_TYPE,
    DEFAULT_DEVICE,
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
    DEFAULT_POSTPROCESS_WORKERS,
    DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
    DEFAULT_SUBTITLE_VOLUME_SCALE_PERCENT,
    DEFAULT_VAD_OFFSET,
    DEFAULT_VAD_ONSET,
    CraigTranscriptionBatch,
    build_speaker_style_map,
    decode_audio_samples,
    parse_craig_speaker_name,
    resolve_alignment,
    resolve_craig_audio_files,
    resolve_reference_audio_path,
    transcribe_craig_audio_files,
    write_json,
)
from .craig_transcription_execution import CraigTranscriptionHint
from .merge_transcripts import refine_segments
from .silence_cut import probe_media_duration
from .subtitle_project import (
    DEFAULT_WAVEFORM_SAMPLE_RATE,
    build_waveform,
    create_project,
    derive_project_path,
    save_project,
)
from .transcribe import build_extract_audio_command, probe_audio_streams
from .transcription_context import TranscriptionContext, transcription_context_from_mapping
from .transcription_hint_plan import TranscriptionAsrSettings
from .transcription_hint_workflow import build_craig_hint_plan_from_context

DEFAULT_SPEAKER_COLORS = ["#FFD966", "#F6B26B", "#93C47D", "#6FA8DC", "#E78284", "#81C8BE"]


def log_progress(message: str) -> None:
    print(f"[subtitle_workflow] {message}", flush=True)


def _context_has_active_hint_inputs(context: TranscriptionContext) -> bool:
    return bool(
        context.game_title
        or context.game_notes
        or context.creator_terms
        or (context.web_dictionary_enabled and context.web_dictionary_terms)
        or (context.dictionary_confirmed and context.dictionary_path)
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


def _audio_cache_fingerprint(video_path: str, selector: str) -> str:
    path = Path(video_path).resolve()
    components = [str(path), selector]
    try:
        stat = path.stat()
        components.extend([str(stat.st_size), str(stat.st_mtime_ns)])
    except (OSError, ValueError):
        pass
    return hashlib.sha256("|".join(components).encode("utf-8")).hexdigest()[:16]


def _extract_video_audio_track(video_path: str, selector: str, transcript_dir: Path) -> Path:
    transcript_dir.mkdir(parents=True, exist_ok=True)
    safe_selector = selector.replace(":", "_").replace("/", "_")
    fingerprint = _audio_cache_fingerprint(video_path, selector)
    output = transcript_dir / f"{Path(video_path).stem}.{safe_selector}.{fingerprint}.wav"
    if not output.exists():
        subprocess.run(build_extract_audio_command(video_path, str(output), selector), check=True)
    return output


def build_workflow_asr_settings(
    *,
    model: str = DEFAULT_MODEL,
    device: str = DEFAULT_DEVICE,
    compute_type: str = DEFAULT_COMPUTE_TYPE,
    language: str = DEFAULT_LANGUAGE,
    vad_onset: float | None = DEFAULT_VAD_ONSET,
    vad_offset: float | None = DEFAULT_VAD_OFFSET,
    whisperx_version: str = "",
) -> TranscriptionAsrSettings:
    """Build the ASR settings payload used by workflow-level cache fingerprints."""
    return TranscriptionAsrSettings(
        model=model,
        device=device,
        compute_type=compute_type,
        language=language,
        vad_onset=vad_onset,
        vad_offset=vad_offset,
        whisperx_version=whisperx_version,
    )


def build_default_workflow_transcription_hint(
    transcription_context: TranscriptionContext | Mapping[str, Any] | None,
    *,
    output_dir: str | Path,
    asr_settings: TranscriptionAsrSettings,
) -> CraigTranscriptionHint | None:
    """Build the shared Craig transcription hint used by the editable workflow.

    Empty contexts intentionally return ``None`` so existing projects keep the
    legacy transcript cache behavior until the user provides or confirms game
    dictionary context.
    """
    context = transcription_context_from_mapping(transcription_context) if not isinstance(transcription_context, TranscriptionContext) else transcription_context
    if not _context_has_active_hint_inputs(context):
        return None
    return build_craig_hint_plan_from_context(
        context,
        base_dir=output_dir,
        asr_settings=asr_settings,
    ).hint


def transcribe_craig_audio_files_for_workflow(
    audio_files: list[Path],
    transcript_dir: Path,
    style_map: dict[str, str],
    offset_seconds: float,
    *,
    transcription_context: TranscriptionContext | Mapping[str, Any] | None = None,
    model: str = DEFAULT_MODEL,
    device: str = DEFAULT_DEVICE,
    compute_type: str = DEFAULT_COMPUTE_TYPE,
    language: str = DEFAULT_LANGUAGE,
    vad_onset: float | None = DEFAULT_VAD_ONSET,
    vad_offset: float | None = DEFAULT_VAD_OFFSET,
    skip_existing_transcripts: bool = True,
    postprocess_workers: int = DEFAULT_POSTPROCESS_WORKERS,
    subtitle_font_size: int = 50,
    subtitle_volume_scale_percent: float = DEFAULT_SUBTITLE_VOLUME_SCALE_PERCENT,
) -> CraigTranscriptionBatch:
    """Run Craig transcription for the editable workflow with optional game hints."""
    asr_settings = build_workflow_asr_settings(
        model=model,
        device=device,
        compute_type=compute_type,
        language=language,
        vad_onset=vad_onset,
        vad_offset=vad_offset,
    )
    default_hint = build_default_workflow_transcription_hint(
        transcription_context,
        output_dir=transcript_dir.parent,
        asr_settings=asr_settings,
    )
    return transcribe_craig_audio_files(
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
        default_transcription_hint=default_hint,
    )


def transcribe_to_project_with_context(
    *,
    video_path: str,
    audio_files: list[str],
    output_dir: str,
    reference_audio: str | None = None,
    reference_track: str | None = None,
    video_audio_track: str | None = None,
    alignment_offset_adjustment: float = 0.0,
    alignment_sample_rate: int = DEFAULT_ALIGNMENT_SAMPLE_RATE,
    model: str = DEFAULT_MODEL,
    device: str = DEFAULT_DEVICE,
    compute_type: str = DEFAULT_COMPUTE_TYPE,
    language: str = DEFAULT_LANGUAGE,
    vad_onset: float | None = DEFAULT_VAD_ONSET,
    vad_offset: float | None = DEFAULT_VAD_OFFSET,
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
    transcription_context: TranscriptionContext | Mapping[str, Any] | None = None,
) -> Path:
    """Create an editable project using context-aware Craig transcription.

    This mirrors ``subtitle_workflow.transcribe_to_project`` while keeping the
    context-aware transcription path isolated until the larger workflow module can
    be safely reduced to a thin call-through.
    """
    context = transcription_context_from_mapping(transcription_context) if not isinstance(transcription_context, TranscriptionContext) else transcription_context
    context_payload = context.to_dict()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    resolved_audio = resolve_craig_audio_files(None, audio_files)
    if not resolved_audio:
        selected_track = (video_audio_track or reference_track or "").strip()
        if not selected_track:
            raise SystemExit("No Craig speaker audio files were selected.")
        resolved_audio = [_extract_video_audio_track(video_path, selected_track, output / "transcripts")]
        reference_track = selected_track
        reference_audio = reference_audio or str(resolved_audio[0])
    if not resolved_audio:
        raise SystemExit("No Craig speaker audio files were selected.")
    reference_path = resolve_reference_audio_path(resolved_audio, reference_audio)
    if reference_path is None:
        reference_path = resolved_audio[0]

    project_path = derive_project_path(video_path, output)
    if project_path.exists() and not overwrite_project:
        raise SystemExit(
            f"Editable project already exists: {project_path}. "
            "Move it or pass --overwrite-project to replace it explicitly."
        )

    colors = dict(track_color_map or {})
    style_map = build_speaker_style_map(resolved_audio)
    speakers = _project_speakers(resolved_audio, style_map, colors)
    log_progress(f"Resolving alignment from {reference_path.name}")
    matched_track, offset_seconds, score = resolve_alignment(
        video_path,
        str(reference_path),
        reference_track,
        alignment_sample_rate,
    )
    offset_seconds += alignment_offset_adjustment
    log_progress(f"Alignment ready at {offset_seconds:+.3f}s on {matched_track}")

    transcript_dir = output / "transcripts"
    with ThreadPoolExecutor(max_workers=1) as waveform_executor:
        waveform_future = waveform_executor.submit(
            _build_waveforms,
            resolved_audio,
            speakers,
            offset_seconds,
        )
        transcription = transcribe_craig_audio_files_for_workflow(
            resolved_audio,
            transcript_dir,
            style_map,
            offset_seconds,
            transcription_context=context,
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
        refined, filtered = refine_segments(
            transcription.segments,
            subtitle_max_gap_seconds=subtitle_max_gap_seconds,
            subtitle_end_padding_seconds=subtitle_end_padding_seconds,
            subtitle_min_duration_seconds=subtitle_min_duration_seconds,
        )
        waveforms = waveform_future.result()

    transcript_map = transcription.transcript_map
    merged_path = write_json(str(output / f"{Path(video_path).stem}.craig.merged.json"), {"segments": refined})
    filtered_path = write_json(str(output / f"{Path(video_path).stem}.craig.filtered.json"), {"segments": filtered})
    try:
        duration_seconds = probe_media_duration(video_path)
    except (OSError, subprocess.CalledProcessError, ValueError):
        duration_seconds = max((float(segment["end"]) for segment in refined), default=0.0)

    project = create_project(
        video_path=video_path,
        output_dir=output,
        duration_seconds=duration_seconds,
        segments=refined,
        audio_sources=speakers,
        speakers=speakers,
        waveforms=waveforms,
        subtitle_settings={
            "font_size": subtitle_font_size,
            "outline_color": subtitle_outline_color,
            "outline_thickness": subtitle_outline_thickness,
            "volume_scale_percent": subtitle_volume_scale_percent,
            "max_gap_seconds": subtitle_max_gap_seconds,
            "end_padding_seconds": subtitle_end_padding_seconds,
            "min_duration_seconds": subtitle_min_duration_seconds,
        },
        render_settings=render_settings,
        transcription_context=context_payload,
        transcription={
            "model": model,
            "device": device,
            "compute_type": compute_type,
            "language": language,
            "reference_audio": str(reference_path.resolve()),
            "matched_track": matched_track,
            "offset_seconds": offset_seconds,
            "alignment_score": score,
            "transcripts": transcript_map,
            "merged_json": str(merged_path.resolve()),
            "filtered_json": str(filtered_path.resolve()),
            "transcription_context": context_payload,
        },
    )
    try:
        video_tracks = video_track_entries(probe_audio_streams(video_path))
    except (OSError, subprocess.SubprocessError, ValueError):
        video_tracks = None
    reconcile_audio_mix(project, video_tracks)
    save_project(project_path, project)
    log_progress(f"Project ready: {project_path}")
    return project_path
