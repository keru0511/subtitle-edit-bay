from __future__ import annotations

import json
from pathlib import Path

DEFAULT_SPEAKER_COLOR_CONFIG = Path(__file__).resolve().parent.parent / "assets" / "speaker_colors.json"


def normalize_color_key(value: str) -> str:
    return str(value).strip().casefold()


def load_color_entries(entries: dict) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for name, entry in entries.items():
        if isinstance(entry, str):
            color = entry
            aliases: list[str] = []
        else:
            color = str(entry["color"])
            aliases = [str(alias) for alias in entry.get("aliases", [])]
        mapping[normalize_color_key(name)] = color
        for alias in aliases:
            mapping[normalize_color_key(alias)] = color
    return mapping


def load_speaker_color_map(config_path: str | Path | None = None) -> dict[str, str]:
    path = Path(config_path) if config_path is not None else DEFAULT_SPEAKER_COLOR_CONFIG
    if not path.exists():
        return {}

    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    mapping: dict[str, str] = {}
    mapping.update(load_color_entries(payload.get("speakers", {})))
    mapping.update(load_color_entries(payload.get("files", {})))
    return mapping
