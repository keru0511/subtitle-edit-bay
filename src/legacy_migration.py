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
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Mapping, Sequence

from . import installer_migration as _installer_migration
from .channel_presets import ChannelPreset, ChannelPresetError, PRESET_SCHEMA_VERSION
from .transcription_dictionary import (
    TranscriptionDictionaryError,
    load_transcription_dictionary,
)


# The installer migration slice owns the schema/capability validators and the
# transaction primitives.  This module only adapts them to the read-only
# inventory produced here; it must not create a second validator contract.
MigrationError = _installer_migration.MigrationError
RuntimeCapabilities = _installer_migration.RuntimeCapabilities


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

SETTINGS_MIGRATION_SCHEMA_VERSION = 1
SETTING_RUNTIME_CONFIG = "runtime_config"
SETTING_SPEAKER_COLORS = "speaker_colors"
SETTING_DICTIONARY = "dictionary"
SETTING_PRESET = "preset"
SETTING_USER_SETTINGS = "user_settings"

MIGRATION_ITEM_READY = "ready"
MIGRATION_ITEM_SKIPPED = "skipped"
MIGRATION_ITEM_INVALID = "invalid"
MIGRATION_OPERATION_COPY = "copy"
MIGRATION_OPERATION_SKIP = "skip"

MIGRATION_REASON_DESTINATION_EXISTS = "destination_exists"
MIGRATION_REASON_INVALID = "invalid"
MIGRATION_REASON_NOT_SELECTED = "not_selected"
MIGRATION_REASON_UNSAFE_SOURCE = "unsafe_source"
MIGRATION_REASON_UNSUPPORTED = "unsupported"
MIGRATION_REASON_EXTERNAL_REFERENCE = "external_reference"
MIGRATION_REASON_CONFIRMATION_REQUIRED = "confirmation_required"
MIGRATION_REASON_SOURCE_MISSING = "source_missing"

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
    (CATEGORY_USER_SETTINGS, "dictionaries"),
    (CATEGORY_USER_SETTINGS, "presets"),
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

    def to_settings_plan(
        self,
        destination: str | os.PathLike[str],
        *,
        options: SettingsMigrationOptions | None = None,
        capabilities: RuntimeCapabilities | None = None,
    ) -> LegacySettingsMigrationPlan:
        return build_settings_migration_plan(
            self,
            destination,
            options=options or SettingsMigrationOptions(),
            capabilities=capabilities,
        )


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
class SettingsMigrationOptions:
    """Selection and conflict policy for the user-settings migration slice.

    The inventory plan remains read-only.  ``overwrite`` only affects existing
    destination files and never permits writes outside the selected setting
    roots.  ``selected`` may contain candidate IDs, relative paths, or setting
    categories; an empty tuple selects every supported setting candidate.
    """

    runtime_config: bool = True
    speaker_colors: bool = True
    dictionaries: bool = True
    presets: bool = True
    user_settings: bool = True
    overwrite: bool = False
    confirm: bool = False
    # Capability probes are optional at this boundary.  Unknown capabilities
    # must fail closed so a legacy GPU setting is never carried into a runtime
    # that has not been verified to support it.
    capabilities: RuntimeCapabilities = RuntimeCapabilities(cuda=False, nvenc=False)
    selected: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SettingsMigrationItem:
    """A secret-free dry-run decision for one user-setting candidate."""

    candidate_id: str
    category: str
    relative_path: str
    source_path: str
    destination_path: str
    status: str
    operation: str
    reason: str | None = None
    diagnostic: str | None = None
    diff: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "category": self.category,
            "relative_path": self.relative_path,
            "source_path": self.source_path,
            "destination_path": self.destination_path,
            "status": self.status,
            "operation": self.operation,
            "reason": self.reason,
            "diagnostic": self.diagnostic,
            "diff": list(self.diff),
        }


