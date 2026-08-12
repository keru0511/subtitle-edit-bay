from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .transcription_execution import TranscriptionExecutionResult, transcribe_audio_with_cache


@dataclass(frozen=True)
class CraigTranscriptionHint:
    """Optional ASR hint and cache metadata for one Craig speaker audio file."""

    initial_prompt: str = ""
    hotwords: tuple[str, ...] = ()
    cache_fingerprint: str | None = None
    cache_settings: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class CraigTranscriptionFileResult:
    """Cache-aware transcription result for one Craig speaker audio file."""

    audio_path: Path
    transcript_path: Path
    cache_hit: bool
    cache_metadata_path: Path | None = None


@dataclass(frozen=True)
class CraigTranscriptionBatchExecution:
    """Cache-aware transcription results for a batch of Craig speaker audio files."""

    transcript_map: dict[str, str]
    results: tuple[CraigTranscriptionFileResult, ...]


def resolve_craig_transcription_hint(
    audio_path: str | Path,
    hints_by_audio: Mapping[str, CraigTranscriptionHint] | None = None,
    *,
    default_hint: CraigTranscriptionHint | None = None,
) -> CraigTranscriptionHint:
    """Resolve per-audio Craig hint data by absolute path, original path, or file name."""
    if not hints_by_audio:
        return default_hint or CraigTranscriptionHint()

    path = Path(audio_path)
    lookup_keys = (
        str(path),
        str(path.resolve()),
        path.name,
    )
    for key in lookup_keys:
        if key in hints_by_audio:
            return hints_by_audio[key]
    return default_hint or CraigTranscriptionHint()


def transcribe_craig_audio_file_with_cache(
    audio_path: str | Path,
    transcript_dir: str | Path,
    *,
    model: str = "large-v3",
    device: str = "cpu",
    compute_type: str = "int8",
    language: str | None = "ja",
    vad_onset: float | None = 0.35,
    vad_offset: float | None = 0.2,
    skip_existing_transcripts: bool = True,
    hint: CraigTranscriptionHint | None = None,
) -> TranscriptionExecutionResult:
    """Run one Craig speaker audio transcription through the cache-aware runner.

    This adapter keeps the Craig pipeline boundary narrow: legacy callers can omit
    ``hint`` and retain path-exists cache reuse, while dictionary-aware callers can
    pass prompt/hotword/fingerprint data without changing the low-level runner.
    """
    resolved_hint = hint or CraigTranscriptionHint()
    return transcribe_audio_with_cache(
        str(audio_path),
        str(transcript_dir),
        model=model,
        device=device,
        compute_type=compute_type,
        language=language,
        vad_onset=vad_onset,
        vad_offset=vad_offset,
        initial_prompt=resolved_hint.initial_prompt,
        hotwords=resolved_hint.hotwords,
        skip_existing=skip_existing_transcripts,
        cache_fingerprint=resolved_hint.cache_fingerprint,
        cache_settings=resolved_hint.cache_settings,
    )


def transcribe_craig_audio_batch_with_cache(
    audio_files: Sequence[str | Path],
    transcript_dir: str | Path,
    *,
    model: str = "large-v3",
    device: str = "cpu",
    compute_type: str = "int8",
    language: str | None = "ja",
    vad_onset: float | None = 0.35,
    vad_offset: float | None = 0.2,
    skip_existing_transcripts: bool = True,
    hints_by_audio: Mapping[str, CraigTranscriptionHint] | None = None,
    default_hint: CraigTranscriptionHint | None = None,
) -> CraigTranscriptionBatchExecution:
    """Run cache-aware transcription for an ordered batch of Craig audio files.

    The returned ``transcript_map`` preserves the historical Craig pipeline shape:
    absolute audio paths map to absolute transcript JSON paths. The explicit
    per-file ``results`` tuple lets the pipeline log cache hits without repeating
    fingerprint validation logic.
    """
    transcript_map: dict[str, str] = {}
    results: list[CraigTranscriptionFileResult] = []

    for audio_file in audio_files:
        audio_path = Path(audio_file)
        hint = resolve_craig_transcription_hint(
            audio_path,
            hints_by_audio,
            default_hint=default_hint,
        )
        result = transcribe_craig_audio_file_with_cache(
            audio_path,
            transcript_dir,
            model=model,
            device=device,
            compute_type=compute_type,
            language=language,
            vad_onset=vad_onset,
            vad_offset=vad_offset,
            skip_existing_transcripts=skip_existing_transcripts,
            hint=hint,
        )
        transcript_map[str(audio_path.resolve())] = str(result.transcript_path.resolve())
        results.append(
            CraigTranscriptionFileResult(
                audio_path=audio_path,
                transcript_path=result.transcript_path,
                cache_hit=result.cache_hit,
                cache_metadata_path=result.cache_metadata_path,
            )
        )

    return CraigTranscriptionBatchExecution(
        transcript_map=transcript_map,
        results=tuple(results),
    )
