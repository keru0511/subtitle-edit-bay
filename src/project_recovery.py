"""Crash-recovery journal for newer, checksummed project state."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

from .data_boundary import coerce_int, decode_json, is_object_mapping

from src.project_snapshots import (
    _merge_media_references,
    project_checksum,
    sanitize_project,
)


_ProjectKey = TypeVar("_ProjectKey")


class RecoveryError(ValueError):
    """Raised when recovery state is invalid."""


def _atomic_json(path: Path, payload: Mapping[object, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _revision_number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return coerce_int(value)
    except (TypeError, ValueError):
        return None


class RecoveryJournal:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.path = self.root / "pending-recovery.json"

    def record(self, project: Mapping[_ProjectKey, object], revision: object, reason: str = "crash") -> None:
        clean_project = sanitize_project(project)
        if not is_object_mapping(clean_project):
            raise RecoveryError("project must be an object")
        payload: dict[object, object] = {
            "schema_version": 1,
            "revision": revision,
            "reason": reason,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "checksum": project_checksum(clean_project),
            "project": dict(clean_project),
        }
        _atomic_json(self.path, payload)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def pending(self) -> dict[object, object] | None:
        if not self.path.exists():
            return None
        try:
            payload = decode_json(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RecoveryError("unable to read recovery journal") from exc
        if not is_object_mapping(payload):
            raise RecoveryError("invalid recovery journal")
        project = payload.get("project")
        if payload.get("schema_version") != 1 or not is_object_mapping(project):
            raise RecoveryError("invalid recovery journal")
        if payload.get("checksum") != project_checksum(project):
            raise RecoveryError("recovery checksum mismatch")
        return copy.deepcopy(dict(payload))

    def candidate(self, current_revision: object) -> dict[object, object] | None:
        payload = self.pending()
        if payload is None:
            return None
        stored = _revision_number(payload.get("revision"))
        current = _revision_number(current_revision)
        if stored is None or current is None or stored <= current:
            return None
        return payload

    def restore_if_newer(
        self, current_project: Mapping[_ProjectKey, object], current_revision: object
    ) -> dict[object, object] | None:
        payload = self.candidate(current_revision)
        if payload is None:
            return None
        restored = _merge_media_references(payload["project"], current_project)
        if not is_object_mapping(restored):
            raise RecoveryError("restored project must be an object")
        self.clear()
        return dict(restored)
