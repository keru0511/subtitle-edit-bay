from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .transcription_context import TranscriptionContext, transcription_context_from_mapping


class TranscriptionContextConfigError(ValueError):
    """Raised when runtime transcription context configuration cannot be resolved."""


def _context_from_value(value: object) -> TranscriptionContext:
    if value is None:
        return TranscriptionContext()
    if isinstance(value, TranscriptionContext):
        return value
    if isinstance(value, Mapping):
        return transcription_context_from_mapping(value)
    raise TranscriptionContextConfigError("transcription_context must be an object")


def resolve_transcription_context_file_path(
    value: object,
    *,
    base_dir: str | Path | None = None,
) -> Path:
    """Resolve a transcription context JSON file path from CLI/config input."""
    if not isinstance(value, str) or not value.strip():
        raise TranscriptionContextConfigError("transcription_context_file must be a non-empty string")

    candidate = Path(value.strip()).expanduser()
    if candidate.is_absolute() or base_dir is None:
        return candidate
    return Path(base_dir) / candidate


def load_transcription_context_file(
    value: object,
    *,
    base_dir: str | Path | None = None,
) -> TranscriptionContext:
    """Load a transcription context JSON file.

    The file may be either a raw transcription context object or a runtime-config
    shaped object containing a ``transcription_context`` object.
    """
    path = resolve_transcription_context_file_path(value, base_dir=base_dir)
    if not path.is_file():
        raise TranscriptionContextConfigError(f"transcription context file was not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise TranscriptionContextConfigError(f"transcription context file is invalid JSON: {path}") from exc
    if not isinstance(payload, Mapping):
        raise TranscriptionContextConfigError("transcription context file must contain an object")

    context_payload: object = payload.get("transcription_context", payload)
    return _context_from_value(context_payload)


def transcription_context_from_runtime_config(
    config: Mapping[str, Any] | None,
    *,
    cli_context_file: str | None = None,
    base_dir: str | Path | None = None,
) -> TranscriptionContext:
    """Resolve transcription context from runtime config and optional CLI input.

    CLI file input wins over config. Config can provide either:

    - ``transcription_context``: inline object
    - ``transcription_context_file``: JSON file path, resolved relative to ``base_dir``
    """
    if config is None:
        config = {}
    if not isinstance(config, Mapping):
        raise TranscriptionContextConfigError("runtime config must be an object")

    if cli_context_file:
        return load_transcription_context_file(cli_context_file, base_dir=base_dir)

    if "transcription_context_file" in config:
        return load_transcription_context_file(config["transcription_context_file"], base_dir=base_dir)

    return _context_from_value(config.get("transcription_context"))


def normalized_transcription_context_from_runtime_config(
    config: Mapping[str, Any] | None,
    *,
    cli_context_file: str | None = None,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    return transcription_context_from_runtime_config(
        config,
        cli_context_file=cli_context_file,
        base_dir=base_dir,
    ).to_dict()
