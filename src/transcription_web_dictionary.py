from __future__ import annotations

import html
import re
from typing import Any, Mapping, Sequence


_CJK_TERM_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[\u3040-\u30ff]{2,}")
_ENGLISH_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9-']*")
_SPLIT_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff\u3040-\u30ff]+")

_GENERIC_STOP_WORDS = {
    "game",
    "games",
    "match",
    "use",
    "run",
    "video",
    "stream",
    "session",
    "battle",
    "mode",
    "with",
    "then",
    "in",
    "on",
    "the",
    "of",
    "for",
    "or",
    "to",
    "if",
}

_MAX_WEB_DICTIONARY_CANDIDATES = 40
_MAX_TERM_LENGTH = 48

_TITLE_SOURCE = "title"
_NOTES_SOURCE = "notes"
_SNIPPET_SOURCE_PREFIX = "snippet"


def _normalize_term(term: str) -> str:
    cleaned = "".join(char for char in term.strip() if char >= " " and char != "\x7f")
    normalized = " ".join(cleaned.split()).strip("- ")
    if not normalized:
        return ""
    ascii_parts = normalized.split()
    if all(part.isalpha() and part.islower() for part in ascii_parts):
        return " ".join(part.capitalize() for part in ascii_parts)
    return normalized


def _is_usable_term(term: str) -> bool:
    if not term or len(term) < 2:
        return False
    if len(term) > _MAX_TERM_LENGTH:
        return False
    lowered = term.lower()
    if lowered in _GENERIC_STOP_WORDS:
        return False
    if term.isdigit():
        return False
    return True


def _extract_terms(value: str, *, allow_branded_phrase: bool = False) -> list[str]:
    result: list[str] = []
    tokens: list[str] = []

    for raw in _SPLIT_RE.split(value):
        for token in raw.split():
            normalized_token = _normalize_term(token)
            if normalized_token:
                tokens.append(normalized_token)

    if allow_branded_phrase:
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if (
                index + 1 < len(tokens)
                and _ENGLISH_TERM_RE.fullmatch(token) is not None
                and tokens[index + 1].isdigit()
            ):
                result.append(f"{token} {tokens[index + 1]}")
                index += 2
                continue
            result.append(token)
            index += 1
    else:
        result.extend(tokens)

    result.extend(match.group(0) for match in _CJK_TERM_RE.finditer(value))
    result.extend(match.group(0) for match in _ENGLISH_TERM_RE.finditer(value))
    return result


def _coerce_snippets(snippets: Sequence[str] | None) -> list[tuple[str, str]]:
    if not snippets:
        return []
    if isinstance(snippets, str):
        raise TypeError("snippets must be a sequence of strings")
    collected: list[tuple[str, str]] = []
    for index, raw in enumerate(snippets):
        if isinstance(raw, str) and raw.strip():
            collected.append((f"{_SNIPPET_SOURCE_PREFIX}:{index + 1}", raw))
    return collected


def _strip_html(value: str) -> str:
    decoded = html.unescape(value)
    return re.sub(r"<[^>]+>", " ", decoded)


def _candidate_terms_with_sources(
    game_title: str,
    game_notes: str = "",
    *,
    max_terms: int = _MAX_WEB_DICTIONARY_CANDIDATES,
    snippets: Sequence[str] | None = None,
) -> list[tuple[str, str]]:
    seen: set[str] = set()
    items: list[tuple[str, str]] = []
    source_order = [
        (_TITLE_SOURCE, game_title),
        (_NOTES_SOURCE, game_notes),
        *_coerce_snippets(snippets),
    ]

    for source_label, raw_text in source_order:
        if not isinstance(raw_text, str):
            continue
        normalized_source_text = _strip_html(raw_text)
        if not normalized_source_text.strip():
            continue
        allow_branded_phrase = source_label == _TITLE_SOURCE
        for candidate in _extract_terms(normalized_source_text, allow_branded_phrase=allow_branded_phrase):
            normalized_term = _normalize_term(candidate)
            if not normalized_term:
                continue
            if not _is_usable_term(normalized_term):
                continue
            lowered = normalized_term.casefold()
            if lowered in seen:
                continue
            seen.add(lowered)
            items.append((normalized_term, source_label))
            if len(items) >= max_terms:
                return items
    return items


def build_web_dictionary_candidate_metadata(
    game_title: str,
    game_notes: str = "",
    *,
    max_terms: int = _MAX_WEB_DICTIONARY_CANDIDATES,
    snippets: Sequence[str] | None = None,
) -> tuple[dict[str, str], ...]:
    return tuple({"term": term, "source": source} for term, source in _candidate_terms_with_sources(
        game_title,
        game_notes,
        max_terms=max_terms,
        snippets=snippets,
    ))


def build_web_dictionary_candidates(
    game_title: str,
    game_notes: str = "",
    *,
    max_terms: int = _MAX_WEB_DICTIONARY_CANDIDATES,
    snippets: Sequence[str] | None = None,
) -> tuple[str, ...]:
    """Build a deterministic candidate list from title/notes/snippets without network access."""
    return tuple(term for term, _ in _candidate_terms_with_sources(
        game_title,
        game_notes,
        max_terms=max_terms,
        snippets=snippets,
    ))


def normalize_web_dictionary_candidate_metadata(
    value: Any,
    field: str,
    *,
    max_items: int,
) -> tuple[dict[str, str], ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise TypeError(f"{field} must be an array of source objects")

    seen: set[tuple[str, str]] = set()
    terms: list[dict[str, str]] = []
    for index, raw in enumerate(value):
        if index >= max_items:
            break
        if not isinstance(raw, Mapping):
            raise TypeError(f"{field} must contain only objects")
        term = _normalize_term(str(raw.get("term", "")))
        source = _normalize_term(str(raw.get("source", ""))) or "unknown"
        if not term:
            continue
        key = (term.casefold(), source.casefold())
        if key in seen:
            continue
        seen.add(key)
        terms.append({"term": term, "source": source})
    return tuple(terms)
