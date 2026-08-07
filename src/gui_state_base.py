from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .color_config import load_speaker_color_map

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".mov", ".webm"}
AUDIO_EXTENSIONS = {".aac", ".flac", ".wav", ".m4a"}
DEFAULT_SPEAKER_COLORS = ["#FFD966", "#F6B26B", "#93C47D", "#6FA8DC", "#E78284", "#81C8BE"]
SOURCE_CONFIG_KEYS = {"video", "audio_dir", "audio_file", "output_dir", "reference_audio", "reference_track", "target"}


@dataclass(frozen=True)
class SourceSelection:
    video: str = ""
    output_dir: str = ""
    audio_files: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["audio_files"] = list(self.audio_files)
        return payload


def build_speaker_entries_from_files(
    audio_files: list[str | Path] | tuple[str | Path, ...],
    color_config_path: str | Path | None = None,
) -> list[dict[str, str]]:
    color_map = load_speaker_color_map(color_config_path)
    supported_files = sorted(
        (Path(path) for path in audio_files if Path(path).suffix.lower() in AUDIO_EXTENSIONS),
        key=lambda path: (path.name.casefold(), str(path).casefold()),
    )

    entries: list[dict[str, str]] = []
    for index, audio_file in enumerate(supported_files):
        stem_parts = audio_file.stem.split("-", 1)
        speaker_name = stem_parts[1] if len(stem_parts) == 2 else audio_file.stem
        color = (
            color_map.get(audio_file.name.casefold())
            or color_map.get(speaker_name.casefold())
            or DEFAULT_SPEAKER_COLORS[index % len(DEFAULT_SPEAKER_COLORS)]
        )
        entries.append({
            "name": speaker_name,
            "file_name": audio_file.name,
            "path": str(audio_file.resolve()),
            "color": color.upper(),
            "track_key": f"craig:{speaker_name}",
        })
    return entries


def build_gui_runtime_config(
    base_config: dict[str, Any],
    settings: dict[str, Any],
    speakers: list[dict[str, str]],
) -> dict[str, Any]:
    payload = json.loads(json.dumps(base_config))
    shared = payload.setdefault("shared", {})
    craig = payload.setdefault("craig_pipeline", {})

    for key in (
        "model", "device", "compute_type", "language", "nvenc_cq", "x264_crf",
        "subtitle_font_size", "subtitle_outline_color", "subtitle_outline_thickness",
        "subtitle_max_gap_seconds", "subtitle_end_padding_seconds", "subtitle_min_duration_seconds",
    ):
        if key in settings:
            shared[key] = settings[key]

    for key in (
        "video_codec", "audio_normalize", "audio_target_lufs", "cut_no_speech",
        "subtitle_volume_scale_percent",
        "no_speech_min_seconds", "speech_padding_seconds", "postprocess_workers",
        "alignment_offset_adjustment",
    ):
        if key in settings:
            craig[key] = settings[key]

    for key in SOURCE_CONFIG_KEYS:
        craig.pop(key, None)
    craig["track_color"] = [f"{speaker['track_key']}={speaker['color']}" for speaker in speakers]
    return payload


def write_gui_runtime_config(path: str | Path, payload: dict[str, Any]) -> Path:
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
    command.extend([
        "--alignment-offset-adjustment", str(alignment_offset_adjustment),
        "--config", str(config_path),
        "--run",
    ])
    return command