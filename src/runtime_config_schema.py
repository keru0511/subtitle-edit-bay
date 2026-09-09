from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


STRING = "string"
NULLABLE_STRING = "nullable_string"
BOOLEAN = "boolean"
INTEGER = "integer"
NUMBER = "number"
STRING_ARRAY = "string_array"
OBJECT = "object"


# This is the runtime configuration contract shared by the application loader
# and migrations. It is intentionally independent from the default values file:
# defaults may omit valid persisted settings (for example ``codex_model``), and
# a null default does not describe the accepted non-null type.
COMMON_RUNTIME_SETTINGS: dict[str, str] = {
    "codex_model": STRING,
    "model": STRING,
    "device": STRING,
    "compute_type": STRING,
    "language": STRING,
    "vad_onset": NUMBER,
    "vad_offset": NUMBER,
    "skip_existing_transcripts": BOOLEAN,
    "width": INTEGER,
    "height": INTEGER,
    "video_codec": STRING,
    "audio_codec": STRING,
    "output_audio_track": STRING,
    "nvenc_preset": STRING,
    "nvenc_cq": INTEGER,
    "x264_crf": INTEGER,
    "audio_normalize": BOOLEAN,
    "audio_target_lufs": NUMBER,
    "audio_loudness_range": NUMBER,
    "audio_true_peak_db": NUMBER,
    "cut_no_speech": BOOLEAN,
    "no_speech_min_seconds": NUMBER,
    "speech_padding_seconds": NUMBER,
    "speech_threshold_db": STRING,
    "speech_min_clip_seconds": NUMBER,
    "subtitle_font_size": INTEGER,
    "subtitle_outline_color": STRING,
    "subtitle_outline_thickness": INTEGER,
    "subtitle_max_gap_seconds": NUMBER,
    "subtitle_end_padding_seconds": NUMBER,
    "subtitle_min_duration_seconds": NUMBER,
    "subtitle_volume_scale_percent": NUMBER,
    "reference_track": NULLABLE_STRING,
    "reference_audio": NULLABLE_STRING,
    "alignment_sample_rate": INTEGER,
    "alignment_offset_adjustment": NUMBER,
    "postprocess_workers": INTEGER,
    "short_mode_enabled": BOOLEAN,
    "short_mode_output_width": INTEGER,
    "short_mode_output_height": INTEGER,
    "short_mode_output_fps": INTEGER,
    "short_mode_global_fit": STRING,
    "short_mode_global_background_color": STRING,
    "short_mode_transition_type": STRING,
    "short_mode_transition_duration": NUMBER,
    "short_mode_bgm_path": STRING,
    "short_mode_bgm_in": NUMBER,
    "short_mode_bgm_out": NUMBER,
    "short_mode_bgm_start": NUMBER,
    "short_mode_bgm_volume": NUMBER,
    "short_mode_subtitle_scale_percent": NUMBER,
}

COMMAND_SETTINGS: dict[str, str] = {
    "input_dir": STRING,
    "input_root": STRING,
    "output_dir": STRING,
    "export_root": STRING,
    "audio_track": STRING_ARRAY,
    "diarize_track": STRING_ARRAY,
    "min_speakers": INTEGER,
    "max_speakers": INTEGER,
    "track_color": STRING_ARRAY,
    "op_file": STRING,
    "ed_file": STRING,
    "transcription_context": OBJECT,
}

RUNTIME_CONFIG_SCHEMA: dict[str, dict[str, str]] = {
    "shared": dict(COMMON_RUNTIME_SETTINGS),
    "pipeline": {**COMMON_RUNTIME_SETTINGS, **COMMAND_SETTINGS},
    "batch": {**COMMON_RUNTIME_SETTINGS, **COMMAND_SETTINGS},
    "craig_pipeline": {**COMMON_RUNTIME_SETTINGS, **COMMAND_SETTINGS},
}


def _validate_value(value: object, kind: str, path: str) -> None:
    valid = False
    if kind == STRING:
        valid = isinstance(value, str)
    elif kind == NULLABLE_STRING:
        valid = value is None or isinstance(value, str)
    elif kind == BOOLEAN:
        valid = isinstance(value, bool)
    elif kind == INTEGER:
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif kind == NUMBER:
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif kind == STRING_ARRAY:
        valid = isinstance(value, list) and all(isinstance(item, str) for item in value)
    elif kind == OBJECT:
        valid = isinstance(value, Mapping)
    if not valid:
        raise ValueError(f"Runtime config value has invalid type: {path} ({kind})")


def validate_runtime_config_payload(
    payload: object,
    *,
    discard_unknown: bool,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("Runtime config root must be an object")
    result: dict[str, Any] = {}
    for section_name, section in payload.items():
        schema = RUNTIME_CONFIG_SCHEMA.get(str(section_name))
        if schema is None:
            if not discard_unknown:
                result[str(section_name)] = deepcopy(section)
            continue
        if not isinstance(section, Mapping):
            if discard_unknown:
                raise ValueError(f"Runtime config section must be an object: {section_name}")
            # Preserve the application's longstanding behaviour: command
            # loading ignores non-object sections. Migrations remain strict.
            result[str(section_name)] = deepcopy(section)
            continue
        migrated_section: dict[str, Any] = {}
        for key, value in section.items():
            kind = schema.get(str(key))
            if kind is None:
                if not discard_unknown:
                    migrated_section[str(key)] = deepcopy(value)
                continue
            _validate_value(value, kind, f"{section_name}.{key}")
            migrated_section[str(key)] = deepcopy(value)
        result[str(section_name)] = migrated_section
    return result
