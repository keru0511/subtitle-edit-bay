"""旧環境のファイル構成を安全に調査し、移行対象を列挙する。"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .legacy_migration_types import (
    CATEGORY_CACHE,
    CATEGORY_PROJECT,
    DISCOVERY_NOT_DISCOVERABLE,
    DISCOVERY_ROOT_RELATIVE,
    INVENTORY_SCHEMA_VERSION,
    InventoryAction,
    InventoryEntry,
    LegacyInventory,
    LegacyInventoryError,
    LegacyMigrationPlan,
    LegacySettingsMigrationPlan,
    MAX_SIZE_SCAN_ENTRIES,
    MigrationError,
    RuntimeCapabilities,
    SCOPE_EXTERNAL,
    SCOPE_LEGACY_ROOT,
    SETTING_DICTIONARY,
    SETTING_PRESET,
    SETTING_RUNTIME_CONFIG,
    SETTING_SPEAKER_COLORS,
    SETTING_USER_SETTINGS,
    STATUS_MISSING,
    STATUS_NOT_DISCOVERABLE,
    STATUS_PRESENT,
    STATUS_UNREADABLE,
    STATUS_UNSAFE,
    SettingsMigrationOptions,
    _CANDIDATE_PATHS,
    _EXTERNAL_CACHE_CANDIDATES,
    _PROJECT_FILE_SUFFIXES,
)


@dataclass(frozen=True, slots=True)
class _PathObservation:
    status: str
    node_type: str
    exists: bool
    safe: bool
    size_bytes: int | None
    size_is_approximate: bool
    diagnostic: str | None


def _lexical_absolute(path_value: str | os.PathLike[str]) -> Path:
    """Return an absolute lexical path without following its final link."""

    try:
        raw = os.fspath(path_value)
        if isinstance(raw, bytes):
            raw = os.fsdecode(raw)
        return Path(os.path.abspath(os.path.expanduser(raw)))
    except (TypeError, ValueError, OSError) as exc:
        raise LegacyInventoryError(f"invalid legacy root path: {path_value!r}") from exc


def _safe_lstat(path: Path) -> tuple[os.stat_result | None, str | None]:
    try:
        return os.lstat(path), None
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError, TypeError) as exc:
        return None, f"unable to inspect path: {exc}"


def _is_link_like(path: Path, metadata: os.stat_result) -> bool:
    if stat.S_ISLNK(metadata.st_mode) or path.is_symlink():
        return True
    # Windows junctions are reparse points.  Do not follow them during an
    # inventory even on Python versions without Path.is_junction().
    reparse_point = 0x400
    return bool(getattr(metadata, "st_file_attributes", 0) & reparse_point)


def _size_without_following_links(path: Path) -> tuple[int, str | None]:
    """Best-effort directory size using lstat/scandir without link traversal."""

    total = 0
    scanned = 0
    pending = [path]
    diagnostic: str | None = None
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as children:
                for child in children:
                    scanned += 1
                    if scanned > MAX_SIZE_SCAN_ENTRIES:
                        return total, (
                            f"directory size scan stopped after {MAX_SIZE_SCAN_ENTRIES} entries; size is partial"
                        )
                    child_path = Path(child.path)
                    metadata, error = _safe_lstat(child_path)
                    if error:
                        diagnostic = error
                        continue
                    if metadata is None:
                        continue
                    if _is_link_like(child_path, metadata):
                        diagnostic = "symbolic link or junction skipped during size scan"
                        continue
                    if stat.S_ISREG(metadata.st_mode):
                        total += metadata.st_size
                    elif stat.S_ISDIR(metadata.st_mode):
                        pending.append(child_path)
        except (OSError, ValueError, TypeError) as exc:
            diagnostic = f"unable to enumerate directory: {exc}"
    return total, diagnostic


def _observe_path(path: Path) -> _PathObservation:
    metadata, error = _safe_lstat(path)
    if error:
        return _PathObservation(
            STATUS_UNREADABLE,
            "unknown",
            False,
            False,
            None,
            False,
            error,
        )
    if metadata is None:
        return _PathObservation(STATUS_MISSING, "missing", False, True, None, False, None)
    if _is_link_like(path, metadata):
        return _PathObservation(
            STATUS_UNSAFE,
            "symlink",
            True,
            False,
            None,
            False,
            "symbolic link or junction is not followed",
        )
    if stat.S_ISREG(metadata.st_mode):
        return _PathObservation(STATUS_PRESENT, "file", True, True, metadata.st_size, False, None)
    if stat.S_ISDIR(metadata.st_mode):
        size_bytes, diagnostic = _size_without_following_links(path)
        return _PathObservation(
            STATUS_PRESENT,
            "directory",
            True,
            True,
            size_bytes,
            True,
            diagnostic,
        )
    return _PathObservation(
        STATUS_UNSAFE,
        "special",
        True,
        False,
        None,
        False,
        "special filesystem node is not inspected",
    )


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise LegacyInventoryError(f"candidate path escapes legacy root: {path}") from exc


def _entry_for(root: Path, category: str, relative: str) -> InventoryEntry:
    candidate = root / relative
    # Candidate definitions are module-owned constants, but keep the check at
    # the boundary so future additions cannot accidentally introduce ``..``.
    relative_path = _relative_path(root, candidate)
    observation = _observe_path(candidate)
    return InventoryEntry(
        category=category,
        relative_path=relative_path,
        path=str(candidate),
        status=observation.status,
        node_type=observation.node_type,
        exists=observation.exists,
        safe=observation.safe,
        size_bytes=observation.size_bytes,
        size_is_approximate=observation.size_is_approximate,
        diagnostic=observation.diagnostic,
        candidate_id=f"legacy:{relative_path}",
        scope=SCOPE_LEGACY_ROOT,
        discovery=DISCOVERY_ROOT_RELATIVE,
    )


def _external_cache_entries() -> tuple[InventoryEntry, ...]:
    """Return explicit user-cache capabilities without guessing their paths."""

    entries: list[InventoryEntry] = []
    for candidate_id, label in _EXTERNAL_CACHE_CANDIDATES:
        entries.append(
            InventoryEntry(
                category=CATEGORY_CACHE,
                relative_path=f"external-cache/{candidate_id}",
                path="",
                status=STATUS_NOT_DISCOVERABLE,
                node_type="external",
                exists=False,
                safe=True,
                size_bytes=None,
                size_is_approximate=False,
                diagnostic=(
                    f"{label} is outside the selected legacy root and was not inspected; it is not treated as missing"
                ),
                candidate_id=candidate_id,
                scope=SCOPE_EXTERNAL,
                discovery=DISCOVERY_NOT_DISCOVERABLE,
            )
        )
    return tuple(entries)


def _project_file_candidates(root: Path) -> Iterable[tuple[str, str]]:
    """Yield only known project filename suffixes from the selected root."""

    try:
        with os.scandir(root) as children:
            for child in children:
                name = child.name
                if name.endswith(_PROJECT_FILE_SUFFIXES):
                    yield CATEGORY_PROJECT, name
    except (OSError, ValueError, TypeError):
        # The root diagnostic is recorded separately; no broad scan or retry is
        # appropriate for an unreadable/malformed legacy root.
        return


def _root_observation(root: Path) -> _PathObservation:
    observation = _observe_path(root)
    if observation.exists and observation.node_type != "directory":
        return _PathObservation(
            observation.status,
            observation.node_type,
            observation.exists,
            False,
            None,
            False,
            "legacy root must be a regular directory",
        )
    return observation


def build_legacy_inventory(legacy_root: str | os.PathLike[str]) -> LegacyInventory:
    """Inspect a user-selected legacy root without executing or changing it."""

    try:
        root = _lexical_absolute(legacy_root)
    except LegacyInventoryError as exc:
        # Preserve a JSON-serializable diagnostic for malformed user input
        # instead of requiring callers to catch an error during discovery.
        raw = str(legacy_root)
        external_entries = _external_cache_entries()
        return LegacyInventory(
            INVENTORY_SCHEMA_VERSION,
            raw,
            STATUS_UNREADABLE,
            "unknown",
            False,
            False,
            external_entries,
            tuple([str(exc), *(entry.diagnostic or "" for entry in external_entries)]),
        )

    root_observation = _root_observation(root)
    diagnostics: list[str] = []
    if root_observation.diagnostic:
        diagnostics.append(root_observation.diagnostic)
    if root_observation.status == STATUS_UNSAFE:
        diagnostics.append("legacy root is a symbolic link, junction, or special node; candidates were not followed")
    elif root_observation.exists and root_observation.node_type != "directory":
        diagnostics.append("legacy root must be a regular directory")

    entries: list[InventoryEntry] = []
    if root_observation.exists and root_observation.node_type == "directory" and root_observation.safe:
        candidates = list(_CANDIDATE_PATHS)
        candidates.extend(_project_file_candidates(root))
        seen: set[tuple[str, str]] = set()
        for category, relative in sorted(candidates, key=lambda item: (item[1], item[0])):
            key = (category, relative)
            if key in seen:
                continue
            seen.add(key)
            entry = _entry_for(root, category, relative)
            entries.append(entry)
            if entry.diagnostic:
                diagnostics.append(f"{entry.relative_path}: {entry.diagnostic}")

    external_entries = _external_cache_entries()
    entries.extend(external_entries)
    diagnostics.extend(entry.diagnostic or "" for entry in external_entries)

    return LegacyInventory(
        schema_version=INVENTORY_SCHEMA_VERSION,
        legacy_root=str(root),
        root_status=root_observation.status,
        root_node_type=root_observation.node_type,
        root_exists=root_observation.exists,
        root_safe=root_observation.safe,
        entries=tuple(entries),
        diagnostics=tuple(sorted(set(diagnostics))),
    )


def inventory_legacy_root(legacy_root: str | os.PathLike[str]) -> LegacyInventory:
    """Descriptive alias for :func:`build_legacy_inventory`."""

    return build_legacy_inventory(legacy_root)


def build_migration_plan(
    inventory: LegacyInventory,
    destination: str | os.PathLike[str] | None = None,
    *,
    options: SettingsMigrationOptions | None = None,
    capabilities: RuntimeCapabilities | None = None,
) -> LegacyMigrationPlan | LegacySettingsMigrationPlan:
    """Build an immutable inventory plan or a settings dry-run plan.

    Passing ``destination`` selects the user-settings migration slice while
    preserving the original one-argument inventory-only API.
    """

    if not isinstance(inventory, LegacyInventory):
        raise LegacyInventoryError("migration plan requires a LegacyInventory")
    if destination is not None:
        from .legacy_settings_migration import build_settings_migration_plan

        return build_settings_migration_plan(
            inventory,
            destination,
            options=options or SettingsMigrationOptions(),
            capabilities=capabilities,
        )
    actions = tuple(
        InventoryAction(
            category=entry.category,
            relative_path=entry.relative_path,
            path=entry.path,
            candidate_id=entry.candidate_id,
            scope=entry.scope,
            discovery=entry.discovery,
        )
        for entry in inventory.entries
    )
    return LegacyMigrationPlan(
        schema_version=inventory.schema_version,
        legacy_root=inventory.legacy_root,
        entries=inventory.entries,
        actions=actions,
        diagnostics=inventory.diagnostics,
    )


def build_legacy_migration_plan(
    legacy_root: str | os.PathLike[str],
    destination: str | os.PathLike[str] | None = None,
    *,
    options: SettingsMigrationOptions | None = None,
    capabilities: RuntimeCapabilities | None = None,
) -> LegacyMigrationPlan | LegacySettingsMigrationPlan:
    """Inspect a root and return an inventory-only migration plan."""

    return build_migration_plan(
        build_legacy_inventory(legacy_root),
        destination,
        options=options,
        capabilities=capabilities,
    )


_SETTING_CATEGORIES = frozenset(
    {
        SETTING_RUNTIME_CONFIG,
        SETTING_SPEAKER_COLORS,
        SETTING_DICTIONARY,
        SETTING_PRESET,
        SETTING_USER_SETTINGS,
    }
)


def _json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _path_is_within(root: Path, candidate: Path) -> bool:
    try:
        root_path = os.path.abspath(str(root))
        candidate_path = os.path.abspath(str(candidate))
        return os.path.commonpath((root_path, candidate_path)) == root_path
    except ValueError:
        return False


def _ensure_safe_destination(destination: Path, path: Path) -> None:
    """Reject symlink/reparse parents before an atomic write."""

    if not _path_is_within(destination, path):
        raise MigrationError(f"migration target resolves outside destination: {path}")
    current = path
    while current != destination and current != current.parent:
        if current.exists() or current.is_symlink():
            metadata, error = _safe_lstat(current)
            if error:
                raise MigrationError(f"unable to inspect migration target: {current}")
            if metadata is not None and _is_link_like(current, metadata):
                raise MigrationError(f"migration target contains a symbolic link or junction: {current}")
        current = current.parent
    if destination.exists() or destination.is_symlink():
        metadata, error = _safe_lstat(destination)
        if error or (metadata is not None and _is_link_like(destination, metadata)):
            raise MigrationError(f"migration destination must not be a symbolic link: {destination}")
        if metadata is not None and not stat.S_ISDIR(metadata.st_mode):
            raise MigrationError(f"migration destination must be a directory: {destination}")
