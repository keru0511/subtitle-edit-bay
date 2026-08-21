"""Crash-recovery journal for newer, checksummed project state."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.project_snapshots import (
    SnapshotError,
    _merge_media_references,
    project_checksum,
    sanitize_project,
)


class RecoveryError(ValueError):
    """Raised when recovery state is invalid."""


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
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


def _revision_number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class RecoveryJournal:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.path = self.root / "pending-recovery.json"

    def record(self, project: Mapping[str, Any], revision: Any, reason: str = "crash") -> None:
        clean_project = sanitize_project(project)
        if not isinstance(clean_project, Mapping):
            raise RecoveryError("project must be an object")
        payload = {
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

    def pending(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RecoveryError("unable to read recovery journal") from exc
        project = payload.get("project")
        if payload.get("schema_version") != 1 or not isinstance(project, Mapping):
            raise RecoveryError("invalid recovery journal")
        if payload.get("checksum") != project_checksum(project):
            raise RecoveryError("recovery checksum mismatch")
        return copy.deepcopy(payload)

    def candidate(self, current_revision: Any) -> dict[str, Any] | None:
        payload = self.pending()
        if payload is None:
            return None
        stored = _revision_number(payload.get("revision"))
        current = _revision_number(current_revision)
        if stored is None or current is None or stored <= current:
            return None
        return payload

    def restore_if_newer(self, current_project: Mapping[str, Any], current_revision: Any) -> dict[str, Any] | None:
        payload = self.candidate(current_revision)
        if payload is None:
            return None
        restored = _merge_media_references(payload["project"], current_project)
        self.clear()
        return restored
