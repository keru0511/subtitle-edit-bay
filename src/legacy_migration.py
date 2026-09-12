"""Read-only inventory primitives for legacy BAT/ZIP workspaces.

This module deliberately does not import or execute anything from a legacy
workspace.  It only uses ``lstat``/directory enumeration to describe known
candidate paths.  Later migration slices can consume the immutable inventory
without having to rediscover the safety boundary.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


INVENTORY_SCHEMA_VERSION = 1
MAX_SIZE_SCAN_ENTRIES = 100_000

STATUS_PRESENT = "present"
STATUS_MISSING = "missing"
STATUS_UNREADABLE = "unreadable"
STATUS_UNSAFE = "unsafe"
STATUS_NOT_DISCOVERABLE = "not_discoverable"

SCOPE_LEGACY_ROOT = "legacy_root"
SCOPE_EXTERNAL = "external_user_cache"
DISCOVERY_ROOT_RELATIVE = "root_relative"
DISCOVERY_NOT_DISCOVERABLE = "not_discoverable_from_legacy_root"

CATEGORY_LEGACY_RUNTIME = "legacy_runtime"
CATEGORY_RUNTIME_CONFIG = "runtime_config"
CATEGORY_SPEAKER_COLORS = "speaker_colors"
CATEGORY_USER_SETTINGS = "user_settings"
CATEGORY_PROJECT = "project"
CATEGORY_MEDIA = "media"
CATEGORY_OUTPUT = "output"
CATEGORY_CACHE = "cache"

# These are intentionally explicit.  Inventory does not perform a broad
# filesystem search, and the paths below are only candidates; no migration is
# implied by their presence.
_CANDIDATE_PATHS: tuple[tuple[str, str], ...] = (
    (CATEGORY_LEGACY_RUNTIME, ".venv"),
    (CATEGORY_RUNTIME_CONFIG, ".gui/runtime_config.json"),
    (CATEGORY_SPEAKER_COLORS, "assets/speaker_colors.json"),
    (CATEGORY_SPEAKER_COLORS, ".gui/speaker_colors.json"),
    (CATEGORY_USER_SETTINGS, ".gui/settings.json"),
    (CATEGORY_USER_SETTINGS, ".gui/dictionary.json"),
    (CATEGORY_USER_SETTINGS, ".gui/dictionaries"),
    (CATEGORY_USER_SETTINGS, ".gui/presets"),
    (CATEGORY_USER_SETTINGS, "assets/dictionary.json"),
    (CATEGORY_USER_SETTINGS, "assets/dictionaries"),
    (CATEGORY_USER_SETTINGS, "assets/preset.json"),
    (CATEGORY_USER_SETTINGS, "assets/presets"),
    (CATEGORY_PROJECT, "project"),
    (CATEGORY_PROJECT, "projects"),
    (CATEGORY_MEDIA, "video_import"),
    (CATEGORY_OUTPUT, "video_export"),
    (CATEGORY_OUTPUT, "out"),
    (CATEGORY_CACHE, "cache"),
    (CATEGORY_CACHE, ".cache"),
    (CATEGORY_CACHE, ".local/cache"),
    (CATEGORY_CACHE, ".local/models"),
    (CATEGORY_CACHE, ".local/huggingface"),
    (CATEGORY_CACHE, ".local/torch"),
    (CATEGORY_CACHE, ".local/whisperx"),
)
_PROJECT_FILE_SUFFIXES = (".seb-project.json", ".subtitle-project.json")

# pip, model, and application caches normally live outside a selected legacy
# root.  Keep these as named capabilities instead of guessing OS-specific
# paths or treating an uninspected cache as missing.  A later migration slice
# may resolve them through an explicit user/environment policy.
_EXTERNAL_CACHE_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("pip-user-cache", "pip user cache"),
    ("pytorch-user-cache", "PyTorch wheel/model cache"),
    ("huggingface-user-cache", "Hugging Face/WhisperX model cache"),
    ("application-user-cache", "application user cache"),
)


class LegacyInventoryError(ValueError):
    """Raised for an invalid inventory value supplied by a caller."""


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    """Metadata for one known legacy candidate path.

    ``size_bytes`` is ``None`` when the path is missing, unsafe, or could not
    be inspected.  Directory sizes are best-effort and must not be treated as
    a migration guarantee.
    """

    category: str
    relative_path: str
    path: str
    status: str
    node_type: str
    exists: bool
    safe: bool
    size_bytes: int | None
    size_is_approximate: bool
    diagnostic: str | None = None
    candidate_id: str | None = None
    scope: str = SCOPE_LEGACY_ROOT
    discovery: str = DISCOVERY_ROOT_RELATIVE

    @property
    def kind(self) -> str:
        """Compatibility alias for callers that use category as a kind."""

        return self.category

    @property
    def is_present(self) -> bool:
        return self.status == STATUS_PRESENT

    def to_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "relative_path": self.relative_path,
            "path": self.path,
            "status": self.status,
            "node_type": self.node_type,
            "exists": self.exists,
            "safe": self.safe,
            "size_bytes": self.size_bytes,
            "size_is_approximate": self.size_is_approximate,
            "diagnostic": self.diagnostic,
            "candidate_id": self.candidate_id,
            "scope": self.scope,
            "discovery": self.discovery,
        }


@dataclass(frozen=True, slots=True)
class LegacyInventory:
    """Immutable read-only description of a user-selected legacy root."""

    schema_version: int
    legacy_root: str
    root_status: str
    root_node_type: str
    root_exists: bool
    root_safe: bool
    entries: tuple[InventoryEntry, ...]
    diagnostics: tuple[str, ...]

    @property
    def candidates(self) -> tuple[InventoryEntry, ...]:
        return self.entries

    @property
    def present_entries(self) -> tuple[InventoryEntry, ...]:
        return tuple(entry for entry in self.entries if entry.exists)

    @property
    def external_cache_entries(self) -> tuple[InventoryEntry, ...]:
        """Named user-cache capabilities that are not discoverable from root."""

        return tuple(entry for entry in self.entries if entry.scope == SCOPE_EXTERNAL)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "legacy_root": self.legacy_root,
            "root": {
                "status": self.root_status,
                "node_type": self.root_node_type,
                "exists": self.root_exists,
                "safe": self.root_safe,
            },
            "entries": [entry.to_dict() for entry in self.entries],
            "diagnostics": list(self.diagnostics),
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True) + "\n"

    def to_plan(self) -> LegacyMigrationPlan:
        return build_migration_plan(self)


@dataclass(frozen=True, slots=True)
class InventoryAction:
    """A deliberately non-mutating action in the first migration slice."""

    category: str
    relative_path: str
    path: str
    operation: str = "inspect_only"
    destructive: bool = False
    candidate_id: str | None = None
    scope: str = SCOPE_LEGACY_ROOT
    discovery: str = DISCOVERY_ROOT_RELATIVE

    def to_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "relative_path": self.relative_path,
            "path": self.path,
            "operation": self.operation,
            "destructive": self.destructive,
            "candidate_id": self.candidate_id,
            "scope": self.scope,
            "discovery": self.discovery,
        }


@dataclass(frozen=True, slots=True)
class LegacyMigrationPlan:
    """Immutable plan that records inventory-only operations.

    The plan intentionally contains no copy/move/delete operation.  A future
    slice may extend this type, but this slice cannot mutate a legacy root.
    """

    schema_version: int
    legacy_root: str
    entries: tuple[InventoryEntry, ...]
    actions: tuple[InventoryAction, ...]
    diagnostics: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "legacy_root": self.legacy_root,
            "entries": [entry.to_dict() for entry in self.entries],
            "actions": [action.to_dict() for action in self.actions],
            "diagnostics": list(self.diagnostics),
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True) + "\n"


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
                    f"{label} is outside the selected legacy root and was not inspected; "
                    "it is not treated as missing"
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


def build_migration_plan(inventory: LegacyInventory) -> LegacyMigrationPlan:
    """Build an immutable, inventory-only plan from an existing inventory."""

    if not isinstance(inventory, LegacyInventory):
        raise LegacyInventoryError("migration plan requires a LegacyInventory")
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


def build_legacy_migration_plan(legacy_root: str | os.PathLike[str]) -> LegacyMigrationPlan:
    """Inspect a root and return an inventory-only migration plan."""

    return build_migration_plan(build_legacy_inventory(legacy_root))


__all__ = [
    "CATEGORY_CACHE",
    "CATEGORY_LEGACY_RUNTIME",
    "CATEGORY_MEDIA",
    "CATEGORY_OUTPUT",
    "CATEGORY_PROJECT",
    "CATEGORY_RUNTIME_CONFIG",
    "CATEGORY_SPEAKER_COLORS",
    "CATEGORY_USER_SETTINGS",
    "DISCOVERY_NOT_DISCOVERABLE",
    "DISCOVERY_ROOT_RELATIVE",
    "INVENTORY_SCHEMA_VERSION",
    "InventoryAction",
    "InventoryEntry",
    "LegacyInventory",
    "LegacyInventoryError",
    "LegacyMigrationPlan",
    "SCOPE_EXTERNAL",
    "SCOPE_LEGACY_ROOT",
    "STATUS_NOT_DISCOVERABLE",
    "STATUS_MISSING",
    "STATUS_PRESENT",
    "STATUS_UNREADABLE",
    "STATUS_UNSAFE",
    "build_legacy_inventory",
    "build_legacy_migration_plan",
    "build_migration_plan",
    "inventory_legacy_root",
]
