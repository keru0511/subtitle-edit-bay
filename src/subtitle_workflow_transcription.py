from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .craig_pipeline import (
    DEFAULT_COMPUTE_TYPE,
    DEFAULT_DEVICE,
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
    DEFAULT_POSTPROCESS_WORKERS,
    DEFAULT_SUBTITLE_VOLUME_SCALE_PERCENT,
    DEFAULT_VAD_OFFSET,
    DEFAULT_VAD_ONSET,
    CraigTranscriptionBatch,
    transcribe_craig_audio_files,
)
from .craig_transcription_execution import CraigTranscriptionHint
from .transcription_context import TranscriptionContext, transcription_context_from_mapping
from .transcription_hint_plan import TranscriptionAsrSettings
from .transcription_hint_workflow import build_craig_hint_plan_from_context


def _context_has_active_hint_inputs(context: TranscriptionContext) -> bool:
    return bool(
        context.game_title
        or context.game_notes
        or context.creator_terms
        or (context.dictionary_confirmed and context.dictionary_path)
    )


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
