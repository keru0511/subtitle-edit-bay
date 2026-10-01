"""旧環境の移行計画で共有する定数と不変データ型。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Sequence

from . import installer_migration as _installer_migration
from .channel_presets import (
    ChannelPreset as ChannelPreset,
    ChannelPresetError as ChannelPresetError,
    PRESET_SCHEMA_VERSION as PRESET_SCHEMA_VERSION,
)
from .transcription_dictionary import (
    TranscriptionDictionaryError as TranscriptionDictionaryError,
    load_transcription_dictionary as load_transcription_dictionary,
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

# Cache/cleanup is intentionally a policy layer over the inventory and the
# settings migration transaction above.  It never makes a cache reusable just
# because a path exists; callers need to provide an explicit verification
# decision for content-addressed package caches.
CACHE_CLEANUP_SCHEMA_VERSION = 1
CACHE_KIND_PIP_DOWNLOAD = "pip_download"
CACHE_KIND_PYTORCH_WHEEL = "pytorch_wheel"
CACHE_KIND_HUGGINGFACE_WHISPERX_MODEL = "huggingface_whisperx_model"
# Short aliases keep integrations readable without changing the canonical
# serialized values above.
CACHE_KIND_PIP = CACHE_KIND_PIP_DOWNLOAD
CACHE_KIND_PYTORCH = CACHE_KIND_PYTORCH_WHEEL
CACHE_KIND_HUGGINGFACE_MODEL = CACHE_KIND_HUGGINGFACE_WHISPERX_MODEL
CACHE_KIND_AUDIO_PREVIEW = "audio_preview"
CACHE_KIND_TRANSCRIPT_METADATA = "transcript_metadata"
CACHE_KIND_RUNTIME_DEPENDENT = "runtime_dependent_cache"
CACHE_KIND_LEGACY_VENV = "legacy_venv"
CACHE_KIND_LEGACY_APP_SOURCE = "legacy_app_source"
CACHE_KIND_PROJECT = "project"
CACHE_KIND_MEDIA = "media"
CACHE_KIND_OUTPUT = "output"

CACHE_STATE_REUSE = "reuse"
CACHE_STATE_REBUILD = "rebuild"
CACHE_STATE_PRESERVE = "preserve"
CACHE_STATE_REMOVABLE_AFTER_SUCCESS = "removable_after_success"
CLEANUP_STATE_REUSE = CACHE_STATE_REUSE
CLEANUP_STATE_REBUILD = CACHE_STATE_REBUILD
CLEANUP_STATE_PRESERVE = CACHE_STATE_PRESERVE
CLEANUP_STATE_REMOVABLE_AFTER_SUCCESS = CACHE_STATE_REMOVABLE_AFTER_SUCCESS

CACHE_REASON_NOT_DISCOVERABLE = "path_not_discoverable"
CACHE_REASON_REUSE_UNVERIFIED = "cache_fingerprint_unverified"
CACHE_REASON_PROJECT_PRESERVED = "project_data_must_be_preserved"
CACHE_REASON_MEDIA_PRESERVED = "media_data_must_be_preserved"
CACHE_REASON_OUTPUT_PRESERVED = "output_data_must_be_preserved"
CACHE_REASON_MIGRATION_INCOMPLETE = "migration_incomplete"
CACHE_REASON_LEGACY_ROOT_PRESERVED = "legacy_root_contains_user_data"
CACHE_REASON_REFERENCED_DATA = "referenced_data_must_be_preserved"
CACHE_REASON_UNSAFE_PATH = "unsafe_path"
CACHE_REASON_SOURCE_MISSING = "source_missing"
CACHE_REASON_NOT_SELECTED = "not_selected"
CACHE_REASON_NOT_CLEANUP_TARGET = "not_cleanup_target"

# These are intentionally explicit.  Inventory does not perform a broad
# filesystem search, and the paths below are only candidates; no migration is
# implied by their presence.
_CANDIDATE_PATHS: tuple[tuple[str, str], ...] = (
    (CATEGORY_LEGACY_RUNTIME, ".venv"),
    (CATEGORY_LEGACY_RUNTIME, "src"),
    (CATEGORY_LEGACY_RUNTIME, "app"),
    (CATEGORY_LEGACY_RUNTIME, "source"),
    (CATEGORY_LEGACY_RUNTIME, "setup.bat"),
    (CATEGORY_LEGACY_RUNTIME, "start.bat"),
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
    (CATEGORY_CACHE, ".cache/pip"),
    (CATEGORY_CACHE, ".cache/torch"),
    (CATEGORY_CACHE, ".cache/huggingface"),
    (CATEGORY_CACHE, ".cache/whisperx"),
    (CATEGORY_CACHE, ".cache/audio-preview"),
    (CATEGORY_CACHE, ".cache/audio_preview"),
    (CATEGORY_CACHE, ".cache/transcripts"),
    (CATEGORY_CACHE, ".cache/transcript"),
    (CATEGORY_CACHE, ".local/cache"),
    (CATEGORY_CACHE, ".local/pip"),
    (CATEGORY_CACHE, ".local/models"),
    (CATEGORY_CACHE, ".local/huggingface"),
    (CATEGORY_CACHE, ".local/torch"),
    (CATEGORY_CACHE, ".local/whisperx"),
    (CATEGORY_CACHE, ".local/audio-preview"),
    (CATEGORY_CACHE, ".local/audio_preview"),
    (CATEGORY_CACHE, ".local/transcripts"),
    (CATEGORY_CACHE, ".local/transcript"),
    (CATEGORY_CACHE, "cache/pip"),
    (CATEGORY_CACHE, "cache/torch"),
    (CATEGORY_CACHE, "cache/huggingface"),
    (CATEGORY_CACHE, "cache/whisperx"),
    (CATEGORY_CACHE, "cache/audio-preview"),
    (CATEGORY_CACHE, "cache/audio_preview"),
    (CATEGORY_CACHE, "cache/transcripts"),
    (CATEGORY_CACHE, "cache/transcript"),
    (CATEGORY_CACHE, "pip-cache"),
    (CATEGORY_CACHE, "torch-cache"),
    (CATEGORY_CACHE, "huggingface-cache"),
    (CATEGORY_CACHE, "whisperx-cache"),
    (CATEGORY_CACHE, "audio-preview"),
    (CATEGORY_CACHE, "audio_preview"),
    (CATEGORY_CACHE, "transcripts"),
    (CATEGORY_CACHE, "transcript"),
    (CATEGORY_CACHE, ".gui/transcripts"),
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
        from .legacy_migration_inventory import build_migration_plan

        return build_migration_plan(self)

    def to_settings_plan(
        self,
        destination: str | os.PathLike[str],
        *,
        options: SettingsMigrationOptions | None = None,
        capabilities: RuntimeCapabilities | None = None,
    ) -> LegacySettingsMigrationPlan:
        from .legacy_settings_migration import build_settings_migration_plan

        return build_settings_migration_plan(
            self,
            destination,
            options=options or SettingsMigrationOptions(),
            capabilities=capabilities,
        )

    def to_cache_cleanup_plan(
        self,
        *,
        options: CacheCleanupOptions | None = None,
        migration_completed: bool | None = None,
        cache_paths: Mapping[str, str | os.PathLike[str]] | None = None,
        selected: Sequence[str] | None = None,
        confirm: bool | None = None,
    ) -> CacheCleanupPlan:
        from .legacy_cache_cleanup import build_cache_cleanup_plan

        return build_cache_cleanup_plan(
            self,
            options=options,
            migration_completed=migration_completed,
            cache_paths=cache_paths,
            selected=selected,
            confirm=confirm,
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
        from .legacy_settings_migration import apply_settings_migration

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
class CacheCleanupOptions:
    """Explicit policy inputs for cache inspection and post-migration cleanup.

    ``cache_paths`` is intentionally opt-in.  The inventory cannot discover
    OS-level pip, PyTorch, or Hugging Face locations from a legacy root, so a
    caller must provide an exact path before it can be inspected.  A path is
    still treated as non-reusable unless its candidate ID is included in
    ``reusable_cache_ids`` after an independent lock/fingerprint check.
    """

    migration_completed: bool = False
    confirm: bool = False
    selected: tuple[str, ...] = ()
    cache_paths: Mapping[str, str | os.PathLike[str]] = field(default_factory=dict)
    referenced_data: Mapping[str, Sequence[str]] = field(default_factory=dict)
    reusable_cache_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CacheCleanupEntry:
    """One classified path in a cache/legacy cleanup plan.

    ``state`` describes whether the path can be reused, must be rebuilt, must
    be preserved, or may be removed after a successful migration.  Cleanup is
    separately guarded by ``cleanup_allowed`` so a caller cannot infer a
    deletion permission from a reuse decision.
    """

    candidate_id: str
    kind: str
    path: str
    state: str
    exists: bool
    safe: bool
    size_bytes: int | None
    reclaimable_bytes: int
    cleanup_allowed: bool
    protection_reason: str | None = None
    referenced_data: tuple[str, ...] = ()
    diagnostic: str | None = None
    scope: str = SCOPE_LEGACY_ROOT

    @property
    def disposition(self) -> str:
        """Readable alias for consumers that call the state a disposition."""

        return self.state

    @property
    def status(self) -> str:
        """Compatibility alias with :class:`InventoryEntry`."""

        return self.state

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "kind": self.kind,
            "path": self.path,
            "state": self.state,
            "disposition": self.state,
            "exists": self.exists,
            "safe": self.safe,
            "size_bytes": self.size_bytes,
            "reclaimable_bytes": self.reclaimable_bytes,
            "cleanup_allowed": self.cleanup_allowed,
            "protection_reason": self.protection_reason,
            "referenced_data": list(self.referenced_data),
            "diagnostic": self.diagnostic,
            "scope": self.scope,
        }


@dataclass(frozen=True, slots=True)
class CacheCleanupPlan:
    """Immutable dry-run plan for cache classification and cleanup."""

    schema_version: int
    source: str
    inventory_schema_version: int
    migration_completed: bool
    confirm: bool
    selected: tuple[str, ...]
    entries: tuple[CacheCleanupEntry, ...]
    reclaimable_bytes: int
    diagnostics: tuple[str, ...] = ()

    @property
    def cleanup_entries(self) -> tuple[CacheCleanupEntry, ...]:
        return tuple(entry for entry in self.entries if entry.cleanup_allowed)

    @property
    def actions(self) -> tuple[CacheCleanupEntry, ...]:
        """Compatibility alias for plan consumers that use ``actions``."""

        return self.entries

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "inventory_schema_version": self.inventory_schema_version,
            "migration_completed": self.migration_completed,
            "confirm": self.confirm,
            "selected": list(self.selected),
            "entries": [entry.to_dict() for entry in self.entries],
            "actions": [entry.to_dict() for entry in self.entries],
            "reclaimable_bytes": self.reclaimable_bytes,
            "diagnostics": list(self.diagnostics),
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True) + "\n"

    def apply(
        self,
        *,
        confirm: bool | None = None,
        selected: Sequence[str] | None = None,
        now: datetime | None = None,
    ) -> CacheCleanupResult:
        from .legacy_cache_cleanup import apply_cache_cleanup

        return apply_cache_cleanup(self, confirm=confirm, selected=selected, now=now)


@dataclass(frozen=True, slots=True)
class CacheCleanupSkip:
    candidate_id: str
    path: str
    reason: str
    diagnostic: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "path": self.path,
            "reason": self.reason,
            "diagnostic": self.diagnostic,
        }


@dataclass(frozen=True, slots=True)
class CacheCleanupResult:
    """Secret-free result of an explicitly confirmed post-migration cleanup."""

    schema_version: int
    source: str
    removed: tuple[str, ...]
    skipped: tuple[CacheCleanupSkip, ...]
    reclaimed_bytes: int
    diagnostics: tuple[str, ...]
    completed_at: str

    @property
    def deleted(self) -> tuple[str, ...]:
        """Readable alias for callers that use ``deleted`` for paths."""

        return self.removed

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "removed": list(self.removed),
            "deleted": list(self.removed),
            "skipped": [item.to_dict() for item in self.skipped],
            "reclaimed_bytes": self.reclaimed_bytes,
            "diagnostics": list(self.diagnostics),
            "completed_at": self.completed_at,
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True) + "\n"
