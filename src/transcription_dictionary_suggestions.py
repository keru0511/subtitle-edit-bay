from __future__ import annotations

import difflib
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .transcription_dictionary import DictionaryTerm, TranscriptionDictionary


SUGGESTION_VERSION = "correction-token-v1"
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[一-龥ぁ-んァ-ヶー]+")


@dataclass(frozen=True)
class DictionarySuggestion:
    before: str
    after: str
    context: str
    speaker: str
    project_ids: tuple[str, ...]
    occurrence_count: int
    confidence: float
    reason: str
    scope: str = "game"
    status: str = "pending"
    extractor_version: str = SUGGESTION_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "before": self.before,
            "after": self.after,
            "context": self.context,
            "speaker": self.speaker,
            "project_ids": list(self.project_ids),
            "occurrence_count": self.occurrence_count,
            "confidence": self.confidence,
            "reason": self.reason,
            "scope": self.scope,
            "status": self.status,
            "extractor_version": self.extractor_version,
        }


def extract_dictionary_suggestions(
    corrections: Iterable[Mapping[str, Any]],
    *,
    existing_terms: Iterable[str] = (),
) -> list[DictionarySuggestion]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    existing = {str(item) for item in existing_terms}
    for correction in corrections:
        original = str(correction.get("original_text", ""))
        corrected = str(correction.get("corrected_text", ""))
        if not original.strip() or not corrected.strip() or _normalize_for_compare(original) == _normalize_for_compare(corrected):
            continue
        replacements = _token_replacements(original, corrected)
        if len(replacements) != 1:
            continue
        before, after = replacements[0]
        if not _valid_term_pair(before, after):
            continue
        key = (before, after)
        entry = grouped.setdefault(
            key,
            {
                "contexts": [],
                "speaker": str(correction.get("speaker", "")),
                "projects": set(),
                "count": 0,
            },
        )
        context = " ".join(str(correction.get(key, "")) for key in ("context_before", "context_after") if correction.get(key))
        if context:
            entry["contexts"].append(context[:120])
        project_id = str(correction.get("project_id", ""))
        if project_id:
            entry["projects"].add(project_id)
        entry["count"] += 1

    suggestions: list[DictionarySuggestion] = []
    for (before, after), entry in grouped.items():
        count = int(entry["count"])
        project_count = len(entry["projects"])
        duplicate_penalty = 0.15 if after in existing or before in existing else 0.0
        confidence = max(0.0, min(1.0, 0.45 + min(0.35, count * 0.08) + min(0.2, project_count * 0.1) - duplicate_penalty))
        reason = f"{count}回の手修正"
        if project_count > 1:
            reason += f"、{project_count}プロジェクトで再現"
        if duplicate_penalty:
            reason += "、既存辞書との重複を確認"
        suggestions.append(
            DictionarySuggestion(
                before=before,
                after=after,
                context=entry["contexts"][0] if entry["contexts"] else "",
                speaker=entry["speaker"],
                project_ids=tuple(sorted(entry["projects"])),
                occurrence_count=count,
                confidence=round(confidence, 4),
                reason=reason,
            )
        )
    return sorted(suggestions, key=lambda item: (-item.confidence, -item.occurrence_count, item.before, item.after))


def apply_dictionary_suggestion(
    dictionary: TranscriptionDictionary,
    suggestion: DictionarySuggestion,
    *,
    scope: str | None = None,
) -> TranscriptionDictionary:
    target_scope = scope or suggestion.scope
    if target_scope not in {"global", "game", "project"}:
        raise ValueError("scope must be global, game, or project")
    terms = list(dictionary.terms)
    for term in terms:
        if suggestion.after == term.term or suggestion.after in term.aliases:
            raise ValueError(f"dictionary term already exists: {suggestion.after}")
        if suggestion.after in term.aliases or suggestion.before == term.term:
            raise ValueError(f"dictionary term conflicts with: {term.term}")
    terms.append(
        DictionaryTerm(
            term=suggestion.after,
            aliases=(suggestion.before,),
            type_hint="suggested",
            enabled=False,
            score=suggestion.confidence,
        )
    )
    return TranscriptionDictionary(game_title=dictionary.game_title, terms=tuple(terms))


def _token_replacements(original: str, corrected: str) -> list[tuple[str, str]]:
    before = _TOKEN_PATTERN.findall(original)
    after = _TOKEN_PATTERN.findall(corrected)
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    replacements: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace" and i2 - i1 == 1 and j2 - j1 == 1:
            replacements.append((before[i1], after[j1]))
        elif tag in {"delete", "insert", "replace"}:
            return []
    return replacements


def _normalize_for_compare(value: str) -> str:
    return re.sub(r"[\s、。！？!?.,，．]+", "", value)


def _valid_term_pair(before: str, after: str) -> bool:
    return (
        1 <= len(before) <= 32
        and 1 <= len(after) <= 32
        and before != after
        and not any(char in before + after for char in "\r\n")
    )
