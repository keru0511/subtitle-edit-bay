from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .ass_template import DEFAULT_SUBTITLE_OUTLINE_COLOR, DEFAULT_SUBTITLE_OUTLINE_THICKNESS
from .runtime_config import load_command_runtime_config

VALID_SHORT_FIT_MODES = ("cover", "contain", "blur")
VALID_SHORT_TRANSITION_TYPES = ("crossfade", "fade", "cut")

RuntimeConfig = Mapping[str, Any]
DEFAULT_POSTPROCESS_WORKERS = max(1, min(4, os.cpu_count() or 1))


@dataclass(frozen=True)
class TranscriptionSettings:
    model: str = "large-v3"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str = "ja"
    vad_onset: float = 0.35
    vad_offset: float = 0.2
    skip_existing_transcripts: bool = True


@dataclass(frozen=True)
class SubtitleLayoutSettings:
    subtitle_font_size: int = 50
    subtitle_outline_color: str = DEFAULT_SUBTITLE_OUTLINE_COLOR
    subtitle_outline_thickness: int = DEFAULT_SUBTITLE_OUTLINE_THICKNESS
    subtitle_max_gap_seconds: float = 0.32
    subtitle_end_padding_seconds: float = 0.08
    subtitle_min_duration_seconds: float = 0.35
    subtitle_volume_scale_percent: float = 20.0


@dataclass(frozen=True)
class VideoExportSettings:
    width: int = 1920
    height: int = 1080
    video_codec: str = "libx264"
    audio_codec: str = "copy"
    output_audio_track: str = "0:a:0"
    nvenc_preset: str = "p5"
    nvenc_cq: int = 18
    x264_crf: int = 18


@dataclass(frozen=True)
class AudioNormalizeSettings:
    audio_normalize: bool = True
    audio_target_lufs: float = -16.0
    audio_loudness_range: float = 11.0
    audio_true_peak_db: float = -1.5


@dataclass(frozen=True)
class SilenceCutSettings:
    cut_no_speech: bool = False
    no_speech_min_seconds: float = 1.2
    speech_padding_seconds: float = 0.25
    speech_threshold_db: str = "-40dB"
    speech_min_clip_seconds: float = 0.25


@dataclass(frozen=True)
class AlignmentSettings:
    reference_track: str | None = None
    reference_audio: str | None = None
    alignment_sample_rate: int = 120
    alignment_offset_adjustment: float = 0.0


@dataclass(frozen=True)
class PipelineSettings:
    postprocess_workers: int = DEFAULT_POSTPROCESS_WORKERS


@dataclass(frozen=True)
class ShortModeSettings:
    short_mode_enabled: bool = False
    short_mode_output_width: int = 1080
    short_mode_output_height: int = 1920
    short_mode_output_fps: int = 30
    short_mode_global_fit: str = "cover"
    short_mode_global_background_color: str = "000000"
    short_mode_transition_type: str = "crossfade"
    short_mode_transition_duration: float = 0.5
    short_mode_bgm_path: str = ""
    short_mode_bgm_in: float = 0.0
    short_mode_bgm_out: float = 0.0
    short_mode_bgm_start: float = 0.0
    short_mode_bgm_volume: float = 0.3
    short_mode_subtitle_scale_percent: float = 150.0


@dataclass(frozen=True)
class RuntimeSettings:
    transcription: TranscriptionSettings
    subtitle_layout: SubtitleLayoutSettings
    video_export: VideoExportSettings
    audio_normalize: AudioNormalizeSettings
    silence_cut: SilenceCutSettings
    alignment: AlignmentSettings
    pipeline: PipelineSettings
    short_video: ShortModeSettings


TRANSCRIBE_OPTION_KEYS = (
    "alignment_sample_rate",
    "alignment_offset_adjustment",
    "model",
    "device",
    "compute_type",
    "language",
    "vad_onset",
    "vad_offset",
    "skip_existing_transcripts",
    "postprocess_workers",
    "subtitle_font_size",
    "subtitle_outline_color",
    "subtitle_outline_thickness",
    "subtitle_volume_scale_percent",
    "subtitle_max_gap_seconds",
    "subtitle_end_padding_seconds",
    "subtitle_min_duration_seconds",
)

RENDER_OPTION_KEYS = (
    "video_codec",
    "audio_codec",
    "output_audio_track",
    "nvenc_preset",
    "nvenc_cq",
    "x264_crf",
    "audio_normalize",
    "audio_target_lufs",
    "audio_loudness_range",
    "audio_true_peak_db",
    "cut_no_speech",
    "no_speech_min_seconds",
    "speech_padding_seconds",
    "speech_threshold_db",
    "speech_min_clip_seconds",
)

PERSISTED_RENDER_SETTING_KEYS = (
    "video_codec",
    "audio_codec",
    "output_audio_track",
    "nvenc_preset",
    "nvenc_cq",
    "x264_crf",
    "audio_normalize",
    "audio_target_lufs",
    "cut_no_speech",
    "no_speech_min_seconds",
    "speech_padding_seconds",
    "speech_threshold_db",
    "speech_min_clip_seconds",
)

