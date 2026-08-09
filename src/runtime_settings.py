from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .runtime_config import load_command_runtime_config

RuntimeConfig = Mapping[str, Any]


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
class RuntimeSettings:
    transcription: TranscriptionSettings
    subtitle_layout: SubtitleLayoutSettings
    video_export: VideoExportSettings
    audio_normalize: AudioNormalizeSettings
    silence_cut: SilenceCutSettings
    alignment: AlignmentSettings


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
    ):
        flattened.update(asdict(group))
    return flattened
