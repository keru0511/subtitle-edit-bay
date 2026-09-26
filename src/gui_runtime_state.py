from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Mapping, cast

from .data_boundary import decode_json, is_object_dict
from .gui_source_state import SOURCE_CONFIG_KEYS
from .runtime_settings import gui_runtime_config_updates
from .transcription_context import normalize_transcription_context


def _config_section(payload: dict[str, object], name: str) -> dict[str, object]:
    value = payload.setdefault(name, {})
    if not is_object_dict(value) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"runtime config section must be an object: {name}")
    return cast(dict[str, object], value)


def build_gui_runtime_config(
    base_config: dict[str, object],
    settings: dict[str, object],
    speakers: list[dict[str, str]],
    transcription_context: Mapping[str, object] | None = None,
) -> dict[str, object]:
    payload_value = decode_json(json.dumps(base_config))
    if not is_object_dict(payload_value) or not all(isinstance(key, str) for key in payload_value):
        raise ValueError("runtime config must be an object")
    payload = cast(dict[str, object], payload_value)
    shared = _config_section(payload, "shared")
    craig = _config_section(payload, "craig_pipeline")

    shared_updates, craig_updates = gui_runtime_config_updates(settings)
    shared.update(shared_updates)
    craig.update(craig_updates)

    if transcription_context is not None:
        craig["transcription_context"] = normalize_transcription_context(transcription_context)

    for key in SOURCE_CONFIG_KEYS:
        craig.pop(key, None)
    craig["track_color"] = [f"{speaker['track_key']}={speaker['color']}" for speaker in speakers]
    return payload


def write_gui_runtime_config(path: str | Path, payload: dict[str, object]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def build_gui_command(
    config_path: str | Path,
    *,
    video: str,
    audio_files: list[str] | tuple[str, ...],
    output_dir: str,
    reference_audio: str | None = None,
    reference_track: str | None = None,
    alignment_offset_adjustment: float = 0.0,
) -> list[str]:
    if not video or not audio_files or not output_dir:
        raise ValueError("video, audio_files, and output_dir are required")

    command = [sys.executable, "-u", "-m", "src.craig_pipeline", "--video", video]
    for audio_file in audio_files:
        command.extend(["--audio-file", audio_file])
    command.extend(["--output-dir", output_dir])
    if reference_audio:
        command.extend(["--reference-audio", reference_audio])
    if reference_track:
        command.extend(["--reference-track", reference_track])
    command.extend(
        [
            "--alignment-offset-adjustment",
            str(alignment_offset_adjustment),
            "--config",
            str(config_path),
            "--run",
        ]
    )
    return command