GUI_SHARED_SETTING_KEYS = (
    "model",
    "device",
    "compute_type",
    "language",
    "nvenc_cq",
    "x264_crf",
    "subtitle_font_size",
    "subtitle_outline_color",
    "subtitle_outline_thickness",
    "subtitle_max_gap_seconds",
    "subtitle_end_padding_seconds",
    "subtitle_min_duration_seconds",
)

GUI_CRAIG_PIPELINE_SETTING_KEYS = (
    "video_codec",
    "audio_normalize",
    "audio_target_lufs",
    "cut_no_speech",
    "subtitle_volume_scale_percent",
    "no_speech_min_seconds",
    "speech_padding_seconds",
    "speech_threshold_db",
    "alignment_offset_adjustment",
    "postprocess_workers",
)


def _raw(config: RuntimeConfig, key: str, default: Any) -> Any:
    if key in config:
        return config[key]
    return default


def _str(config: RuntimeConfig, key: str, default: str) -> str:
    value = _raw(config, key, default)
    if isinstance(value, str):
        return value
    raise ValueError(f"Runtime setting '{key}' must be a string.")


def _optional_str(config: RuntimeConfig, key: str, default: str | None = None) -> str | None:
    value = _raw(config, key, default)
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"Runtime setting '{key}' must be a string or null.")


