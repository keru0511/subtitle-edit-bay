from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable, Mapping

from .data_boundary import coerce_int, decode_json, is_object_list, is_object_mapping


PRESET_SCHEMA_VERSION = 1
PRESET_CATEGORIES = {"subtitle", "audio", "short", "export"}


class ChannelPresetError(ValueError):
    pass


@dataclass(frozen=True)
class ChannelPreset:
    name: str
    categories: Mapping[str, dict[str, object]]
    schema_version: int = PRESET_SCHEMA_VERSION

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "categories": copy.deepcopy(dict(self.categories)),
        }

    @classmethod
    def from_json(cls, payload: object) -> "ChannelPreset":
        if not is_object_mapping(payload):
            raise ChannelPresetError("preset must be an object")
        if coerce_int(payload.get("schema_version", 0)) != PRESET_SCHEMA_VERSION:
            raise ChannelPresetError("unsupported preset schema")
        categories = payload.get("categories")
        if not is_object_mapping(categories):
            raise ChannelPresetError("preset categories must be an object")
        unknown = {str(category) for category in categories if category not in PRESET_CATEGORIES}
        if unknown:
            raise ChannelPresetError(f"unknown preset categories: {sorted(unknown)}")
        validated_categories: dict[str, dict[str, object]] = {}
        for category, value in categories.items():
            if not isinstance(category, str) or not is_object_mapping(value):
                raise ChannelPresetError("preset category must be an object")
            validated_categories[category] = _copy_string_mapping(value)
        return cls(str(payload.get("name", "")), validated_categories)


@dataclass(frozen=True)
class PresetApplyResult:
    settings: dict[str, object]
    warnings: tuple[str, ...]
    changed_categories: tuple[str, ...]


def create_channel_preset(
    name: str, settings: Mapping[str, object], *, categories: Iterable[str] | None = None
) -> ChannelPreset:
    selected = set(categories or PRESET_CATEGORIES)
    if not selected <= PRESET_CATEGORIES:
        raise ChannelPresetError("unknown preset category")
    payload: dict[str, dict[str, object]] = {}
    for category in selected:
        raw_category = settings.get(category, {})
        if not is_object_mapping(raw_category):
            raise ChannelPresetError("preset category must be an object")
        payload[category] = _sanitize_mapping(raw_category)
    return ChannelPreset(str(name), payload)


def diff_channel_preset(
    current: Mapping[str, object],
    preset: ChannelPreset,
    *,
    categories: Iterable[str] | None = None,
) -> dict[str, dict[str, object]]:
    selected = set(categories or preset.categories)
    return {
        category: {
            "before": copy.deepcopy(current.get(category, {})),
            "after": copy.deepcopy(preset.categories.get(category, {})),
        }
        for category in selected
        if current.get(category, {}) != preset.categories.get(category, {})
    }


def apply_channel_preset(
    current: Mapping[str, object],
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
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.path.parent, prefix="presets-", suffix=".tmp", delete=False
        )
        temp_path = Path(handle.name)
        try:
            with handle:
                payload: dict[str, object] = {
                    "schema_version": PRESET_SCHEMA_VERSION,
                    "default": self.default_name,
                    "presets": [item.to_json() for item in self.presets.values()],
                }
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def load(self) -> None:
        if not self.path.is_file():
            return
        payload = decode_json(self.path.read_text(encoding="utf-8"))
        if not is_object_mapping(payload):
            raise ChannelPresetError("preset store must be an object")
        stored_presets = payload.get("presets", [])
        if not is_object_list(stored_presets):
            raise ChannelPresetError("presets must be an array")
        presets = (ChannelPreset.from_json(item) for item in stored_presets)
        self.presets = {preset.name: preset for preset in presets}
        self.default_name = str(payload.get("default", ""))

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


def _copy_string_mapping(value: Mapping[object, object]) -> dict[str, object]:
    if not all(isinstance(key, str) for key in value):
        raise ChannelPresetError("preset category keys must be strings")
    return {key: copy.deepcopy(item) for key, item in value.items() if isinstance(key, str)}


def _sanitize_mapping(value: Mapping[object, object]) -> dict[str, object]:
    return {
        str(key): _sanitize_category(item)
        for key, item in value.items()
        if not _is_secret_or_media_path(str(key), item)
    }


def _sanitize_category(value: object) -> object:
    if is_object_mapping(value):
        return _sanitize_mapping(value)
    if is_object_list(value):
        return [_sanitize_category(item) for item in value]
    return value


def _is_secret_or_media_path(key: str, value: object) -> bool:
    lowered = key.casefold()
    if any(term in lowered for term in ("token", "password", "secret", "api_key", "authorization")):
        return True
    if "path" in lowered and isinstance(value, str):
        return PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute()
    return False


def _match_channels(current: object, incoming: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    incoming_channels = incoming.get("channels")
    if not is_object_list(incoming_channels) or not is_object_mapping(current):
        return incoming, []
    raw_current_channels = current.get("channels", [])
    current_channels = raw_current_channels if is_object_list(raw_current_channels) else []
    indexes = {_channel_key(item): item for item in current_channels if is_object_mapping(item)}
    incoming_indexes = {_channel_key(item): item for item in incoming_channels if is_object_mapping(item)}
    warnings: list[str] = []
    matched: list[object] = []
    for channel in current_channels:
        if not is_object_mapping(channel):
            matched.append(channel)
            continue
        key = _channel_key(channel)
        if key in incoming_indexes:
            matched.append({**dict(channel), **dict(incoming_indexes[key])})
        else:
            matched.append(dict(channel))
    for channel in incoming_channels:
        if not is_object_mapping(channel):
            raise ChannelPresetError("audio channel must be an object")
        key = _channel_key(channel)
        if key not in indexes:
            warnings.append(f"未一致channel: {key}")
    return {**incoming, "channels": matched}, warnings


def _channel_key(value: Mapping[object, object]) -> str:
    return str(value.get("track_key") or value.get("stable_id") or value.get("name") or value.get("file_name") or "")


def _preserve_manual_short_overrides(current: object, incoming: dict[str, object]) -> dict[str, object]:
    if not is_object_mapping(current):
        return incoming
    current_clips = current.get("clips")
    incoming_clips = incoming.get("clips")
    if not is_object_list(current_clips) or not is_object_list(incoming_clips):
        return incoming
    manual_by_id = {
        str(item.get("segment_id")): item
        for item in current_clips
        if is_object_mapping(item) and item.get("manual_override")
    }
    incoming_ids = {str(item.get("segment_id")) for item in incoming_clips if is_object_mapping(item)}
    clips: list[object] = []
    for item in incoming_clips:
        if not is_object_mapping(item):
            raise ChannelPresetError("short clip must be an object")
        clips.append(manual_by_id.get(str(item.get("segment_id")), item))
    clips.extend(
        item
        for item in current_clips
        if is_object_mapping(item) and item.get("manual_override") and str(item.get("segment_id")) not in incoming_ids
    )
    return {**incoming, "clips": clips}
