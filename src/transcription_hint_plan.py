from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .craig_transcription_execution import CraigTranscriptionHint
from .transcript_cache import build_transcript_cache_fingerprint, stable_payload_hash
from .transcription_context import TranscriptionContext
from .transcription_dictionary import TranscriptionDictionary
from .transcription_hints import TranscriptionHints, build_transcription_hints


@dataclass(frozen=True)
class TranscriptionAsrSettings:
    model: str = "large-v3"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str = "ja"
    vad_onset: float | None = 0.35
    vad_offset: float | None = 0.2
    whisperx_version: str = ""

    def to_cache_settings(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "device": self.device,
            "compute_type": self.compute_type,
            "language": self.language,
            "vad_onset": self.vad_onset,
            "vad_offset": self.vad_offset,
            "whisperx_version": self.whisperx_version,
        }


@dataclass(frozen=True)
class CraigTranscriptionHintPlan:
    hint: CraigTranscriptionHint
    transcription_hints: TranscriptionHints
    dictionary_hash: str
    cache_fingerprint: str
    cache_settings: Mapping[str, Any]


def confirmed_dictionary_hash(
    context: TranscriptionContext,
    dictionary: TranscriptionDictionary | None,
) -> str:
    if not context.dictionary_confirmed or dictionary is None:
        return ""
    return stable_payload_hash(dictionary.to_json())


def build_craig_transcription_hint_plan(
    context: TranscriptionContext,
    dictionary: TranscriptionDictionary | None = None,
    *,
    asr_settings: TranscriptionAsrSettings | None = None,
) -> CraigTranscriptionHintPlan:
    """Build Craig ASR hints and cache metadata without running WhisperX.

    Only confirmed dictionaries affect the prompt, hotwords, and dictionary hash.
    Unconfirmed candidate dictionaries remain inert until the creator approves
    them by setting ``transcription_context.dictionary_confirmed`` to true.
    """
    settings = asr_settings or TranscriptionAsrSettings()
    transcription_hints = build_transcription_hints(context, dictionary)
    dictionary_hash = confirmed_dictionary_hash(context, dictionary)
    cache_fingerprint = build_transcript_cache_fingerprint(
        model=settings.model,
        device=settings.device,
        compute_type=settings.compute_type,
        language=settings.language,
        vad_onset=settings.vad_onset,
        vad_offset=settings.vad_offset,
        initial_prompt=transcription_hints.initial_prompt,
        hotwords=transcription_hints.hotwords,
        dictionary_hash=dictionary_hash,
        game_title=context.game_title,
        whisperx_version=settings.whisperx_version,
    )
    cache_settings: dict[str, Any] = {
        "asr": settings.to_cache_settings(),
        "transcription_context": context.to_dict(),
        "dictionary_hash": dictionary_hash,
        "initial_prompt_hash": stable_payload_hash(transcription_hints.initial_prompt),
        "hotwords_hash": stable_payload_hash(transcription_hints.hotwords),
    }
    return CraigTranscriptionHintPlan(
        hint=CraigTranscriptionHint(
            initial_prompt=transcription_hints.initial_prompt,
            hotwords=transcription_hints.hotwords,
            cache_fingerprint=cache_fingerprint,
            cache_settings=cache_settings,
        ),
        transcription_hints=transcription_hints,
        dictionary_hash=dictionary_hash,
        cache_fingerprint=cache_fingerprint,
        cache_settings=cache_settings,
    )