@dataclass(frozen=True, slots=True)
class LegacySettingsMigrationPlan:
    """Immutable, dry-run plan for validated user-setting migration."""

    schema_version: int
    source: str
    destination: str
    inventory_schema_version: int
    items: tuple[SettingsMigrationItem, ...]
    diagnostics: tuple[str, ...] = ()
    overwrite: bool = False
    confirm: bool = False
    # Keep plans created directly (without the builder) conservative too.
    capabilities: RuntimeCapabilities = RuntimeCapabilities(cuda=False, nvenc=False)

    @property
    def ready_items(self) -> tuple[SettingsMigrationItem, ...]:
        return tuple(item for item in self.items if item.status == MIGRATION_ITEM_READY)

    @property
    def skipped_items(self) -> tuple[SettingsMigrationItem, ...]:
        return tuple(item for item in self.items if item.status != MIGRATION_ITEM_READY)

    @property
    def actions(self) -> tuple[SettingsMigrationItem, ...]:
        """Compatibility alias for callers that call plan entries actions."""

        return self.items

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "destination": self.destination,
            "inventory_schema_version": self.inventory_schema_version,
            "items": [item.to_dict() for item in self.items],
            "actions": [item.to_dict() for item in self.items],
            "diagnostics": list(self.diagnostics),
            "overwrite": self.overwrite,
            "confirm": self.confirm,
            "capabilities": {"cuda": self.capabilities.cuda, "nvenc": self.capabilities.nvenc},
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True) + "\n"

    def apply(self, *, confirm: bool | None = None, now: datetime | None = None) -> LegacySettingsMigrationResult:
        return apply_settings_migration(self, confirm=confirm, now=now)


@dataclass(frozen=True, slots=True)
class SettingsMigrationSkip:
    candidate_id: str
    category: str
    destination_path: str
    reason: str
    diagnostic: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "category": self.category,
            "destination_path": self.destination_path,
            "reason": self.reason,
            "diagnostic": self.diagnostic,
        }


@dataclass(frozen=True, slots=True)
class LegacySettingsMigrationResult:
    """Secret-free result of an atomic settings migration transaction."""

    schema_version: int
    source: str
    destination: str
    applied: tuple[str, ...]
    skipped: tuple[SettingsMigrationSkip, ...]
    adjusted: tuple[str, ...]
    diagnostics: tuple[str, ...]
    completed_at: str

    @property
    def copied(self) -> tuple[str, ...]:
        """Compatibility alias for migration result consumers."""

        return self.applied

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "destination": self.destination,
            "applied": list(self.applied),
            "copied": list(self.applied),
            "skipped": [item.to_dict() for item in self.skipped],
            "adjusted": list(self.adjusted),
            "diagnostics": list(self.diagnostics),
            "completed_at": self.completed_at,
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


def _setting_kind(entry: InventoryEntry) -> str | None:
    if entry.scope != SCOPE_LEGACY_ROOT:
        return None
    relative = entry.relative_path.casefold()
    if entry.category == CATEGORY_RUNTIME_CONFIG and relative == ".gui/runtime_config.json":
        return SETTING_RUNTIME_CONFIG
    if entry.category == CATEGORY_SPEAKER_COLORS:
        return SETTING_SPEAKER_COLORS
    if entry.category != CATEGORY_USER_SETTINGS:
        return None
    if "dictionary" in relative or "dictionar" in relative:
        return SETTING_DICTIONARY
    if "preset" in relative:
        return SETTING_PRESET
    if relative == ".gui/settings.json":
        return SETTING_USER_SETTINGS
    return None


def _setting_selected(entry: InventoryEntry, kind: str, options: SettingsMigrationOptions) -> bool:
    enabled = {
        SETTING_RUNTIME_CONFIG: options.runtime_config,
        SETTING_SPEAKER_COLORS: options.speaker_colors,
        SETTING_DICTIONARY: options.dictionaries,
        SETTING_PRESET: options.presets,
        SETTING_USER_SETTINGS: options.user_settings,
    }[kind]
    if not enabled:
        return False
    if not options.selected:
        return True
    selected = {str(value).casefold() for value in options.selected}
    candidates = {
        str(value).casefold()
        for value in (entry.candidate_id or "", entry.relative_path, kind, entry.category)
    }
    return bool(candidates & selected)


def _json_key_paths(value: object, prefix: str = "") -> tuple[str, ...]:
    paths: list[str] = []
    if isinstance(value, Mapping):
        for key in sorted(value, key=str):
            child = f"{prefix}.{key}" if prefix else str(key)
            paths.append(child)
            paths.extend(_json_key_paths(value[key], child))
    elif isinstance(value, list):
        for index, child_value in enumerate(value):
            child = f"{prefix}[{index}]"
            paths.append(child)
            paths.extend(_json_key_paths(child_value, child))
    return tuple(paths)


