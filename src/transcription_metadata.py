"""ネットワーク取得に依存しない、辞書候補の保存形式と正規化。"""

from __future__ import annotations

from typing import SupportsFloat, SupportsIndex, TypedDict

from .data_boundary import is_object_mapping, is_object_sequence


class WebDictionaryCandidate(TypedDict):
    term: str
    source: str
    score: str


def normalize_web_dictionary_term(term: str) -> str:
    cleaned = "".join(char for char in term.strip() if char >= " " and char != "\x7f")
    normalized = " ".join(cleaned.split()).strip("- ")
    if not normalized:
        return ""
    ascii_parts = normalized.split()
    if all(part.isalpha() and part.islower() for part in ascii_parts):
        return " ".join(part.capitalize() for part in ascii_parts)
    return normalized


def normalize_web_dictionary_candidate_metadata(
    value: object,
    field: str,
    *,
    max_items: int,
) -> tuple[WebDictionaryCandidate, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not is_object_sequence(value):
        raise TypeError(f"{field} must be an array of source objects")

    seen: set[tuple[str, str]] = set()
    terms: list[WebDictionaryCandidate] = []
    for index, raw in enumerate(value):
        if index >= max_items:
            break
        if not is_object_mapping(raw):
            raise TypeError(f"{field} must contain only objects")
        term = normalize_web_dictionary_term(str(raw.get("term", "")))
        source = normalize_web_dictionary_term(str(raw.get("source", ""))) or "unknown"
        raw_score = raw.get("score", "0.00")
        try:
            if isinstance(raw_score, memoryview):
                raw_score = raw_score.tobytes()
            if not isinstance(raw_score, (str, bytes, bytearray, SupportsFloat, SupportsIndex)):
                raise TypeError("score must be convertible to float")
            score = f"{float(raw_score):.2f}"
        except (TypeError, ValueError):
            score = "0.00"
        if not term:
            continue
        key = (term.casefold(), source.casefold())
        if key in seen:
            continue
        seen.add(key)
        terms.append({"term": term, "source": source, "score": score})
    return tuple(terms)
