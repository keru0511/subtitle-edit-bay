from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, cast

from .ass_template import (
    DEFAULT_SUBTITLE_FONT_SIZE,
    DEFAULT_SUBTITLE_OUTLINE_COLOR,
    DEFAULT_SUBTITLE_OUTLINE_THICKNESS,
)
from .color_config import normalize_rgb_color
from .data_boundary import coerce_float, coerce_int, is_object_mapping
from .gui_runtime_state import build_gui_runtime_config, write_gui_runtime_config
from .gui_source_state import SOURCE_CONFIG_KEYS
from .gui_transcription_context_state import (
    gui_state_to_transcription_context,
    gui_transcription_context_state_from_config,
)
from .runtime_config import DEFAULT_RUNTIME_CONFIG, load_runtime_config
from .runtime_settings import DEFAULT_SUBTITLE_VOLUME_SCALE_PERCENT


@dataclass(frozen=True)
class SettingsControllerState:
    """The settings state exposed to the Qt facade without importing Qt."""

    config: dict[str, object]
    settings: dict[str, object]
    transcription_context: dict[str, object]


def _settings_section(payload: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = payload.get(name, {})
    if not is_object_mapping(value) or not all(isinstance(key, str) for key in value):
        raise TypeError(f"runtime config section must be an object: {name}")
    return cast(Mapping[str, object], value)


class SettingsController:
    """Own GUI settings persistence and runtime-config construction.

    This controller deliberately has no dependency on the Qt backend.  The
    backend remains responsible for signals, status messages, source state,
    projects, and process execution; it only delegates settings work here.
    """

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        config_path: str | Path | None = None,
        base_config_path: str | Path | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.gui_config_path = (
            Path(config_path) if config_path is not None else self.workspace_root / ".gui" / "runtime_config.json"
        )
        default_path = base_config_path if base_config_path is not None else DEFAULT_RUNTIME_CONFIG
        self.base_config = load_runtime_config(default_path)
        self.config = load_runtime_config(self.gui_config_path) if self.gui_config_path.exists() else self.base_config
        self.settings = self.settings_from_config(self.config)
        self.transcription_context = gui_transcription_context_state_from_config(self.config)

    @staticmethod
    def settings_from_config(payload: Mapping[str, object]) -> dict[str, object]:
        """Normalize persisted sections into the settings map used by QML."""

        shared = _settings_section(payload, "shared")
        craig = _settings_section(payload, "craig_pipeline")
        return {
            "codex_model": shared.get("codex_model", ""),
            "gemini_model": shared.get("gemini_model", ""),
            "ai_provider": shared.get("ai_provider", "codex"),
            "model": shared.get("model", "large-v3"),
            "device": shared.get("device", "cuda"),
            "compute_type": shared.get("compute_type", "float16"),
            "language": shared.get("language", "ja"),
            "nvenc_cq": coerce_int(shared.get("nvenc_cq", 18)),
            "x264_crf": coerce_int(shared.get("x264_crf", 18)),
            "subtitle_font_size": coerce_int(shared.get("subtitle_font_size", DEFAULT_SUBTITLE_FONT_SIZE)),
            "subtitle_outline_color": normalize_rgb_color(
                shared.get("subtitle_outline_color", DEFAULT_SUBTITLE_OUTLINE_COLOR)
            ),
            "subtitle_outline_thickness": coerce_int(
                shared.get("subtitle_outline_thickness", DEFAULT_SUBTITLE_OUTLINE_THICKNESS)
            ),
            "subtitle_volume_scale_percent": coerce_float(
                craig.get("subtitle_volume_scale_percent", DEFAULT_SUBTITLE_VOLUME_SCALE_PERCENT)
            ),
            "subtitle_max_gap_seconds": coerce_float(shared.get("subtitle_max_gap_seconds", 0.1)),
            "subtitle_end_padding_seconds": coerce_float(shared.get("subtitle_end_padding_seconds", 0.08)),
            "subtitle_min_duration_seconds": coerce_float(shared.get("subtitle_min_duration_seconds", 0.35)),
            "video_codec": craig.get("video_codec", "h264_nvenc"),
            "audio_normalize": bool(craig.get("audio_normalize", True)),
            "audio_target_lufs": coerce_float(craig.get("audio_target_lufs", -16.0)),
            "cut_no_speech": bool(craig.get("cut_no_speech", False)),
            "no_speech_min_seconds": coerce_float(craig.get("no_speech_min_seconds", 1.2)),
            "speech_padding_seconds": coerce_float(craig.get("speech_padding_seconds", 0.25)),
            "postprocess_workers": coerce_int(craig.get("postprocess_workers", 4)),
            "alignment_offset_adjustment": coerce_float(craig.get("alignment_offset_adjustment", 0.0)),
        }

    @staticmethod
    def normalize_transcription_context(context: Mapping[str, object] | None) -> dict[str, object]:
        runtime_context = gui_state_to_transcription_context(context)
        return gui_transcription_context_state_from_config(
            {"craig_pipeline": {"transcription_context": runtime_context}}
        )

    def build_runtime_config(
        self,
        settings: Mapping[str, object],
        speakers: list[dict[str, str]],
        *,
        transcription_context: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Build a persisted runtime config from the current GUI settings."""

        return build_gui_runtime_config(
            self.base_config,
            dict(settings),
            speakers,
            transcription_context=transcription_context,
        )

    def snapshot(self) -> SettingsControllerState:
        return SettingsControllerState(
            config=self.config,
            settings=self.settings,
            transcription_context=self.transcription_context,
        )

    def save_settings(
        self,
        incoming_settings: Mapping[str, object],
        speakers: list[dict[str, str]],
    ) -> tuple[SettingsControllerState, bool]:
        """Normalize, persist, and publish settings state.

        The boolean in the return value tells the Qt facade whether the
        transcription-context change signal should be emitted.  Signal
        dispatch remains outside this controller so it stays Qt-independent.
        """

        persistent_settings = dict(incoming_settings)
        incoming_context = persistent_settings.pop("transcription_context", None)
        context_changed = incoming_context is not None
        next_context = self.transcription_context
        if context_changed:
            if not is_object_mapping(incoming_context) or not all(isinstance(key, str) for key in incoming_context):
                raise TypeError("transcription_context must be an object")
            next_context = self.normalize_transcription_context(cast(Mapping[str, object], incoming_context))

        for key in SOURCE_CONFIG_KEYS:
            persistent_settings.pop(key, None)

        next_settings = dict(self.settings)
        next_settings.update(persistent_settings)
        transcription_context = gui_state_to_transcription_context(next_context)
        payload = self.build_runtime_config(
            next_settings,
            speakers,
            transcription_context=transcription_context,
        )
        write_gui_runtime_config(self.gui_config_path, payload)

        self.settings = next_settings
        self.transcription_context = next_context
        self.config = payload
        return self.snapshot(), context_changed
