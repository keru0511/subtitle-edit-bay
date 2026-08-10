from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

CACHE_METADATA_SCHEMA_VERSION = 1


def _stable_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_payload_hash(payload: Mapping[str, Any] | Sequence[str] | str | None) -> str:
    if payload is None:
        normalized: Any = None
    elif isinstance(payload, str):
        normalized = payload
    elif isinstance(payload, Mapping):
        normalized = json.loads(_stable_json(payload))
    else:
        normalized = list(payload)
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_transcript_cache_fingerprint(
    *,
    model: str,
    device: str,
    compute_type: str,
    language: str,
    vad_onset: float | None,
    vad_offset: float | None,
    initial_prompt: str = "",
    hotwords: Sequence[str] = (),
    dictionary_hash: str = "",
    game_title: str = "",
    whisperx_version: str = "",
) -> str:
    payload = {
        "model": model,
        "device": device,
        "compute_type": compute_type,
        "language": language,
        "vad_onset": vad_onset,
        "vad_offset": vad_offset,
        "initial_prompt_hash": stable_payload_hash(initial_prompt),
        "hotwords_hash": stable_payload_hash(tuple(hotwords)),
        "dictionary_hash": dictionary_hash,
        "game_title": game_title,
        "whisperx_version": whisperx_version,
    }
    return stable_payload_hash(payload)


def transcript_cache_metadata_path(transcript_path: str | Path) -> Path:
    path = Path(transcript_path)
    return path.with_name(f"{path.name}.cache.json")


def write_transcript_cache_metadata(
    transcript_path: str | Path,
    *,
    fingerprint: str,
    settings: Mapping[str, Any] | None = None,
) -> Path:
    metadata_path = transcript_cache_metadata_path(transcript_path)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema_version": CACHE_METADATA_SCHEMA_VERSION,
        "fingerprint": fingerprint,
        "settings": dict(settings or {}),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata_path


def read_transcript_cache_metadata(transcript_path: str | Path) -> dict[str, Any] | None:
    metadata_path = transcript_cache_metadata_path(transcript_path)
    if not metadata_path.exists():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(metadata, dict):
        return None
    if metadata.get("schema_version") != CACHE_METADATA_SCHEMA_VERSION:
        return None
    if not isinstance(metadata.get("fingerprint"), str):
        return None
    return metadata


def transcript_cache_is_valid(
    transcript_path: str | Path,
    *,
    expected_fingerprint: str | None = None,
) -> bool:
    path = Path(transcript_path)
    if not path.exists():
        return False
    if not expected_fingerprint:
        return True
    metadata = read_transcript_cache_metadata(path)
    if metadata is None:
        return False
    return metadata["fingerprint"] == expected_fingerprint
