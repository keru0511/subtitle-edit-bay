from __future__ import annotations

from dataclasses import dataclass

from .transcription_context import TranscriptionContext
from .transcription_dictionary import TranscriptionDictionary, enabled_dictionary_terms

DEFAULT_MAX_HOTWORDS = 80
DEFAULT_MAX_HOTWORD_LENGTH = 64
DEFAULT_MAX_PROMPT_CHARS = 1000
DEFAULT_MAX_PROMPT_TERMS = 40


@dataclass(frozen=True)
class TranscriptionHints:
    initial_prompt: str = ""
    hotwords: tuple[str, ...] = ()

    def has_hints(self) -> bool:
        return bool(self.initial_prompt or self.hotwords)


def _clean_hint_text(value: str, *, max_length: int) -> str:
    return "".join(char for char in value.strip() if char >= " " and char != "\x7f")[:max_length]


def _unique_limited_terms(
    terms: list[str] | tuple[str, ...],
    *,
    max_terms: int,
    max_term_length: int,
) -> tuple[str, ...]:
    if max_terms <= 0:
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for value in terms:
        term = _clean_hint_text(value, max_length=max_term_length)
        if not term or term in seen:
            continue
        result.append(term)
        seen.add(term)
        if len(result) >= max_terms:
            break
    return tuple(result)


def _confirmed_dictionary_terms(
    context: TranscriptionContext,
    dictionary: TranscriptionDictionary | None,
) -> tuple[str, ...]:
    if not context.dictionary_confirmed or dictionary is None:
        return ()
    return tuple(enabled_dictionary_terms(dictionary))


def _trim_prompt(prompt: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(prompt) <= max_chars:
        return prompt
    return prompt[:max_chars].rstrip(" 、。,")


def build_transcription_hints(
    context: TranscriptionContext,
    dictionary: TranscriptionDictionary | None = None,
    *,
    max_hotwords: int = DEFAULT_MAX_HOTWORDS,
    max_hotword_length: int = DEFAULT_MAX_HOTWORD_LENGTH,
    max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
    max_prompt_terms: int = DEFAULT_MAX_PROMPT_TERMS,
) -> TranscriptionHints:
    """Build ASR hint strings without invoking WhisperX or touching transcript cache state."""
    creator_terms = _unique_limited_terms(
        context.creator_terms,
        max_terms=max_hotwords,
        max_term_length=max_hotword_length,
    )
    dictionary_terms = _unique_limited_terms(
        _confirmed_dictionary_terms(context, dictionary),
        max_terms=max_hotwords,
        max_term_length=max_hotword_length,
    )
    hotwords = _unique_limited_terms(
        [*creator_terms, *dictionary_terms],
        max_terms=max_hotwords,
        max_term_length=max_hotword_length,
    )

    prompt_parts = ["日本語のゲーム実況です。"]
    game_title = _clean_hint_text(context.game_title, max_length=256)
    game_notes = _clean_hint_text(context.game_notes, max_length=512)
    if game_title:
        prompt_parts.append(f"ゲームタイトル: {game_title}。")
    if game_notes:
        prompt_parts.append(f"補足: {game_notes}。")
    if creator_terms:
        prompt_parts.append(f"作成者用語: {', '.join(creator_terms[:max_prompt_terms])}。")
    if dictionary_terms:
        prompt_parts.append(f"ゲーム内用語: {', '.join(dictionary_terms[:max_prompt_terms])}。")
    if len(prompt_parts) == 1 and not hotwords:
        return TranscriptionHints()
    prompt_parts.append("固有名詞と略称をできるだけ維持してください。")
    return TranscriptionHints(
        initial_prompt=_trim_prompt(" ".join(prompt_parts), max_prompt_chars),
        hotwords=hotwords,
    )
