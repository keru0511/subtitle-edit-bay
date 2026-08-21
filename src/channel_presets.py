from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


PRESET_SCHEMA_VERSION = 1
PRESET_CATEGORIES = {"subtitle", "audio", "short", "export"}


class ChannelPresetError(ValueError):
    pass


@dataclass(frozen=True)
class ChannelPreset:
    name: str
    categories: Mapping[str, Any]
    schema_version: int = PRESET_SCHEMA_VERSION

    def to_json(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "name": self.name, "categories": copy.deepcopy(dict(self.categories))}

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "ChannelPreset":
        if int(payload.get("schema_version", 0)) != PRESET_SCHEMA_VERSION:
            raise ChannelPresetError("unsupported preset schema")
        categories = payload.get("categories")
        if not isinstance(categories, Mapping):
            raise ChannelPresetError("preset categories must be an object")
        unknown = set(categories) - PRESET_CATEGORIES
        if unknown:
            raise ChannelPresetError(f"unknown preset categories: {sorted(unknown)}")
        return cls(str(payload.get("name", "")), copy.deepcopy(dict(categories)))


@dataclass(frozen=True)
class PresetApplyResult:
    settings: dict[str, Any]
    warnings: tuple[str, ...]
    changed_categories: tuple[str, ...]


def create_channel_preset(name: str, settings: Mapping[str, Any], *, categories: Iterable[str] | None = None) -> ChannelPreset:
    selected = set(categories or PRESET_CATEGORIES)
    if not selected <= PRESET_CATEGORIES:
        raise ChannelPresetError("unknown preset category")
    payload = {
        category: _sanitize_category(settings.get(category, {}))
        for category in selected
    }
    return ChannelPreset(str(name), payload)


def diff_channel_preset(
    current: Mapping[str, Any],
    preset: ChannelPreset,
    *,
    categories: Iterable[str] | None = None,
) -> dict[str, dict[str, Any]]:
    selected = set(categories or preset.categories)
    return {
        category: {"before": copy.deepcopy(current.get(category, {})), "after": copy.deepcopy(preset.categories.get(category, {}))}
        for category in selected
        if current.get(category, {}) != preset.categories.get(category, {})
    }


def apply_channel_preset(
    current: Mapping[str, Any],
    preset: ChannelPreset,
    *,
    categories: Iterable[str] | None = None,
    overwrite_manual: bool = False,
) -> PresetApplyResult:
    selected = set(categories or preset.categories)
    if not selected <= PRESET_CATEGORIES:
        raise ChannelPresetError("unknown preset category")
    updated = copy.deepcopy(dict(current))
    warnings: list[str] = []
    changed: list[str] = []
    for category in selected:
        if category not in preset.categories:
            continue
        incoming = copy.deepcopy(dict(preset.categories[category]))
        if category == "audio":
            incoming, audio_warnings = _match_channels(updated.get(category, {}), incoming)
            warnings.extend(audio_warnings)
        if category == "short" and not overwrite_manual:
            incoming = _preserve_manual_short_overrides(updated.get(category, {}), incoming)
        if updated.get(category, {}) != incoming:
            updated[category] = incoming
            changed.append(category)
    return PresetApplyResult(updated, tuple(warnings), tuple(sorted(changed)))


class ChannelPresetStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.presets: dict[str, ChannelPreset] = {}
        self.default_name = ""
        self.load()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, prefix="presets-", suffix=".tmp", delete=False)
        temp_path = Path(handle.name)
        try:
            with handle:
                json.dump({"schema_version": PRESET_SCHEMA_VERSION, "default": self.default_name, "presets": [item.to_json() for item in self.presets.values()]}, handle, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def load(self) -> None:
        if not self.path.is_file():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.default_name = str(payload.get("default", ""))
        self.presets = {preset.name: preset for preset in (ChannelPreset.from_json(item) for item in payload.get("presets", []))}

    def add(self, preset: ChannelPreset) -> None:
        self.presets[preset.name] = preset
        self.save()

    def rename(self, old_name: str, new_name: str) -> None:
        if new_name in self.presets:
            raise ChannelPresetError("preset name already exists")
        preset = self.presets.pop(old_name)
        self.presets[new_name] = ChannelPreset(new_name, preset.categories)
        if self.default_name == old_name:
            self.default_name = new_name
        self.save()

    def delete(self, name: str) -> None:
        self.presets.pop(name)
        if self.default_name == name:
            self.default_name = ""
        self.save()


def _sanitize_category(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_category(item)
            for key, item in value.items()
            if not _is_secret_or_media_path(str(key), item)
        }
    if isinstance(value, list):
        return [_sanitize_category(item) for item in value]
    return value


def _is_secret_or_media_path(key: str, value: Any) -> bool:
    lowered = key.casefold()
    if any(term in lowered for term in ("token", "password", "secret", "api_key", "authorization")):
        return True
    if "path" in lowered and isinstance(value, str):
        return Path(value).is_absolute() or ":\\" in value or value.startswith("/")
    return False


def _match_channels(current: Any, incoming: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(incoming.get("channels"), list) or not isinstance(current, Mapping):
        return incoming, []
    current_channels = current.get("channels", []) if isinstance(current.get("channels", []), list) else []
    indexes = {_channel_key(item): item for item in current_channels if isinstance(item, Mapping)}
    incoming_indexes = {
        _channel_key(item): item
        for item in incoming["channels"]
        if isinstance(item, Mapping)
    }
    warnings: list[str] = []
    matched: list[dict[str, Any]] = []
    for channel in current_channels:
        if not isinstance(channel, Mapping):
            matched.append(channel)
            continue
        key = _channel_key(channel)
        if key in incoming_indexes:
            matched.append({**dict(channel), **dict(incoming_indexes[key])})
        else:
            matched.append(dict(channel))
    for channel in incoming["channels"]:
        key = _channel_key(channel)
        if key not in indexes:
            warnings.append(f"未一致channel: {key}")
    return {**incoming, "channels": matched}, warnings


def _channel_key(value: Mapping[str, Any]) -> str:
    return str(value.get("track_key") or value.get("stable_id") or value.get("name") or value.get("file_name") or "")


def _preserve_manual_short_overrides(current: Any, incoming: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(current, Mapping) or not isinstance(current.get("clips"), list) or not isinstance(incoming.get("clips"), list):
        return incoming
    manual_by_id = {
        str(item.get("segment_id")): item
        for item in current["clips"]
        if isinstance(item, Mapping) and item.get("manual_override")
    }
    incoming_ids = {str(item.get("segment_id")) for item in incoming["clips"] if isinstance(item, Mapping)}
    clips = [manual_by_id.get(str(item.get("segment_id")), item) for item in incoming["clips"]]
    clips.extend(
        item
        for item in current["clips"]
        if isinstance(item, Mapping)
        and item.get("manual_override")
        and str(item.get("segment_id")) not in incoming_ids
    )
    return {**incoming, "clips": clips}