def _setting_files(path: Path) -> tuple[Path, ...]:
    """Enumerate JSON user-setting files without following links."""

    if path.is_file() and not path.is_symlink():
        return (path,)
    if not path.is_dir() or path.is_symlink():
        raise MigrationError(f"user setting source must be a regular file or directory: {path}")
    files: list[Path] = []
    pending = [path]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as children:
            for child in children:
                child_path = Path(child.path)
                metadata, error = _safe_lstat(child_path)
                if error or metadata is None:
                    raise MigrationError(f"unable to inspect user setting source: {child_path}")
                if _is_link_like(child_path, metadata):
                    raise MigrationError(f"user setting source contains a symbolic link or junction: {child_path}")
                if stat.S_ISDIR(metadata.st_mode):
                    pending.append(child_path)
                elif stat.S_ISREG(metadata.st_mode) and child_path.suffix.casefold() == ".json":
                    files.append(child_path)
                else:
                    raise MigrationError(f"unsupported user setting file: {child_path}")
    if not files:
        raise MigrationError(f"user setting directory contains no JSON files: {path}")
    return tuple(sorted(files))


def _validate_preset_payload(payload: object, path: Path) -> object:
    if not isinstance(payload, Mapping):
        raise MigrationError(f"preset root must be an object: {path}")
    if "presets" in payload:
        if int(payload.get("schema_version", 0)) != PRESET_SCHEMA_VERSION:
            raise MigrationError(f"unsupported preset schema: {path}")
        presets = payload.get("presets")
        if not isinstance(presets, list):
            raise MigrationError(f"preset list must be an array: {path}")
        for item in presets:
            if not isinstance(item, Mapping):
                raise MigrationError(f"preset entry must be an object: {path}")
            try:
                ChannelPreset.from_json(item)
            except (ChannelPresetError, TypeError, ValueError) as exc:
                raise MigrationError(f"invalid preset entry: {path}") from exc
        return payload
    try:
        ChannelPreset.from_json(payload)
    except (ChannelPresetError, TypeError, ValueError) as exc:
        raise MigrationError(f"invalid preset: {path}") from exc
    return payload


_SECRET_SETTING_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "password",
    "passwd",
    "private_key",
    "secret",
    "token",
)
_PATH_SETTING_KEY_PARTS = ("path", "file", "directory", "dir", "root")


def _is_absolute_setting_value(value: str) -> bool:
    return Path(value).is_absolute() or PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _has_parent_reference(value: str) -> bool:
    return ".." in Path(value).parts or ".." in PurePosixPath(value).parts or ".." in PureWindowsPath(value).parts


def _assert_safe_user_setting_payload(value: object) -> None:
    """Reject secrets and file references that the settings slice cannot own."""

    def visit(current: object) -> None:
        if isinstance(current, Mapping):
            for key, child in current.items():
                normalized = str(key).casefold().replace("-", "_")
                if any(part in normalized for part in _SECRET_SETTING_KEY_PARTS):
                    raise MigrationError("user setting contains a secret-like key and cannot be migrated")
                if isinstance(child, str) and any(part in normalized for part in _PATH_SETTING_KEY_PARTS):
                    if _is_absolute_setting_value(child) or _has_parent_reference(child):
                        raise MigrationError("user setting contains an external or unsafe path reference")
                visit(child)
        elif isinstance(current, list):
            for child in current:
                visit(child)

    visit(value)


def _safe_setting_file(source_root: Path, path: Path) -> None:
    """Ensure a source file is below .gui/assets and has no link component."""

    try:
        relative = path.relative_to(source_root)
    except ValueError as exc:
        raise MigrationError(f"user setting source escapes legacy root: {path}") from exc
    if not relative.parts or relative.parts[0].casefold() not in {".gui", "assets", "dictionaries", "presets"}:
        raise MigrationError(f"user setting source is outside the allowed roots: {path}")
    current = source_root
    metadata: os.stat_result | None = None
    for component in relative.parts:
        if component in {"", ".", ".."}:
            raise MigrationError(f"user setting source contains an unsafe relative path: {path}")
        current = current / component
        metadata, error = _safe_lstat(current)
        if error:
            raise MigrationError(f"unable to inspect user setting source: {current}")
        if metadata is None:
            raise MigrationError(f"user setting source is missing: {path}")
        if _is_link_like(current, metadata):
            raise MigrationError(f"user setting source contains a symbolic link or junction: {current}")
    if metadata is None or not stat.S_ISREG(metadata.st_mode):
        raise MigrationError(f"user setting source must be a regular file: {path}")


