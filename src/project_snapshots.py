"""Checksummed project snapshots with explicit restore and retention rules."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from collections.abc import Mapping
from pathlib import Path
from typing import Any


SNAPSHOT_SCHEMA_VERSION = 1
_SECRET_KEYS = {"api_key", "apikey", "access_token", "refresh_token", "password", "secret", "authorization", "credential"}
_MEDIA_KEYS = {"path", "file", "file_path", "video_path", "audio_path", "media_path", "source_path", "thumbnail_path"}


class SnapshotError(ValueError):
    """Raised when a snapshot is invalid or cannot be restored safely."""


def sanitize_project(value: Any, key: str | None = None) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, child in value.items():
            child_key = str(raw_key)
            normalized = child_key.lower().replace("-", "_")
            if normalized in _SECRET_KEYS or any(part in normalized for part in ("api_key", "token", "password", "secret")):
                continue
            if normalized in _MEDIA_KEYS or normalized.endswith("_path"):
                continue
            result[child_key] = sanitize_project(child, normalized)
        return result
    if isinstance(value, list):
        return [sanitize_project(child, key) for child in value]
    if isinstance(value, tuple):
        return [sanitize_project(child, key) for child in value]
    return copy.deepcopy(value)


def _is_media_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in _MEDIA_KEYS or normalized.endswith("_path")


def _merge_media_references(target: Any, current: Any) -> Any:
    """Restore current media references without copying other live fields."""
    if isinstance(current, Mapping):
        result = copy.deepcopy(dict(target)) if isinstance(target, Mapping) else {}
        for raw_key, current_value in current.items():
            key = str(raw_key)
            if _is_media_key(key):
                result[key] = copy.deepcopy(current_value)
            elif isinstance(current_value, (Mapping, list, tuple)):
                result[key] = _merge_media_references(result.get(key), current_value)
        return result
    if isinstance(current, (list, tuple)):
        target_items = list(target) if isinstance(target, (list, tuple)) else []
        result = copy.deepcopy(target_items)
        for index, current_value in enumerate(current):
            existing = result[index] if index < len(result) else None
            merged = _merge_media_references(existing, current_value)
            if index < len(result):
                result[index] = merged
            else:
                result.append(merged)
        return result
    return copy.deepcopy(target)


def project_checksum(project: Mapping[str, Any]) -> str:
    encoded = json.dumps(project, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any], *, overwrite: bool = True) -> None:
    if path.exists() and not overwrite:
        raise SnapshotError(f"destination already exists: {path}")
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


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    revision: Any
    reason: str
    created_at: str
    checksum: str
    project: dict[str, Any]
    pinned: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "snapshot_id": self.snapshot_id,
            "revision": self.revision,
            "reason": self.reason,
            "created_at": self.created_at,
            "checksum": self.checksum,
            "pinned": self.pinned,
            "project": self.project,
        }


def _snapshot_from_payload(payload: Mapping[str, Any]) -> Snapshot:
    if payload.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise SnapshotError("unsupported snapshot schema")
    project = payload.get("project")
    if not isinstance(project, Mapping):
        raise SnapshotError("snapshot project is invalid")
    project_copy = copy.deepcopy(dict(project))
    checksum = str(payload.get("checksum", ""))
    if checksum != project_checksum(project_copy):
        raise SnapshotError("snapshot checksum mismatch")
    return Snapshot(
        snapshot_id=str(payload.get("snapshot_id", "")),
        revision=payload.get("revision"),
        reason=str(payload.get("reason", "")),
        created_at=str(payload.get("created_at", "")),
        checksum=checksum,
        project=project_copy,
        pinned=bool(payload.get("pinned", False)),
    )


class SnapshotStore:
    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        retention_count: int = 30,
        retention_days: int | None = 30,
        retention_bytes: int | None = 250 * 1024 * 1024,
    ) -> None:
        if retention_count < 1:
            raise ValueError("retention_count must be positive")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.retention_count = retention_count
        self.retention_days = retention_days
        self.retention_bytes = retention_bytes

    def _path(self, snapshot_id: str) -> Path:
        if not snapshot_id or Path(snapshot_id).name != snapshot_id:
            raise SnapshotError("invalid snapshot id")
        return self.root / f"snapshot-{snapshot_id}.json"

    def create(
        self,
        project: Mapping[str, Any],
        revision: Any,
        reason: str,
        *,
        pinned: bool = False,
        created_at: str | None = None,
    ) -> Snapshot:
        clean_project = sanitize_project(project)
        if not isinstance(clean_project, Mapping):
            raise SnapshotError("project must be an object")
        snapshot = Snapshot(
            snapshot_id=uuid.uuid4().hex[:16],
            revision=revision,
            reason=str(reason),
            created_at=created_at or datetime.now(timezone.utc).isoformat(),
            checksum=project_checksum(clean_project),
            project=dict(clean_project),
            pinned=pinned,
        )
        _atomic_json(self._path(snapshot.snapshot_id), snapshot.to_json())
        self.prune()
        return snapshot

    def get(self, snapshot_id: str) -> Snapshot:
        path = self._path(snapshot_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SnapshotError(f"unable to read snapshot: {snapshot_id}") from exc
        snapshot = _snapshot_from_payload(payload)
        if snapshot.snapshot_id != snapshot_id:
            raise SnapshotError("snapshot id mismatch")
        return snapshot

    def validate(self, snapshot_id: str) -> bool:
        try:
            self.get(snapshot_id)
        except SnapshotError:
            return False
        return True

    def list(self) -> list[Snapshot]:
        snapshots: list[Snapshot] = []
        for path in self.root.glob("snapshot-*.json"):
            try:
                snapshots.append(self.get(path.stem.removeprefix("snapshot-")))
            except SnapshotError:
                continue
        return sorted(snapshots, key=lambda item: item.created_at, reverse=True)

    def pin(self, snapshot_id: str, pinned: bool = True) -> Snapshot:
        snapshot = self.get(snapshot_id)
        updated = Snapshot(snapshot.snapshot_id, snapshot.revision, snapshot.reason, snapshot.created_at, snapshot.checksum, snapshot.project, pinned)
        _atomic_json(self._path(snapshot_id), updated.to_json())
        return updated

    def diff(self, left_id: str, right_id: str) -> list[dict[str, Any]]:
        left = self.get(left_id).project
        right = self.get(right_id).project
        changes: list[dict[str, Any]] = []

        def visit(path: str, first: Any, second: Any) -> None:
            if isinstance(first, Mapping) and isinstance(second, Mapping):
                for key in sorted(set(first) | set(second)):
                    visit(f"{path}/{key}", first.get(key), second.get(key))
                return
            if first != second:
                changes.append({"path": path or "/", "before": copy.deepcopy(first), "after": copy.deepcopy(second)})

        visit("", left, right)
        return changes

    def restore(
        self,
        snapshot_id: str,
        *,
        current_project: Mapping[str, Any] | None = None,
        current_revision: Any = None,
        destination: str | os.PathLike[str] | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        target_project = copy.deepcopy(self.get(snapshot_id).project)
        if current_project is not None:
            target_project = _merge_media_references(target_project, current_project)
            self.create(current_project, current_revision, "pre-restore")
        if destination is not None:
            path = Path(destination)
            if path.exists() and not overwrite:
                raise SnapshotError(f"restore destination already exists: {path}")
            _atomic_json(path, target_project, overwrite=True)
        return target_project

    def prune(self, *, now: datetime | None = None) -> None:
        current = now or datetime.now(timezone.utc)
        snapshots = self.list()
        removable = [item for item in snapshots if not item.pinned]
        keep_unpinned = set(item.snapshot_id for item in removable[: self.retention_count])
        for snapshot in removable[self.retention_count :]:
            self._path(snapshot.snapshot_id).unlink(missing_ok=True)
        for snapshot in self.list():
            if snapshot.pinned or snapshot.snapshot_id not in keep_unpinned:
                continue
            if self.retention_days is not None:
                try:
                    created = datetime.fromisoformat(snapshot.created_at)
                    if created < current - timedelta(days=self.retention_days):
                        self._path(snapshot.snapshot_id).unlink(missing_ok=True)
                except ValueError:
                    self._path(snapshot.snapshot_id).unlink(missing_ok=True)
        if self.retention_bytes is not None:
            total = sum(path.stat().st_size for path in self.root.glob("snapshot-*.json") if path.is_file())
            for snapshot in sorted(self.list(), key=lambda item: item.created_at):
                if total <= self.retention_bytes or snapshot.pinned:
                    continue
                path = self._path(snapshot.snapshot_id)
                total -= path.stat().st_size
                path.unlink(missing_ok=True)