def _int(config: RuntimeConfig, key: str, default: int) -> int:
    value = _raw(config, key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Runtime setting '{key}' must be an integer.")
    return value


def _float(config: RuntimeConfig, key: str, default: float) -> float:
    value = _raw(config, key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Runtime setting '{key}' must be a number.")
    return float(value)


def _bool(config: RuntimeConfig, key: str, default: bool) -> bool:
    value = _raw(config, key, default)
    if isinstance(value, bool):
        return value
    raise ValueError(f"Runtime setting '{key}' must be true or false.")


def _choice(config: RuntimeConfig, key: str, choices: Sequence[str], default: str) -> str:
    value = _str(config, key, default)
    if value not in choices:
        raise ValueError(f"Runtime setting '{key}' must be one of {choices}.")
    return value


def settings_from_config(config: RuntimeConfig) -> RuntimeSettings:
    return RuntimeSettings(
        transcription=TranscriptionSettings(
            model=_str(config, "model", TranscriptionSettings.model),
            device=_str(config, "device", TranscriptionSettings.device),
            compute_type=_str(config, "compute_type", TranscriptionSettings.compute_type),
            language=_str(config, "language", TranscriptionSettings.language),
            vad_onset=_float(config, "vad_onset", TranscriptionSettings.vad_onset),
            vad_offset=_float(config, "vad_offset", TranscriptionSettings.vad_offset),
            skip_existing_transcripts=_bool(
                config,
                "skip_existing_transcripts",
                TranscriptionSettings.skip_existing_transcripts,
            ),
        ),
        subtitle_layout=SubtitleLayoutSettings(
            subtitle_font_size=_int(config, "subtitle_font_size", SubtitleLayoutSettings.subtitle_font_size),
            subtitle_outline_color=_str(
                config,
                "subtitle_outline_color",
                SubtitleLayoutSettings.subtitle_outline_color,
            ),
            subtitle_outline_thickness=_int(
                config,
                "subtitle_outline_thickness",
                SubtitleLayoutSettings.subtitle_outline_thickness,
            ),
            subtitle_max_gap_seconds=_float(
                config,
                "subtitle_max_gap_seconds",
                SubtitleLayoutSettings.subtitle_max_gap_seconds,
            ),
            subtitle_end_padding_seconds=_float(
                config,
                "subtitle_end_padding_seconds",
                SubtitleLayoutSettings.subtitle_end_padding_seconds,
            ),
            subtitle_min_duration_seconds=_float(
                config,
                "subtitle_min_duration_seconds",
                SubtitleLayoutSettings.subtitle_min_duration_seconds,
            ),
            subtitle_volume_scale_percent=_float(
                config,
                "subtitle_volume_scale_percent",
                SubtitleLayoutSettings.subtitle_volume_scale_percent,
            ),
        ),
        video_export=VideoExportSettings(
            width=_int(config, "width", VideoExportSettings.width),
            height=_int(config, "height", VideoExportSettings.height),
            video_codec=_str(config, "video_codec", VideoExportSettings.video_codec),
            audio_codec=_str(config, "audio_codec", VideoExportSettings.audio_codec),
            output_audio_track=_str(config, "output_audio_track", VideoExportSettings.output_audio_track),
            nvenc_preset=_str(config, "nvenc_preset", VideoExportSettings.nvenc_preset),
            nvenc_cq=_int(config, "nvenc_cq", VideoExportSettings.nvenc_cq),
            x264_crf=_int(config, "x264_crf", VideoExportSettings.x264_crf),
        ),
        audio_normalize=AudioNormalizeSettings(
            audio_normalize=_bool(config, "audio_normalize", AudioNormalizeSettings.audio_normalize),
            audio_target_lufs=_float(config, "audio_target_lufs", AudioNormalizeSettings.audio_target_lufs),
            audio_loudness_range=_float(
                config,
                "audio_loudness_range",
                AudioNormalizeSettings.audio_loudness_range,
            ),
            audio_true_peak_db=_float(config, "audio_true_peak_db", AudioNormalizeSettings.audio_true_peak_db),
        ),
        silence_cut=SilenceCutSettings(
            cut_no_speech=_bool(config, "cut_no_speech", SilenceCutSettings.cut_no_speech),
            no_speech_min_seconds=_float(config, "no_speech_min_seconds", SilenceCutSettings.no_speech_min_seconds),
            speech_padding_seconds=_float(
                config,
                "speech_padding_seconds",
                SilenceCutSettings.speech_padding_seconds,
            ),
            speech_threshold_db=_str(config, "speech_threshold_db", SilenceCutSettings.speech_threshold_db),
            speech_min_clip_seconds=_float(
                config,
                "speech_min_clip_seconds",
                SilenceCutSettings.speech_min_clip_seconds,
            ),
        ),
        alignment=AlignmentSettings(
            reference_track=_optional_str(config, "reference_track", AlignmentSettings.reference_track),
            reference_audio=_optional_str(config, "reference_audio", AlignmentSettings.reference_audio),
            alignment_sample_rate=_int(config, "alignment_sample_rate", AlignmentSettings.alignment_sample_rate),
            alignment_offset_adjustment=_float(
                config,
                "alignment_offset_adjustment",
                AlignmentSettings.alignment_offset_adjustment,
            ),
        ),
        pipeline=PipelineSettings(
            postprocess_workers=_int(config, "postprocess_workers", PipelineSettings.postprocess_workers),
        ),
        short_video=ShortModeSettings(
            short_mode_enabled=_bool(config, "short_mode_enabled", ShortModeSettings.short_mode_enabled),
            short_mode_output_width=_int(config, "short_mode_output_width", ShortModeSettings.short_mode_output_width),
            short_mode_output_height=_int(config, "short_mode_output_height", ShortModeSettings.short_mode_output_height),
            short_mode_output_fps=_int(config, "short_mode_output_fps", ShortModeSettings.short_mode_output_fps),
            short_mode_global_fit=_choice(config, "short_mode_global_fit", VALID_SHORT_FIT_MODES, ShortModeSettings.short_mode_global_fit),
            short_mode_global_background_color=_str(config, "short_mode_global_background_color", ShortModeSettings.short_mode_global_background_color),
            short_mode_transition_type=_choice(config, "short_mode_transition_type", VALID_SHORT_TRANSITION_TYPES, ShortModeSettings.short_mode_transition_type),
            short_mode_transition_duration=_float(config, "short_mode_transition_duration", ShortModeSettings.short_mode_transition_duration),
            short_mode_bgm_path=_str(config, "short_mode_bgm_path", ShortModeSettings.short_mode_bgm_path),
            short_mode_bgm_in=_float(config, "short_mode_bgm_in", ShortModeSettings.short_mode_bgm_in),
            short_mode_bgm_out=_float(config, "short_mode_bgm_out", ShortModeSettings.short_mode_bgm_out),
            short_mode_bgm_start=_float(config, "short_mode_bgm_start", ShortModeSettings.short_mode_bgm_start),
            short_mode_bgm_volume=_float(config, "short_mode_bgm_volume", ShortModeSettings.short_mode_bgm_volume),
            short_mode_subtitle_scale_percent=_float(config, "short_mode_subtitle_scale_percent", ShortModeSettings.short_mode_subtitle_scale_percent),
        ),
    )


def load_runtime_settings(command_name: str, config_path: str | Path | None = None) -> RuntimeSettings:
    return settings_from_config(load_command_runtime_config(command_name, config_path))


def settings_to_flat_dict(settings: RuntimeSettings) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for group in (
        settings.transcription,
        settings.subtitle_layout,
        settings.video_export,
        settings.audio_normalize,
        settings.silence_cut,
        settings.alignment,
        settings.pipeline,
        settings.short_video,
    ):
        flattened.update(asdict(group))
    return flattened


def select_runtime_options(settings: RuntimeSettings, keys: Sequence[str]) -> dict[str, Any]:
    flattened = settings_to_flat_dict(settings)
    return {key: flattened[key] for key in keys}


def transcribe_runtime_options(settings: RuntimeSettings) -> dict[str, Any]:
    return select_runtime_options(settings, TRANSCRIBE_OPTION_KEYS)


def render_runtime_options(settings: RuntimeSettings) -> dict[str, Any]:
    return select_runtime_options(settings, RENDER_OPTION_KEYS)


def configured_render_settings(settings: RuntimeSettings, config: RuntimeConfig) -> dict[str, Any]:
    flattened = settings_to_flat_dict(settings)
    return {key: flattened[key] for key in PERSISTED_RENDER_SETTING_KEYS if key in config}


def gui_runtime_config_updates(settings: RuntimeConfig) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {key: settings[key] for key in GUI_SHARED_SETTING_KEYS if key in settings},
        {key: settings[key] for key in GUI_CRAIG_PIPELINE_SETTING_KEYS if key in settings},
    )