def _allowed_setting_relative(source_root: Path, path: Path) -> Path | None:
    try:
        relative = path.relative_to(source_root)
    except ValueError:
        return None
    if not relative.parts or relative.parts[0].casefold() not in {".gui", "assets", "dictionaries", "presets"}:
        return None
    return relative


def _prepare_runtime_payload(
    source_path: Path,
    source_root: Path,
    destination_root: Path,
    capabilities: RuntimeCapabilities,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    payload, adjusted = _installer_migration.validated_runtime_config(source_path, capabilities)
    changes = list(adjusted)
    context = (
        payload.get("craig_pipeline", {}).get("transcription_context")
        if isinstance(payload.get("craig_pipeline"), Mapping)
        else None
    )
    if isinstance(context, Mapping):
        dictionary_value = context.get("dictionary_path")
        if isinstance(dictionary_value, str) and dictionary_value:
            dictionary_path = Path(dictionary_value)
            if dictionary_path.is_absolute() and not _path_is_within(source_root, dictionary_path):
                raise MigrationError("external dictionary reference cannot be migrated")
            if dictionary_path.is_absolute():
                relative = _allowed_setting_relative(source_root, dictionary_path)
                if relative is None:
                    raise MigrationError("dictionary reference is outside the allowed setting roots")
                _safe_setting_file(source_root, dictionary_path)
                destination_path = destination_root / relative
                context["dictionary_path"] = str(destination_path)
                changes.append("craig_pipeline.transcription_context.dictionary_path: remapped")
    return payload, tuple(changes)


def _prepare_setting_file(
    kind: str,
    source_path: Path,
    source_root: Path,
    destination_root: Path,
    capabilities: RuntimeCapabilities,
) -> tuple[bytes, tuple[str, ...], tuple[str, ...]]:
    try:
        _safe_setting_file(source_root, source_path)
        if kind == SETTING_RUNTIME_CONFIG:
            payload, adjusted = _prepare_runtime_payload(source_path, source_root, destination_root, capabilities)
            return _json_bytes(payload), tuple(adjusted), _json_key_paths(payload)
        if kind == SETTING_SPEAKER_COLORS:
            raw_payload = _installer_migration._load_object(source_path)
            _assert_safe_user_setting_payload(raw_payload)
            payload = _installer_migration.validated_speaker_colors(source_path)
            return _json_bytes(payload), (), _json_key_paths(payload)
        payload = json.loads(source_path.read_text(encoding="utf-8-sig"))
        _assert_safe_user_setting_payload(payload)
        if kind == SETTING_DICTIONARY:
            normalized = load_transcription_dictionary(source_path).to_json()
            return _json_bytes(normalized), ("dictionary.normalized",), _json_key_paths(normalized)
        elif kind == SETTING_PRESET:
            _validate_preset_payload(payload, source_path)
        elif kind == SETTING_USER_SETTINGS:
            if not isinstance(payload, Mapping):
                raise MigrationError(f"user settings root must be an object: {source_path}")
        return source_path.read_bytes(), (), _json_key_paths(payload)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TranscriptionDictionaryError,
        MigrationError,
        ValueError,
    ) as exc:
        if isinstance(exc, MigrationError):
            raise
        raise MigrationError(f"invalid user setting: {source_path}") from exc


def _destination_conflict(
    path: Path,
    overwrite: bool,
    confirm: bool,
    *,
    source_is_directory: bool = False,
) -> tuple[str, str | None] | None:
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink():
        return MIGRATION_ITEM_INVALID, MIGRATION_REASON_UNSAFE_SOURCE
    if source_is_directory and path.is_dir():
        if not overwrite:
            return MIGRATION_ITEM_SKIPPED, MIGRATION_REASON_DESTINATION_EXISTS
        if not confirm:
            return MIGRATION_ITEM_SKIPPED, MIGRATION_REASON_CONFIRMATION_REQUIRED
        return None
    if not path.is_file():
        return MIGRATION_ITEM_INVALID, MIGRATION_REASON_UNSAFE_SOURCE
    if not overwrite:
        return MIGRATION_ITEM_SKIPPED, MIGRATION_REASON_DESTINATION_EXISTS
    if not confirm:
        return MIGRATION_ITEM_SKIPPED, MIGRATION_REASON_CONFIRMATION_REQUIRED
    return None


