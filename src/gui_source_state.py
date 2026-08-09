from __future__ import annotations

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
        entries.append(
            {
                "name": speaker_name,
                "file_name": audio_file.name,
                "path": str(audio_file.resolve()),
                "color": color.upper(),
                "track_key": f"craig:{speaker_name}",
            }
        )
    return entries
