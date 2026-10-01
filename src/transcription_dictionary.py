from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from .data_boundary import decode_json, is_object_mapping, is_object_sequence


class DictionarySourcePayload(TypedDict, total=False):
    """JSONに保存する出典。空のフィールドは省略する。"""

    url: str
    title: str
    where_found: list[str]


class DictionaryTermPayload(TypedDict):
    """検証済みの辞書エントリーを保存する形式。"""

    term: str
    aliases: list[str]
    type_hint: str
    enabled: bool
    score: float
    sources: list[DictionarySourcePayload]


class TranscriptionDictionaryPayload(TypedDict):
    """辞書全体の保存形式。"""

    game_title: str
    terms: list[DictionaryTermPayload]
    scope: str


class TranscriptionDictionaryError(ValueError):
    """Raised when a transcription dictionary JSON file is malformed."""


@dataclass(frozen=True)
class DictionarySource:
    url: str = ""
    title: str = ""
    where_found: tuple[str, ...] = ()

    def to_json(self) -> DictionarySourcePayload:
        payload: DictionarySourcePayload = {}
        if self.url:
            payload["url"] = self.url
        if self.title:
            payload["title"] = self.title
        if self.where_found:
            payload["where_found"] = list(self.where_found)
        return payload


@dataclass(frozen=True)
class DictionaryTerm:
    term: str
    aliases: tuple[str, ...] = ()
    type_hint: str = ""
    enabled: bool = True
    score: float = 1.0
    sources: tuple[DictionarySource, ...] = ()

    def to_json(self) -> DictionaryTermPayload:
        return {
            "term": self.term,
            "aliases": list(self.aliases),
            "type_hint": self.type_hint,
            "enabled": self.enabled,
            "score": self.score,
            "sources": [source.to_json() for source in self.sources],
        }


@dataclass(frozen=True)
class TranscriptionDictionary:
    game_title: str = ""
    terms: tuple[DictionaryTerm, ...] = ()
    scope: str = "game"

    def to_json(self) -> TranscriptionDictionaryPayload:
        return {
            "game_title": self.game_title,
            "terms": [term.to_json() for term in self.terms],
            "scope": self.scope,
        }


def _clean_text(value: object, field: str, *, required: bool = False, max_length: int = 256) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise TranscriptionDictionaryError(f"{field} must be a string")
    cleaned = "".join(char for char in value.strip() if char >= " " and char != "\x7f")[:max_length]
    if required and not cleaned:
        raise TranscriptionDictionaryError(f"{field} must not be empty")
    return cleaned


def _clean_text_list(value: object, field: str, *, exclude: set[str] | None = None) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not is_object_sequence(value):
        raise TranscriptionDictionaryError(f"{field} must be an array")
    blocked = exclude or set()
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        text = _clean_text(item, f"{field}[{index}]", max_length=256)
        if not text or text in blocked or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return tuple(result)


def _clean_bool(value: object, field: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise TranscriptionDictionaryError(f"{field} must be true or false")
    return value


def _clean_score(value: object, field: str) -> float:
    if value is None:
        return 1.0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TranscriptionDictionaryError(f"{field} must be a number")
    score = float(value)
    if not math.isfinite(score):
        raise TranscriptionDictionaryError(f"{field} must be finite")
    return round(score, 4)


def _normalize_source(value: object, field: str) -> DictionarySource:
    if not is_object_mapping(value):
        raise TranscriptionDictionaryError(f"{field} must be an object")
    return DictionarySource(
        url=_clean_text(value.get("url", ""), f"{field}.url", max_length=2048),
        title=_clean_text(value.get("title", ""), f"{field}.title", max_length=512),
        where_found=_clean_text_list(value.get("where_found", []), f"{field}.where_found"),
    )


def _normalize_sources(value: object, field: str) -> tuple[DictionarySource, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not is_object_sequence(value):
        raise TranscriptionDictionaryError(f"{field} must be an array")
    return tuple(_normalize_source(item, f"{field}[{index}]") for index, item in enumerate(value))


def normalize_dictionary_term(value: object, index: int) -> DictionaryTerm:
    field = f"terms[{index}]"
    if not is_object_mapping(value):
        raise TranscriptionDictionaryError(f"{field} must be an object")
    term = _clean_text(value.get("term"), f"{field}.term", required=True)
    aliases = _clean_text_list(value.get("aliases", []), f"{field}.aliases", exclude={term})
    return DictionaryTerm(
        term=term,
        aliases=aliases,
        type_hint=_clean_text(value.get("type_hint", ""), f"{field}.type_hint", max_length=128),
        enabled=_clean_bool(value.get("enabled", True), f"{field}.enabled", True),
        score=_clean_score(value.get("score", 1.0), f"{field}.score"),
        sources=_normalize_sources(value.get("sources", []), f"{field}.sources"),
    )


def transcription_dictionary_from_mapping(payload: object) -> TranscriptionDictionary:
    if not is_object_mapping(payload):
        raise TranscriptionDictionaryError("dictionary root must be an object")
    raw_terms = payload.get("terms")
    if not isinstance(raw_terms, list) or not is_object_sequence(raw_terms):
        raise TranscriptionDictionaryError("terms must be an array")
    scope = payload.get("scope", "game")
    if not isinstance(scope, str) or scope not in {"global", "game", "project"}:
        raise TranscriptionDictionaryError("scope must be global, game, or project")
    return TranscriptionDictionary(
        game_title=_clean_text(payload.get("game_title", ""), "game_title", max_length=256),
        terms=tuple(normalize_dictionary_term(item, index) for index, item in enumerate(raw_terms)),
        scope=scope,
    )


def load_transcription_dictionary(path: str | Path) -> TranscriptionDictionary:
    dictionary_path = Path(path)
    try:
        payload = decode_json(dictionary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise TranscriptionDictionaryError(f"invalid dictionary JSON: {dictionary_path}") from error
    if not is_object_mapping(payload):
        raise TranscriptionDictionaryError("dictionary root must be an object")
    return transcription_dictionary_from_mapping(payload)


def enabled_dictionary_terms(dictionary: TranscriptionDictionary, *, include_aliases: bool = True) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in dictionary.terms:
        if not item.enabled:
            continue
        candidates = (item.term, *item.aliases) if include_aliases else (item.term,)
        for term in candidates:
            if term in seen:
                continue
            result.append(term)
            seen.add(term)
    return result
