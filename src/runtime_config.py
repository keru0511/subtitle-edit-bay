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
    value: object, config: Mapping[str, object], key: str, default: list[str] | None = None
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


def resolve_string_option(
    value: str | None, config: Mapping[str, object], key: str, default: str | None = None
) -> str | None:
    resolved = resolve_option(value, config, key, default)
    if resolved is None or isinstance(resolved, str):
        return resolved
    raise SystemExit(f"Config value '{key}' must be a string or null.")


def resolve_required_string_option(value: str | None, config: Mapping[str, object], key: str, default: str) -> str:
    resolved = resolve_string_option(value, config, key, default)
    if resolved is None:
        raise SystemExit(f"Config value '{key}' must be a string.")
    return resolved


def resolve_integer_option(
    value: int | None, config: Mapping[str, object], key: str, default: int | None = None
) -> int | None:
    resolved = resolve_option(value, config, key, default)
    if resolved is None or type(resolved) is int:
        return resolved
    raise SystemExit(f"Config value '{key}' must be an integer or null.")


def resolve_required_integer_option(value: int | None, config: Mapping[str, object], key: str, default: int) -> int:
    resolved = resolve_integer_option(value, config, key, default)
    if resolved is None:
        raise SystemExit(f"Config value '{key}' must be an integer.")
    return resolved


def resolve_number_option(
    value: float | None, config: Mapping[str, object], key: str, default: float | None = None
) -> float | None:
    resolved = resolve_option(value, config, key, default)
    if resolved is None:
        return None
    if isinstance(resolved, (int, float)) and not isinstance(resolved, bool):
        return float(resolved)
    raise SystemExit(f"Config value '{key}' must be a number or null.")


def resolve_required_number_option(
    value: float | None, config: Mapping[str, object], key: str, default: float
) -> float:
    resolved = resolve_number_option(value, config, key, default)
    if resolved is None:
        raise SystemExit(f"Config value '{key}' must be a number.")
    return resolved