def build_settings_migration_plan(
    inventory: LegacyInventory,
    destination: str | os.PathLike[str],
    *,
    options: SettingsMigrationOptions = SettingsMigrationOptions(),
    capabilities: RuntimeCapabilities | None = None,
) -> LegacySettingsMigrationPlan:
    """Create a validated dry-run plan for user settings only.

    ``capabilities`` is an optional trusted probe result.  When it is omitted,
    the options' conservative ``cuda=False, nvenc=False`` default is used so
    GPU-dependent settings are adjusted to CPU/libx264 rather than assumed to
    be supported.
    """

    if not isinstance(inventory, LegacyInventory):
        raise LegacyInventoryError("settings migration requires a LegacyInventory")
    source_root = _lexical_absolute(inventory.legacy_root)
    destination_root = _lexical_absolute(destination)
    if source_root == destination_root:
        raise MigrationError("migration source and destination must be different")
    if _path_is_within(source_root, destination_root):
        raise MigrationError("migration destination must not be inside the legacy root")
    if capabilities is not None:
        options = SettingsMigrationOptions(
            runtime_config=options.runtime_config,
            speaker_colors=options.speaker_colors,
            dictionaries=options.dictionaries,
            presets=options.presets,
            user_settings=options.user_settings,
            overwrite=options.overwrite,
            confirm=options.confirm,
            capabilities=capabilities,
            selected=options.selected,
        )
    if options.overwrite and not options.confirm:
        # Keep the dry-run useful: items are reported as confirmation-required;
        # apply will fail closed if the caller still omits confirmation.
        pass
    items: list[SettingsMigrationItem] = []
    diagnostics: list[str] = []
    for entry in inventory.entries:
        kind = _setting_kind(entry)
        if kind is None:
            continue
        source_path = source_root / entry.relative_path
        destination_path = destination_root / entry.relative_path
        reason: str | None = None
        status = MIGRATION_ITEM_READY
        operation = MIGRATION_OPERATION_COPY
        diff: tuple[str, ...] = ()
        diagnostic: str | None = None
        if not _setting_selected(entry, kind, options):
            status, operation, reason = MIGRATION_ITEM_SKIPPED, MIGRATION_OPERATION_SKIP, MIGRATION_REASON_NOT_SELECTED
        elif entry.status != STATUS_PRESENT or not entry.safe:
            status, operation = MIGRATION_ITEM_SKIPPED, MIGRATION_OPERATION_SKIP
            reason = (
                MIGRATION_REASON_SOURCE_MISSING
                if entry.status == STATUS_MISSING
                else MIGRATION_REASON_UNSAFE_SOURCE
            )
            diagnostic = entry.diagnostic or f"source candidate is {entry.status}"
        else:
            try:
                if _allowed_setting_relative(source_root, source_path) is None:
                    raise MigrationError("user setting source is outside the allowed roots")
                _ensure_safe_destination(destination_root, destination_path)
                conflict = _destination_conflict(
                    destination_path,
                    options.overwrite,
                    options.confirm,
                    source_is_directory=source_path.is_dir(),
                )
                if conflict:
                    status, reason = conflict
                    operation = MIGRATION_OPERATION_SKIP
                else:
                    files = _setting_files(source_path)
                    for source_file in files:
                        _, _, file_diff = _prepare_setting_file(
                            kind, source_file, source_root, destination_root, options.capabilities
                        )
                        relative_file = source_file.relative_to(source_path).as_posix()
                        prefix = "" if relative_file == "." else f"{relative_file}: "
                        diff += tuple(f"{prefix}{path}" for path in file_diff)
            except (MigrationError, OSError, ValueError) as exc:
                reason = (
                    MIGRATION_REASON_EXTERNAL_REFERENCE
                    if "external" in str(exc).casefold()
                    else MIGRATION_REASON_INVALID
                )
                status, operation = MIGRATION_ITEM_INVALID, MIGRATION_OPERATION_SKIP
                diagnostic = str(exc)
        items.append(
            SettingsMigrationItem(
                candidate_id=entry.candidate_id or f"legacy:{entry.relative_path}",
                category=kind,
                relative_path=entry.relative_path,
                source_path=str(source_path),
                destination_path=str(destination_path),
                status=status,
                operation=operation,
                reason=reason,
                diagnostic=diagnostic,
                diff=tuple(sorted(set(diff))),
            )
        )
        if diagnostic:
            diagnostics.append(f"{entry.relative_path}: {diagnostic}")
    return LegacySettingsMigrationPlan(
        schema_version=SETTINGS_MIGRATION_SCHEMA_VERSION,
        source=str(source_root),
        destination=str(destination_root),
        inventory_schema_version=inventory.schema_version,
        items=tuple(items),
        diagnostics=tuple(sorted(set(diagnostics))),
        overwrite=options.overwrite,
        confirm=options.confirm,
        capabilities=options.capabilities,
    )


