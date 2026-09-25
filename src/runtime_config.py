from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TypeVar

from .data_boundary import decode_json, is_object_mapping, is_object_sequence
from .runtime_config_schema import validate_runtime_config_payload

Option = TypeVar("Option")

DEFAULT_RUNTIME_CONFIG = Path(__file__).resolve().parent.parent / "assets" / "runtime_config.json"


def load_runtime_config(config_path: str | Path | None = None) -> dict[str, object]:
    path = Path(config_path) if config_path is not None else DEFAULT_RUNTIME_CONFIG
    if not path.exists():
        return {}
    payload = decode_json(path.read_text(encoding="utf-8-sig"))
    try:
        return validate_runtime_config_payload(payload, discard_unknown=False)
    except ValueError as exc:
        raise SystemExit(f"Invalid runtime config {path}: {exc}") from exc


def load_command_runtime_config(command_name: str, config_path: str | Path | None = None) -> dict[str, object]:
    payload = load_runtime_config(config_path)
    resolved: dict[str, object] = {}
    shared = payload.get("shared", {})
    if is_object_mapping(shared):
        resolved.update((str(key), value) for key, value in shared.items())
    command_config = payload.get(command_name, {})
    if is_object_mapping(command_config):
        resolved.update((str(key), value) for key, value in command_config.items())
    return resolved


def resolve_option(
    value: Option | None, config: Mapping[str, Option], key: str, default: Option | None = None
) -> Option | None:
    if value is not None:
        return value
    if key in config:
        return config[key]
    return default


def resolve_list_option(
    value: list[str] | None, config: Mapping[str, object], key: str, default: list[str] | None = None
) -> list[str]:
    resolved = resolve_option(value, config, key, default)
    if resolved is None:
        return []
    if is_object_sequence(resolved) and isinstance(resolved, list):
        return [str(item) for item in resolved]
    raise SystemExit(f"Config value '{key}' must be a JSON array.")


def resolve_bool_option(value: bool | None, config: Mapping[str, object], key: str, default: bool) -> bool:
    resolved = resolve_option(value, config, key, default)
    if isinstance(resolved, bool):
        return resolved
    raise SystemExit(f"Config value '{key}' must be true or false.")
