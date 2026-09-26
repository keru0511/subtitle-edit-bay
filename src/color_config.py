from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from .data_boundary import decode_json, is_object_dict, is_object_iterable, is_object_mapping

DEFAULT_SPEAKER_COLOR_CONFIG = Path(__file__).resolve().parent.parent / "assets" / "speaker_colors.json"


def _mapping(value: object) -> Mapping[object, object]:
    if not is_object_mapping(value):
        raise TypeError("speaker color entries must be a mapping")
    return value


def normalize_color_key(value: object) -> str:
    return str(value).strip().casefold()


def load_color_entries(entries: object) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for name, entry in _mapping(entries).items():
        if isinstance(entry, str):
            color = entry
            aliases: list[str] = []
        else:
            detail = _mapping(entry)
            color = str(detail["color"])
            raw_aliases = detail.get("aliases", [])
            if not is_object_iterable(raw_aliases):
                raise TypeError("speaker color aliases must be iterable")
            aliases = [str(alias) for alias in raw_aliases]
        mapping[normalize_color_key(name)] = color
        for alias in aliases:
            mapping[normalize_color_key(alias)] = color
    return mapping


def load_speaker_color_map(config_path: str | Path | None = None) -> dict[str, str]:
    path = Path(config_path) if config_path is not None else DEFAULT_SPEAKER_COLOR_CONFIG
    if not path.exists():
        return {}

    payload = _mapping(decode_json(path.read_text(encoding="utf-8-sig")))
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


def _entry_with_color(entry: object, color: str) -> dict[object, object]:
    updated: dict[object, object] = dict(entry) if is_object_dict(entry) else {}
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
    payload: dict[object, object]
    if path.exists():
        raw_payload = decode_json(path.read_text(encoding="utf-8-sig"))
        if not is_object_dict(raw_payload):
            raise ValueError("speaker color config root must be an object")
        payload = raw_payload
    else:
        payload = {}

    files = payload.setdefault("files", {})
    speakers = payload.setdefault("speakers", {})
    if not is_object_dict(files) or not is_object_dict(speakers):
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
