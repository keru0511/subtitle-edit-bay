from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .transcription_context import TranscriptionContext, transcription_context_from_mapping
from .transcription_dictionary import TranscriptionDictionary, load_transcription_dictionary
from .transcription_hint_plan import (
    CraigTranscriptionHintPlan,
    TranscriptionAsrSettings,
    build_craig_transcription_hint_plan,
)


class TranscriptionHintWorkflowError(ValueError):
    """Raised when workflow-level transcription hint inputs cannot be resolved."""


def _context_from_value(context: TranscriptionContext | Mapping[str, Any] | None) -> TranscriptionContext:
    if isinstance(context, TranscriptionContext):
        return context
    return transcription_context_from_mapping(context)


def resolve_confirmed_dictionary_path(
    context: TranscriptionContext | Mapping[str, Any] | None,
    *,
    base_dir: str | Path | None = None,
) -> Path | None:
    """Resolve the confirmed dictionary path for a project/workflow context.

    Unconfirmed dictionaries are intentionally inert: they do not resolve to a
    path and therefore cannot affect ASR prompts, hotwords, or cache keys.
    """
    normalized = _context_from_value(context)
    if not normalized.dictionary_confirmed or not normalized.dictionary_path:
        return None

    candidate = Path(normalized.dictionary_path).expanduser()
    if candidate.is_absolute():
        return candidate
    if base_dir is None:
        return candidate
    return Path(base_dir) / candidate


def load_confirmed_transcription_dictionary(
    context: TranscriptionContext | Mapping[str, Any] | None,
    *,
    base_dir: str | Path | None = None,
) -> TranscriptionDictionary | None:
    """Load the manually confirmed transcription dictionary, when present.

    Missing confirmed dictionaries fail fast because otherwise a changed or lost
    dictionary file would silently produce a different cache fingerprint.
    """
    dictionary_path = resolve_confirmed_dictionary_path(context, base_dir=base_dir)
    if dictionary_path is None:
        return None
    if not dictionary_path.is_file():
        raise TranscriptionHintWorkflowError(f"confirmed transcription dictionary was not found: {dictionary_path}")
    return load_transcription_dictionary(dictionary_path)


def build_craig_hint_plan_from_context(
    context: TranscriptionContext | Mapping[str, Any] | None,
    *,
    asr_settings: TranscriptionAsrSettings | None = None,
    base_dir: str | Path | None = None,
) -> CraigTranscriptionHintPlan:
    """Build Craig transcription hints from saved workflow/project context.

    This function is the workflow boundary between persisted user choices and the
    lower-level Craig transcription execution path. It does not run WhisperX and
    does not touch transcript files.
    """
    normalized = _context_from_value(context)
    dictionary = load_confirmed_transcription_dictionary(normalized, base_dir=base_dir)
    return build_craig_transcription_hint_plan(
        normalized,
        dictionary,
        asr_settings=asr_settings,
    )