def _snapshot_targets(paths: Sequence[Path]) -> tuple[dict[Path, bytes | None], tuple[Path, ...]]:
    snapshots: dict[Path, bytes | None] = {}
    parents: set[Path] = set()
    for path in paths:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise MigrationError(f"migration target must be a regular file: {path}")
        snapshots[path] = path.read_bytes() if path.exists() else None
        parent = path.parent
        while not parent.exists():
            parents.add(parent)
            parent = parent.parent
    return snapshots, tuple(sorted(parents, key=lambda item: len(item.parts), reverse=True))


def _write_bytes_atomic(path: Path, payload: bytes, *, temporary_suffix: str = ".tmp") -> None:
    """Replace one file atomically and remove its temporary file on every path."""

    temporary = path.with_name(f".{path.name}.{os.getpid()}{temporary_suffix}")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_targets(snapshots: Mapping[Path, bytes | None], parents: Sequence[Path]) -> None:
    failures: list[str] = []
    for path, original in reversed(tuple(snapshots.items())):
        try:
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                _write_bytes_atomic(path, original, temporary_suffix=".restore.tmp")
        except OSError as exc:
            failures.append(f"{path}: {exc}")
    for parent in parents:
        try:
            parent.rmdir()
        except OSError:
            pass
    if failures:
        raise MigrationError("migration rollback failed: " + "; ".join(failures))


def apply_settings_migration(
    plan: LegacySettingsMigrationPlan,
    *,
    confirm: bool | None = None,
    now: datetime | None = None,
) -> LegacySettingsMigrationResult:
    """Apply only ready user-setting entries as one atomic transaction."""

    if not isinstance(plan, LegacySettingsMigrationPlan):
        raise LegacyInventoryError("settings migration apply requires a LegacySettingsMigrationPlan")
    confirmed = plan.confirm if confirm is None else confirm
    if plan.overwrite and not confirmed:
        raise MigrationError("explicit confirmation is required for overwrite migration")
    source_root = _lexical_absolute(plan.source)
    destination_root = _lexical_absolute(plan.destination)
    if source_root == destination_root:
        raise MigrationError("migration source and destination must be different")
    if _path_is_within(source_root, destination_root):
        raise MigrationError("migration destination must not be inside the legacy root")
    writes: list[tuple[Path, bytes]] = []
    adjusted: list[str] = []
    dynamic_skips: list[SettingsMigrationSkip] = []
    for item in plan.ready_items:
        source_path = Path(item.source_path)
        destination_path = Path(item.destination_path)
        if not _path_is_within(source_root, source_path):
            raise MigrationError(f"migration source escapes selected legacy root: {source_path}")
        if not _path_is_within(destination_root, destination_path):
            raise MigrationError(f"migration target escapes destination: {destination_path}")
        if _allowed_setting_relative(source_root, source_path) is None:
            raise MigrationError(f"migration source is outside allowed setting roots: {source_path}")
        _ensure_safe_destination(destination_root, destination_path)
        files = _setting_files(source_path)
        for source_file in files:
            relative = source_file.relative_to(source_path) if source_path.is_dir() else Path(source_file.name)
            target = destination_path / relative if source_path.is_dir() else destination_path
            _ensure_safe_destination(destination_root, target)
            if target.exists() and not target.is_file():
                raise MigrationError(f"migration target must be a regular file: {target}")
            payload, changes, _diff = _prepare_setting_file(
                item.category, source_file, source_root, destination_root, plan.capabilities
            )
            # Runtime fallback decisions have already been captured by the plan
            # and are intentionally revalidated before writes.  Preserve the
            # normalized payload while avoiding secret content in the result.
            adjusted.extend(changes)
            if target.exists() and not plan.overwrite:
                dynamic_skips.append(
                    SettingsMigrationSkip(
                        candidate_id=(
                            f"{item.candidate_id}:{relative.as_posix()}"
                            if source_path.is_dir()
                            else item.candidate_id
                        ),
                        category=item.category,
                        destination_path=str(target),
                        reason=MIGRATION_REASON_DESTINATION_EXISTS,
                    )
                )
                continue
            writes.append((target, payload))
    targets = [target for target, _payload in writes]
    snapshots, parents = _snapshot_targets(targets)
    try:
        for target, payload in writes:
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_bytes_atomic(target, payload)
    except Exception as exc:
        try:
            _restore_targets(snapshots, parents)
        except MigrationError as rollback_exc:
            raise MigrationError(f"migration failed and rollback was incomplete: {rollback_exc}") from exc
        raise MigrationError(f"migration changes were rolled back: {exc}") from exc
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    skipped = tuple(
        SettingsMigrationSkip(
            candidate_id=item.candidate_id,
            category=item.category,
            destination_path=item.destination_path,
            reason=item.reason or MIGRATION_REASON_INVALID,
            diagnostic=item.diagnostic,
        )
        for item in plan.skipped_items
    ) + tuple(dynamic_skips)
    applied = tuple(sorted({str(target) for target, _payload in writes}))
    return LegacySettingsMigrationResult(
        schema_version=SETTINGS_MIGRATION_SCHEMA_VERSION,
        source=str(source_root),
        destination=str(destination_root),
        applied=applied,
        skipped=skipped,
        adjusted=tuple(sorted(set(adjusted))),
        diagnostics=plan.diagnostics,
        completed_at=timestamp,
    )


