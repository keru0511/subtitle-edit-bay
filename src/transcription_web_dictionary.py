from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from typing import Any, Callable, Mapping, Sequence
from urllib.error import URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
import re


_CJK_TERM_RE = re.compile(r"[荳-鮴･]{2,}|[縺・繧薙ぃ-繝ｶ繝ｼ]{2,}")
_ENGLISH_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9-']*")
_SPLIT_RE = re.compile(r'[\n\r\t,、。;:！!\.\\/\|()\[\]{}【】『』“”\'"\\]+')
_HTML_SNIPPET_RE = re.compile(
    r'class="result__snippet"[^>]*>(.*?)</(?:a|div|span)>',
    re.IGNORECASE | re.DOTALL,
)

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
    "螳滓ｳ・,
    "蜈ｬ蠑・,
    "繝ｩ繧､繝・,
    "陬懆ｶｳ",
    "繧､繝吶Φ繝・,
    "驟堺ｿ｡",
    "繧ｬ繝・,
    "繝弱・繝・,
    "繝弱・繝・,
}

_MAX_WEB_DICTIONARY_CANDIDATES = 40
_MAX_TERM_LENGTH = 48
_TITLE_SOURCE_SCORE = 3.0
_NOTES_SOURCE_SCORE = 2.0
_WEB_SNIPPET_SOURCE_SCORE = 1.0
_WEB_SEARCH_URL = "https://duckduckgo.com/html/?q={query}"
_WEB_SEARCH_TIMEOUT_SECONDS = 1.5


FetchSnippetsFn = Callable[[str, str, int], tuple[str, ...]]


@dataclass(frozen=True)
class WebDictionarySource:
    label: str
    where_found: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"label": self.label}
        if self.where_found:
            payload["where_found"] = self.where_found
        return payload


@dataclass(frozen=True)
class WebDictionaryCandidate:
    term: str
    sources: tuple[WebDictionarySource, ...] = ()
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "score": self.score,
            "sources": [source.to_dict() for source in self.sources],
        }


class HeuristicWebDictionaryProvider:
    """Build candidate terms using local heuristics and optional web snippets."""

    def __init__(
        self,
        *,
        fetch_snippets: FetchSnippetsFn | None = None,
        max_snippets: int = 3,
    ) -> None:
        self.fetch_snippets = fetch_snippets or _fetch_web_search_snippets
        self.max_snippets = max_snippets

    def discover_snippets(self, game_title: str, game_notes: str) -> tuple[str, ...]:
        max_snippets = self.max_snippets if self.max_snippets > 0 else 0
        if max_snippets <= 0:
            return ()
        return self.fetch_snippets(game_title, game_notes, max_snippets)

    def build_candidate_records(
        self,
        game_title: str,
        game_notes: str = "",
        *,
        max_terms: int = _MAX_WEB_DICTIONARY_CANDIDATES,
    ) -> tuple[WebDictionaryCandidate, ...]:
        return build_web_dictionary_candidate_records(
            game_title=game_title,
            game_notes=game_notes,
            html_snippets=self.discover_snippets(game_title, game_notes),
            max_terms=max_terms,
        )


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


def _strip_html(value: str) -> str:
    return unescape(re.sub(r"<[^>]+>", " ", value))


def _to_score(value: object) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int):
        return float(value)
    if isinstance(value, float):
        return value
    return 0.0


def normalize_web_dictionary_candidate_metadata(
    value: object,
    *,
    field: str = "web_dictionary_candidate_metadata",
) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise TypeError(f"{field} must be an array of metadata objects")

    by_term: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise TypeError(f"{field}[{index}] must be an object")

        raw_term = raw.get("term")
        if not isinstance(raw_term, str):
            raise TypeError(f"{field}[{index}].term must be a string")

        normalized_term = _normalize_term(raw_term)
        if not normalized_term:
            continue
        key = normalized_term.casefold()

        normalized_sources: list[dict[str, str]] = []
        raw_sources = raw.get("sources", ())
        if raw_sources is None:
            raw_sources = ()
        if isinstance(raw_sources, str) or not isinstance(raw_sources, Sequence):
            raise TypeError(f"{field}[{index}].sources must be an array")
        for source_index, source_raw in enumerate(raw_sources):
            if source_raw is None:
                continue
            if not isinstance(source_raw, Mapping):
                raise TypeError(
                    f"{field}[{index}].sources[{source_index}] must be an object",
                )
            source: dict[str, str] = {}
            label = source_raw.get("label")
            if isinstance(label, str) and label.strip():
                source["label"] = label.strip()
            where_found = source_raw.get("where_found")
            if isinstance(where_found, str) and where_found.strip():
                source["where_found"] = where_found.strip()
            if source:
                normalized_sources.append(source)

        normalized_score = _to_score(raw.get("score", 0.0))
        normalized_term_payload = {
            "term": normalized_term,
            "score": normalized_score,
            "sources": normalized_sources,
        }

        if key in by_term:
            existing = by_term[key]
            existing_sources = existing.get("sources")
            if isinstance(existing_sources, list):
                for source in normalized_sources:
                    if source not in existing_sources:
                        existing_sources.append(source)
            existing_score = _to_score(existing.get("score", 0.0))
            if normalized_score > existing_score:
                existing["score"] = normalized_score
            continue

        by_term[key] = normalized_term_payload

    return tuple(by_term[term_key] for term_key in by_term)


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


