from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


EDIT_MODES = ("subtitle", "cut", "audio")
TIMELINE_BASES = ("source", "output")


class TimeMapping(Protocol):
    """Boundary implemented by the cut editor when output timing diverges."""

    def source_to_output(self, position_ms: int) -> int: ...

    def output_to_source(self, position_ms: int) -> int: ...


@dataclass(frozen=True)
class IdentityTimeMapping:
    def source_to_output(self, position_ms: int) -> int:
        return position_ms

    def output_to_source(self, position_ms: int) -> int:
        return position_ms


@dataclass(frozen=True)
class EditModeCapabilities:
    can_preview: bool
    can_edit_subtitles: bool
    can_cut: bool
    can_mix_audio: bool

    def mode_available(self, mode: str) -> bool:
        return {
            "subtitle": self.can_edit_subtitles,
            "cut": self.can_cut,
            "audio": self.can_mix_audio,
        }.get(mode, False)

    def as_dict(self) -> dict[str, object]:
        return {
            "canPreview": self.can_preview,
            "canEditSubtitles": self.can_edit_subtitles,
            "canCut": self.can_cut,
            "canMixAudio": self.can_mix_audio,
            "subtitleReason": "" if self.can_edit_subtitles else "動画プロジェクトを開いてください",
            "cutReason": "" if self.can_cut else "カット編集は準備中です",
            "audioReason": "" if self.can_mix_audio else "音声トラックがないため利用できません",
        }


def build_edit_mode_capabilities(
    *,
    project_loaded: bool,
    preview_available: bool,
    audio_available: bool,
    cut_available: bool = False,
) -> EditModeCapabilities:
    """Build independent capabilities; transcription dependencies are irrelevant here."""

    return EditModeCapabilities(
        can_preview=project_loaded and preview_available,
        can_edit_subtitles=project_loaded and preview_available,
        can_cut=project_loaded and preview_available and cut_available,
        can_mix_audio=project_loaded and preview_available and audio_available,
    )


class EditorWorkspaceState:
    def __init__(self, mapping: TimeMapping | None = None) -> None:
        self._mapping: TimeMapping = mapping or IdentityTimeMapping()
        self._current_mode = "subtitle"
        self._timeline_basis = "source"
        self._source_position_ms = 0
        self._output_position_ms = 0

    @property
    def current_mode(self) -> str:
        return self._current_mode

    @property
    def playhead(self) -> dict[str, object]:
        return {
            "basis": self._timeline_basis,
            "sourcePositionMs": self._source_position_ms,
            "outputPositionMs": self._output_position_ms,
        }

    def select_mode(self, mode: str, capabilities: EditModeCapabilities) -> bool:
        if mode not in EDIT_MODES or not capabilities.mode_available(mode):
            return False
        if self._current_mode == mode:
            return False
        self._current_mode = mode
        return True

    def ensure_available_mode(self, capabilities: EditModeCapabilities) -> bool:
        if capabilities.mode_available(self._current_mode):
            return False
        for mode in EDIT_MODES:
            if capabilities.mode_available(mode):
                self._current_mode = mode
                return True
        if self._current_mode != "subtitle":
            self._current_mode = "subtitle"
            return True
        return False

    def set_playhead(self, position_ms: int, basis: str) -> bool:
        if basis not in TIMELINE_BASES:
            return False
        position_ms = max(0, int(position_ms))
        if basis == "source":
            source_position = position_ms
            output_position = max(0, int(self._mapping.source_to_output(position_ms)))
        else:
            output_position = position_ms
            source_position = max(0, int(self._mapping.output_to_source(position_ms)))
        changed = (
            self._timeline_basis != basis
            or self._source_position_ms != source_position
            or self._output_position_ms != output_position
        )
        self._timeline_basis = basis
        self._source_position_ms = source_position
        self._output_position_ms = output_position
        return changed

    def set_mapping(self, mapping: TimeMapping | None) -> bool:
        self._mapping = mapping or IdentityTimeMapping()
        return self.set_playhead(self._source_position_ms, "source")

    def reset_playhead(self) -> bool:
        return self.set_playhead(0, "source")
