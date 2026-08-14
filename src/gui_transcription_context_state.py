from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .transcription_context import TranscriptionContext, normalize_transcription_context, transcription_context_from_mapping
from .transcription_web_dictionary import build_web_dictionary_candidate_metadata, build_web_dictionary_candidates


@dataclass(frozen=True)
class GuiTranscriptionContextState:
    """GUI-friendly editable transcription context state."""

    game_title: str = ""
    game_notes: str = ""
    creator_terms_text: str = ""
    dictionary_path: str = ""
    dictionary_confirmed: bool = False
    web_dictionary_enabled: bool = False
    web_dictionary_candidates: tuple[str, ...] = ()
    web_dictionary_terms: tuple[str, ...] = ()
    web_dictionary_candidate_metadata: tuple[dict[str, str], ...] = ()

    @classmethod
    def from_context(cls, context: TranscriptionContext | Mapping[str, Any] | None) -> "GuiTranscriptionContextState":
        resolved = context if isinstance(context, TranscriptionContext) else transcription_context_from_mapping(context)
        candidates = resolved.web_dictionary_candidates
        if resolved.web_dictionary_enabled and not candidates:
            candidates = build_web_dictionary_candidates(
                resolved.game_title,
                resolved.game_notes,
            )
            metadata = build_web_dictionary_candidate_metadata(
                resolved.game_title,
                resolved.game_notes,
            )
        else:
            metadata = resolved.web_dictionary_candidate_metadata
        return cls(
            game_title=resolved.game_title,
            game_notes=resolved.game_notes,
            creator_terms_text="\n".join(resolved.creator_terms),
            dictionary_path=resolved.dictionary_path or "",
            dictionary_confirmed=resolved.dictionary_confirmed,
            web_dictionary_enabled=resolved.web_dictionary_enabled,
            web_dictionary_candidates=tuple(candidates),
            web_dictionary_terms=tuple(resolved.web_dictionary_terms),
            web_dictionary_candidate_metadata=tuple(metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_title": self.game_title,
            "game_notes": self.game_notes,
            "creator_terms_text": self.creator_terms_text,
            "dictionary_path": self.dictionary_path,
            "dictionary_confirmed": self.dictionary_confirmed,
            "web_dictionary_enabled": self.web_dictionary_enabled,
            "web_dictionary_candidates": list(self.web_dictionary_candidates),
            "web_dictionary_terms": list(self.web_dictionary_terms),
            "web_dictionary_candidate_metadata": [dict(item) for item in self.web_dictionary_candidate_metadata],
        }

    def to_context_payload(self) -> dict[str, Any]:
        return gui_state_to_transcription_context(self.to_dict())


def _split_creator_terms_text(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, str):
        raise TypeError("creator_terms_text must be a string")
    normalized = value.replace("\u3001", "\n").replace("\uff0c", "\n").replace(",", "\n")
    return [term.strip() for term in normalized.splitlines() if term.strip()]


def _normalize_terms_text(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()

    if isinstance(value, str):
        values = value.replace("\u3001", "\n").replace("\uff0c", "\n").replace(",", "\n")
        terms = [term.strip() for term in values.splitlines() if term.strip()]
    elif isinstance(value, Sequence):
        raw_terms: list[str] = []
        for term in value:
            if not isinstance(term, str):
                raise TypeError(f"{field} must be an array of strings")
            stripped = term.strip()
            if stripped:
                raw_terms.append(stripped)
        terms = raw_terms
    else:
        raise TypeError(f"{field} must be an array of strings")

    if not terms:
        return ()
    return tuple(dict.fromkeys(terms).keys())


def _normalize_candidate_metadata(
    value: object,
    field: str,
) -> tuple[dict[str, str], ...]:
    from .transcription_web_dictionary import normalize_web_dictionary_candidate_metadata

    return normalize_web_dictionary_candidate_metadata(value, field, max_items=512)


def gui_transcription_context_state_from_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    if not config:
        return GuiTranscriptionContextState().to_dict()
    craig = config.get("craig_pipeline", {})
    if not isinstance(craig, Mapping):
        return GuiTranscriptionContextState().to_dict()
    return GuiTranscriptionContextState.from_context(craig.get("transcription_context")).to_dict()


def gui_state_to_transcription_context(gui_state: Mapping[str, Any] | None) -> dict[str, Any]:
    if gui_state is None:
        gui_state = {}
    creator_terms: object
    if "creator_terms" in gui_state and gui_state.get("creator_terms") is not None:
        creator_terms = gui_state.get("creator_terms")
    else:
        creator_terms = _split_creator_terms_text(gui_state.get("creator_terms_text", ""))

    payload = {
        "game_title": gui_state.get("game_title", ""),
        "game_notes": gui_state.get("game_notes", ""),
        "creator_terms": creator_terms,
        "dictionary_path": gui_state.get("dictionary_path") or None,
        "dictionary_confirmed": gui_state.get("dictionary_confirmed", False),
        "web_dictionary_enabled": gui_state.get("web_dictionary_enabled", False),
        "web_dictionary_candidates": _normalize_terms_text(gui_state.get("web_dictionary_candidates"), "web_dictionary_candidates"),
        "web_dictionary_terms": _normalize_terms_text(gui_state.get("web_dictionary_terms"), "web_dictionary_terms"),
        "web_dictionary_candidate_metadata": _normalize_candidate_metadata(
            gui_state.get("web_dictionary_candidate_metadata"),
            "web_dictionary_candidate_metadata",
        ),
    }
    return normalize_transcription_context(payload)
