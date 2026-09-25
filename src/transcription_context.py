from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from .data_boundary import is_object_mapping, is_object_sequence
from .transcription_metadata import WebDictionaryCandidate, normalize_web_dictionary_candidate_metadata


class TranscriptionContextPayload(TypedDict):
    """検証済みコンテキストの保存形式。"""

    game_title: str
    game_notes: str
    creator_terms: list[str]
    dictionary_path: str | None
    dictionary_confirmed: bool
    web_dictionary_enabled: bool
    web_dictionary_candidates: list[str]
    web_dictionary_terms: list[str]
    web_dictionary_candidate_metadata: list[WebDictionaryCandidate]


class TranscriptionContextError(ValueError):
    """Raised when transcription context metadata is malformed."""


@dataclass(frozen=True)
class TranscriptionContext:
    game_title: str = ""
    game_notes: str = ""
    creator_terms: tuple[str, ...] = ()
    dictionary_path: str | None = None
    dictionary_confirmed: bool = False
    web_dictionary_enabled: bool = False
    web_dictionary_candidates: tuple[str, ...] = ()
    web_dictionary_terms: tuple[str, ...] = ()
    web_dictionary_candidate_metadata: tuple[WebDictionaryCandidate, ...] = ()

    def to_dict(self) -> TranscriptionContextPayload:
        return {
            "game_title": self.game_title,
            "game_notes": self.game_notes,
            "creator_terms": list(self.creator_terms),
            "dictionary_path": self.dictionary_path,
            "dictionary_confirmed": self.dictionary_confirmed,
            "web_dictionary_enabled": self.web_dictionary_enabled,
            "web_dictionary_candidates": list(self.web_dictionary_candidates),
            "web_dictionary_terms": list(self.web_dictionary_terms),
            "web_dictionary_candidate_metadata": [item.copy() for item in self.web_dictionary_candidate_metadata],
        }


def _clean_text(value: object, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TranscriptionContextError(f"transcription_context.{field} must be a string")
    return "".join(char for char in value.strip() if char >= " " and char != "\x7f")


def _clean_optional_path(value: object, field: str) -> str | None:
    if value is None:
        return None
    cleaned = _clean_text(value, field)
    return cleaned or None


def _bool(value: object, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise TranscriptionContextError(f"transcription_context.{field} must be true or false")


def _creator_terms(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not is_object_sequence(value):
        raise TranscriptionContextError("transcription_context.creator_terms must be an array of strings")

    terms: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        term = _clean_text(item, f"creator_terms[{index}]")
        if not term:
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
    return tuple(terms)


def _normalize_term_sequence(value: object, field: str, *, max_terms: int = 256) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not is_object_sequence(value):
        raise TranscriptionContextError(f"transcription_context.{field} must be an array of strings")

    terms: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if index >= max_terms:
            break
        term = _clean_text(item, f"{field}[{index}]")
        if not term:
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
    return tuple(terms)


def _normalize_web_dictionary_metadata(
    value: object,
    field: str,
) -> tuple[WebDictionaryCandidate, ...]:
    return normalize_web_dictionary_candidate_metadata(value, field, max_items=256)


def transcription_context_from_mapping(payload: object = None) -> TranscriptionContext:
    if payload is None:
        payload = {}
    if not is_object_mapping(payload):
        raise TranscriptionContextError("transcription_context must be an object")

    return TranscriptionContext(
        game_title=_clean_text(payload.get("game_title", ""), "game_title"),
        game_notes=_clean_text(payload.get("game_notes", ""), "game_notes"),
        creator_terms=_creator_terms(payload.get("creator_terms", ())),
        dictionary_path=_clean_optional_path(payload.get("dictionary_path"), "dictionary_path"),
        dictionary_confirmed=_bool(payload.get("dictionary_confirmed", False), "dictionary_confirmed"),
        web_dictionary_enabled=_bool(payload.get("web_dictionary_enabled", False), "web_dictionary_enabled"),
        web_dictionary_candidates=_normalize_term_sequence(
            payload.get("web_dictionary_candidates", ()), "web_dictionary_candidates"
        ),
        web_dictionary_terms=_normalize_term_sequence(payload.get("web_dictionary_terms", ()), "web_dictionary_terms"),
        web_dictionary_candidate_metadata=_normalize_web_dictionary_metadata(
            payload.get("web_dictionary_candidate_metadata"),
            "web_dictionary_candidate_metadata",
        ),
    )


def normalize_transcription_context(payload: object = None) -> TranscriptionContextPayload:
    return transcription_context_from_mapping(payload).to_dict()