def build_legacy_settings_migration_plan(
    legacy_root: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    options: SettingsMigrationOptions = SettingsMigrationOptions(),
    capabilities: RuntimeCapabilities | None = None,
) -> LegacySettingsMigrationPlan:
    return build_settings_migration_plan(
        build_legacy_inventory(legacy_root), destination, options=options, capabilities=capabilities
    )


def apply_legacy_settings_migration(
    plan: LegacySettingsMigrationPlan,
    *,
    confirm: bool | None = None,
    now: datetime | None = None,
) -> LegacySettingsMigrationResult:
    return apply_settings_migration(plan, confirm=confirm, now=now)


def apply_migration_plan(
    plan: LegacySettingsMigrationPlan,
    *,
    confirm: bool | None = None,
    now: datetime | None = None,
) -> LegacySettingsMigrationResult:
    """Apply a settings migration plan through the atomic boundary."""

    return apply_settings_migration(plan, confirm=confirm, now=now)


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
    "LegacySettingsMigrationPlan",
    "LegacySettingsMigrationResult",
    "SettingsMigrationSkip",
    "MigrationError",
    "MIGRATION_ITEM_INVALID",
    "MIGRATION_ITEM_READY",
    "MIGRATION_ITEM_SKIPPED",
    "MIGRATION_OPERATION_COPY",
    "MIGRATION_OPERATION_SKIP",
    "MIGRATION_REASON_CONFIRMATION_REQUIRED",
    "MIGRATION_REASON_DESTINATION_EXISTS",
    "MIGRATION_REASON_EXTERNAL_REFERENCE",
    "MIGRATION_REASON_INVALID",
    "MIGRATION_REASON_NOT_SELECTED",
    "MIGRATION_REASON_UNSAFE_SOURCE",
    "MIGRATION_REASON_UNSUPPORTED",
    "MIGRATION_REASON_SOURCE_MISSING",
    "RuntimeCapabilities",
    "SETTINGS_MIGRATION_SCHEMA_VERSION",
    "SETTING_DICTIONARY",
    "SETTING_PRESET",
    "SETTING_RUNTIME_CONFIG",
    "SETTING_SPEAKER_COLORS",
    "SETTING_USER_SETTINGS",
    "SettingsMigrationItem",
    "SettingsMigrationOptions",
    "SCOPE_EXTERNAL",
    "SCOPE_LEGACY_ROOT",
    "STATUS_NOT_DISCOVERABLE",
    "STATUS_MISSING",
    "STATUS_PRESENT",
    "STATUS_UNREADABLE",
    "STATUS_UNSAFE",
    "build_legacy_inventory",
    "build_legacy_migration_plan",
    "build_legacy_settings_migration_plan",
    "build_migration_plan",
    "build_settings_migration_plan",
    "apply_legacy_settings_migration",
    "apply_migration_plan",
    "apply_settings_migration",
    "inventory_legacy_root",
]