def build_web_dictionary_candidates(
    game_title: str,
    game_notes: str = "",
    *,
    max_terms: int = _MAX_WEB_DICTIONARY_CANDIDATES,
) -> tuple[str, ...]:
    """Build a deterministic candidate list from title/notes."""

    return tuple(
        candidate.term
        for candidate in build_web_dictionary_candidate_records(
            game_title=game_title,
            game_notes=game_notes,
            max_terms=max_terms,
        )
    )


def build_web_dictionary_candidate_metadata(
    game_title: str,
    game_notes: str = "",
    *,
    html_snippets: Sequence[str] | None = None,
    max_terms: int = _MAX_WEB_DICTIONARY_CANDIDATES,
) -> tuple[dict[str, Any], ...]:
    return tuple(
        candidate.to_dict()
        for candidate in build_web_dictionary_candidate_records(
            game_title=game_title,
            game_notes=game_notes,
            html_snippets=html_snippets,
            max_terms=max_terms,
        )
    )


def build_web_dictionary_candidate_records(
    game_title: str,
    game_notes: str = "",
    *,
    html_snippets: Sequence[str] | None = None,
    max_terms: int = _MAX_WEB_DICTIONARY_CANDIDATES,
) -> tuple[WebDictionaryCandidate, ...]:
    """Build a ranked candidate list with source metadata."""

    html_snippets = html_snippets or ()
    raw_sources: list[tuple[str, str, bool, float]] = [
        ("title", game_title, True, _TITLE_SOURCE_SCORE),
        ("notes", game_notes, False, _NOTES_SOURCE_SCORE),
    ]
    for index, snippet in enumerate(html_snippets):
        raw_sources.append((f"html_snippet[{index}]", _strip_html(snippet), False, _WEB_SNIPPET_SOURCE_SCORE / (index + 1)))

    seen: dict[str, int] = {}
    candidates: list[WebDictionaryCandidate] = []

    def add_candidate(term: str, source: WebDictionarySource, score: float) -> None:
        lowered = term.casefold()
        if lowered in seen:
            idx = seen[lowered]
            existing = candidates[idx]
            next_sources = existing.sources
            if source not in existing.sources:
                next_sources = existing.sources + (source,)
            candidates[idx] = WebDictionaryCandidate(
                term=existing.term,
                sources=next_sources,
                score=existing.score + score,
            )
            return
        seen[lowered] = len(candidates)
        candidates.append(WebDictionaryCandidate(term=term, sources=(source,), score=score))

    for label, text, allow_branded, source_score in raw_sources:
        for raw_term in _extract_terms(text, allow_branded_phrase=allow_branded):
            normalized = _normalize_term(raw_term)
            if not normalized or not _is_usable_term(normalized):
                continue
            snippet = text.strip().replace("\n", " ")
            source = WebDictionarySource(label=label, where_found=snippet[:128])
            add_candidate(normalized, source, source_score)

    ranked = sorted(
        enumerate(candidates),
        key=lambda item: (-item[1].score, item[0]),
    )
    return tuple(candidate for _, candidate in ranked[:max_terms] if isinstance(candidate, WebDictionaryCandidate))


def _fetch_web_search_snippets(game_title: str, game_notes: str, max_results: int = 3) -> tuple[str, ...]:
    query = (f"{game_title} {game_notes}".strip())
    if not query or max_results <= 0:
        return ()
    if max_results > 10:
        max_results = 10
    request = Request(
        _WEB_SEARCH_URL.format(query=quote_plus(query)),
        headers={"User-Agent": "Mozilla/5.0"},
    )
    try:
        with urlopen(request, timeout=_WEB_SEARCH_TIMEOUT_SECONDS) as response:
            html = response.read().decode("utf-8", "ignore")
    except (URLError, OSError, TimeoutError):
        return ()

    snippets: list[str] = []
    for raw in _HTML_SNIPPET_RE.findall(html):
        snippet = _strip_html(str(raw)).strip()
        if snippet:
            snippets.append(snippet)

    if snippets:
        return tuple(snippets[:max_results])

    fallback: list[str] = []
    for raw in re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.IGNORECASE | re.DOTALL):
        snippet = _strip_html(str(raw)).strip()
        if snippet:
            fallback.append(snippet)

    return tuple(fallback[:max_results])




