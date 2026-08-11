from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .transcribe import (
    build_whisperx_command,
    expected_log_path,
    expected_transcript_path,
    run_command_with_utf8_log,
)
from .transcript_cache import transcript_cache_is_valid, write_transcript_cache_metadata


@dataclass(frozen=True)
class TranscriptionExecutionResult:
    transcript_path: Path
    cache_hit: bool
    cache_metadata_path: Path | None = None


def transcribe_audio_with_cache(
    audio_path: str,
    output_dir: str,
    *,
    model: str = "large-v3",
    device: str = "cpu",
    compute_type: str = "int8",
    language: str | None = "ja",
    vad_onset: float | None = 0.35,
    vad_offset: float | None = 0.2,
    initial_prompt: str | None = None,
    hotwords: Sequence[str] | str | None = None,
    skip_existing: bool = True,
    cache_fingerprint: str | None = None,
    cache_settings: Mapping[str, Any] | None = None,
) -> TranscriptionExecutionResult:
    """Run WhisperX with optional dictionary-aware cache validation.

    When ``cache_fingerprint`` is not supplied, existing transcript reuse keeps
    the legacy path-exists behavior. When a fingerprint is supplied, the
    transcript must also have matching sidecar metadata before it can be reused.
    """
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    transcript_path = expected_transcript_path(audio_path, str(output))

    if skip_existing and transcript_cache_is_valid(
        transcript_path,
        expected_fingerprint=cache_fingerprint,
    ):
        return TranscriptionExecutionResult(transcript_path=transcript_path, cache_hit=True)

    whisperx_command = build_whisperx_command(
        audio_path,
        str(output),
        model=model,
        device=device,
        compute_type=compute_type,
        language=language,
        vad_onset=vad_onset,
        vad_offset=vad_offset,
        initial_prompt=initial_prompt,
        hotwords=hotwords,
    )
    run_command_with_utf8_log(whisperx_command, str(expected_log_path(audio_path, str(output))))

    metadata_path = None
    if cache_fingerprint:
        metadata_path = write_transcript_cache_metadata(
            transcript_path,
            fingerprint=cache_fingerprint,
            settings=cache_settings,
        )
    return TranscriptionExecutionResult(
        transcript_path=transcript_path,
        cache_hit=False,
        cache_metadata_path=metadata_path,
    )
