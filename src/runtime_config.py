from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_RUNTIME_CONFIG = Path(__file__).resolve().parent.parent / "assets" / "runtime_config.json"


def load_runtime_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path is not None else DEFAULT_RUNTIME_CONFIG
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise SystemExit(f"Runtime config must be a JSON object: {path}")
    return payload


def load_command_runtime_config(command_name: str, config_path: str | Path | None = None) -> dict[str, Any]:
    payload = load_runtime_config(config_path)
    resolved: dict[str, Any] = {}
    shared = payload.get("shared", {})
    if isinstance(shared, dict):
        resolved.update(shared)
    command_config = payload.get(command_name, {})
    if isinstance(command_config, dict):
        resolved.update(command_config)
    return resolved


def resolve_option(value: Any, config: dict[str, Any], key: str, default: Any = None) -> Any:
    if value is not None:
        return value
    if key in config:
        return config[key]
    return default


def resolve_list_option(value: list[str] | None, config: dict[str, Any], key: str, default: list[str] | None = None) -> list[str]:
    resolved = resolve_option(value, config, key, default)
    if resolved is None:
        return []
    if isinstance(resolved, list):
        return [str(item) for item in resolved]
    raise SystemExit(f"Config value '{key}' must be a JSON array.")


def resolve_bool_option(value: bool | None, config: dict[str, Any], key: str, default: bool) -> bool:
    resolved = resolve_option(value, config, key, default)
    if isinstance(resolved, bool):
        return resolved
    raise SystemExit(f"Config value '{key}' must be true or false.")
