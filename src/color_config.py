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


def normalize_rgb_color(value: object) -> str:
    normalized = str(value).strip()
    if normalized.startswith("#"):
        normalized = normalized[1:]
    if len(normalized) == 8:
        normalized = normalized[-6:]
    if len(normalized) != 6 or any(char not in "0123456789abcdefABCDEF" for char in normalized):
        raise ValueError(f"Unsupported RGB color: {value}")
    return f"#{normalized.upper()}"


def _entry_with_color(entry: object, color: str) -> dict:
    updated = dict(entry) if isinstance(entry, dict) else {}
    updated["color"] = color
    return updated


def save_speaker_color(
    config_path: str | Path,
    *,
    file_name: str,
    speaker_name: str,
    color: object,
) -> Path:
    path = Path(config_path)
    normalized = normalize_rgb_color(color)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("speaker color config root must be an object")
    else:
        payload = {}

    files = payload.setdefault("files", {})
    speakers = payload.setdefault("speakers", {})
    if not isinstance(files, dict) or not isinstance(speakers, dict):
        raise ValueError("speaker color config files and speakers must be objects")
    if file_name:
        files[file_name] = _entry_with_color(files.get(file_name), normalized)
    if speaker_name:
        speakers[speaker_name] = _entry_with_color(speakers.get(speaker_name), normalized)
    if not file_name and not speaker_name:
        raise ValueError("file_name or speaker_name is required")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path
