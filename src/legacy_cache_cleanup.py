"""旧環境のキャッシュ分類と安全な削除処理を担当する。"""

from __future__ import annotations

import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from .legacy_migration_types import (
    CACHE_CLEANUP_SCHEMA_VERSION,
    CACHE_KIND_AUDIO_PREVIEW,
    CACHE_KIND_HUGGINGFACE_WHISPERX_MODEL,
    CACHE_KIND_LEGACY_APP_SOURCE,
    CACHE_KIND_LEGACY_VENV,
    CACHE_KIND_MEDIA,
    CACHE_KIND_OUTPUT,
    CACHE_KIND_PIP_DOWNLOAD,
    CACHE_KIND_PROJECT,
    CACHE_KIND_PYTORCH_WHEEL,
    CACHE_KIND_RUNTIME_DEPENDENT,
    CACHE_KIND_TRANSCRIPT_METADATA,
    CACHE_REASON_LEGACY_ROOT_PRESERVED,
    CACHE_REASON_MEDIA_PRESERVED,
    CACHE_REASON_MIGRATION_INCOMPLETE,
    CACHE_REASON_NOT_CLEANUP_TARGET,
    CACHE_REASON_NOT_DISCOVERABLE,
    CACHE_REASON_NOT_SELECTED,
    CACHE_REASON_OUTPUT_PRESERVED,
    CACHE_REASON_PROJECT_PRESERVED,
    CACHE_REASON_REFERENCED_DATA,
    CACHE_REASON_REUSE_UNVERIFIED,
    CACHE_REASON_SOURCE_MISSING,
    CACHE_REASON_UNSAFE_PATH,
    CACHE_STATE_PRESERVE,
    CACHE_STATE_REBUILD,
    CACHE_STATE_REMOVABLE_AFTER_SUCCESS,
    CACHE_STATE_REUSE,
    CATEGORY_CACHE,
    CATEGORY_LEGACY_RUNTIME,
    CATEGORY_MEDIA,
    CATEGORY_OUTPUT,
    CATEGORY_PROJECT,
    CATEGORY_RUNTIME_CONFIG,
    CATEGORY_SPEAKER_COLORS,
    CATEGORY_USER_SETTINGS,
    CacheCleanupEntry,
    CacheCleanupOptions,
    CacheCleanupPlan,
    CacheCleanupResult,
    CacheCleanupSkip,
    InventoryEntry,
    LegacyInventory,
    LegacyInventoryError,
    MigrationError,
    SCOPE_EXTERNAL,
    SCOPE_LEGACY_ROOT,
    STATUS_NOT_DISCOVERABLE,
    STATUS_PRESENT,
)
from .legacy_migration_inventory import (
    _PathObservation,
    _is_link_like,
    _lexical_absolute,
    _observe_path,
    _path_is_within,
    _root_observation,
    _safe_lstat,
    build_legacy_inventory,
)


def _cleanup_kind(entry: InventoryEntry) -> str | None:
    """Map a narrow inventory candidate to the cleanup policy kind."""

    candidate_id = (entry.candidate_id or "").casefold()
    relative = entry.relative_path.casefold()
    if entry.scope == SCOPE_EXTERNAL:
        return {
            "pip-user-cache": CACHE_KIND_PIP_DOWNLOAD,
            "pytorch-user-cache": CACHE_KIND_PYTORCH_WHEEL,
            "huggingface-user-cache": CACHE_KIND_HUGGINGFACE_WHISPERX_MODEL,
            "application-user-cache": CACHE_KIND_RUNTIME_DEPENDENT,
        }.get(candidate_id)
    if entry.category == CATEGORY_PROJECT:
        return CACHE_KIND_PROJECT
    if entry.category == CATEGORY_MEDIA:
        return CACHE_KIND_MEDIA
    if entry.category == CATEGORY_OUTPUT:
        return CACHE_KIND_OUTPUT
    if entry.category == CATEGORY_LEGACY_RUNTIME:
        if relative == ".venv":
            return CACHE_KIND_LEGACY_VENV
        if relative in {"src", "app", "source", "setup.bat", "start.bat"}:
            return CACHE_KIND_LEGACY_APP_SOURCE
    if entry.category in {
        CATEGORY_RUNTIME_CONFIG,
        CATEGORY_SPEAKER_COLORS,
        CATEGORY_USER_SETTINGS,
    }:
        # These are user settings and rollback evidence, not disposable app
        # source.  Keep them visible in the plan and protected from cleanup.
        return CACHE_KIND_LEGACY_APP_SOURCE
    if entry.category != CATEGORY_CACHE:
        return None
    if "audio-preview" in relative or "preview" in relative:
        return CACHE_KIND_AUDIO_PREVIEW
    if "transcript" in relative or relative.endswith(".cache.json"):
        return CACHE_KIND_TRANSCRIPT_METADATA
    if "pip" in relative:
        return CACHE_KIND_PIP_DOWNLOAD
    if "torch" in relative:
        return CACHE_KIND_PYTORCH_WHEEL
    if "huggingface" in relative or "whisperx" in relative or "model" in relative:
        return CACHE_KIND_HUGGINGFACE_WHISPERX_MODEL
    return CACHE_KIND_RUNTIME_DEPENDENT


def _cleanup_reference_data(
    kind: str,
    path: Path | None,
    inventory: LegacyInventory,
) -> tuple[str, ...]:
    if kind == CACHE_KIND_PROJECT and path is not None:
        return (str(path),)
    if kind == CACHE_KIND_MEDIA and path is not None:
        return (str(path),)
    if kind == CACHE_KIND_OUTPUT and path is not None:
        return (str(path),)
    if kind == CACHE_KIND_LEGACY_APP_SOURCE and path is not None:
        relative = path.relative_to(_lexical_absolute(inventory.legacy_root)).as_posix()
        if relative in {"src", "app", "source", "setup.bat", "start.bat"}:
            return ()
        return ("migrated user settings and rollback evidence",)
    if kind in {
        CACHE_KIND_AUDIO_PREVIEW,
        CACHE_KIND_TRANSCRIPT_METADATA,
        CACHE_KIND_RUNTIME_DEPENDENT,
    }:
        return (
            "project/media/output references are not migrated into this cache",
            "runtime fingerprint is not verified",
        )
    if kind in {
        CACHE_KIND_PIP_DOWNLOAD,
        CACHE_KIND_PYTORCH_WHEEL,
        CACHE_KIND_HUGGINGFACE_WHISPERX_MODEL,
    }:
        return ("package/runtime lock or content fingerprint is not verified",)
    return ()


def _lookup_cache_path(
    entry: InventoryEntry,
    kind: str,
    cache_paths: Mapping[str, str | os.PathLike[str]],
) -> Path | None:
    for key in (entry.candidate_id, kind, entry.relative_path):
        if key and key in cache_paths:
            return _lexical_absolute(cache_paths[key])
    return None


def _cleanup_observation(
    entry: InventoryEntry,
    path: Path | None,
) -> _PathObservation:
    if path is None:
        return _PathObservation(
            STATUS_NOT_DISCOVERABLE,
            "external",
            False,
            True,
            None,
            False,
            entry.diagnostic or "path was not explicitly supplied for inspection",
        )
    return _observe_path(path)


def _cleanup_entry_state(
    kind: str,
    entry: InventoryEntry,
    observation: _PathObservation,
    *,
    migration_completed: bool,
    reusable: bool,
) -> tuple[str, bool, str | None]:
    """Return state, deletion permission, and protection reason."""

    if kind == CACHE_KIND_PROJECT:
        return CACHE_STATE_PRESERVE, False, CACHE_REASON_PROJECT_PRESERVED
    if kind == CACHE_KIND_MEDIA:
        return CACHE_STATE_PRESERVE, False, CACHE_REASON_MEDIA_PRESERVED
    if kind == CACHE_KIND_OUTPUT:
        return CACHE_STATE_PRESERVE, False, CACHE_REASON_OUTPUT_PRESERVED
    if kind == CACHE_KIND_LEGACY_APP_SOURCE:
        relative = entry.relative_path.casefold()
        if relative not in {"src", "app", "source", "setup.bat", "start.bat"}:
            return CACHE_STATE_PRESERVE, False, CACHE_REASON_LEGACY_ROOT_PRESERVED
        if not migration_completed:
            return CACHE_STATE_PRESERVE, False, CACHE_REASON_MIGRATION_INCOMPLETE
        if observation.status != STATUS_PRESENT:
            return CACHE_STATE_PRESERVE, False, CACHE_REASON_SOURCE_MISSING
        if not observation.safe:
            return CACHE_STATE_PRESERVE, False, CACHE_REASON_UNSAFE_PATH
        return CACHE_STATE_REMOVABLE_AFTER_SUCCESS, True, None
    if kind == CACHE_KIND_LEGACY_VENV:
        if not migration_completed:
            return CACHE_STATE_PRESERVE, False, CACHE_REASON_MIGRATION_INCOMPLETE
        if observation.status != STATUS_PRESENT:
            return CACHE_STATE_PRESERVE, False, CACHE_REASON_SOURCE_MISSING
        if not observation.safe:
            return CACHE_STATE_PRESERVE, False, CACHE_REASON_UNSAFE_PATH
        return CACHE_STATE_REMOVABLE_AFTER_SUCCESS, True, None
    if observation.status == STATUS_NOT_DISCOVERABLE:
        return CACHE_STATE_REBUILD, False, CACHE_REASON_NOT_DISCOVERABLE
    if observation.status != STATUS_PRESENT:
        return CACHE_STATE_REBUILD, False, CACHE_REASON_SOURCE_MISSING
    if not observation.safe:
        return CACHE_STATE_REBUILD, False, CACHE_REASON_UNSAFE_PATH
    if reusable:
        return CACHE_STATE_REUSE, False, None
    if not migration_completed:
        return CACHE_STATE_REBUILD, False, CACHE_REASON_MIGRATION_INCOMPLETE
    # A stale/unknown cache must still be rebuilt by the new runtime.  Cleanup
    # permission is a separate bit so the plan can report both facts without
    # pretending that deletion makes the cache reusable.
    return CACHE_STATE_REBUILD, True, CACHE_REASON_REUSE_UNVERIFIED


def build_cache_cleanup_plan(
    inventory: LegacyInventory,
    *,
    options: CacheCleanupOptions | None = None,
    migration_completed: bool | None = None,
    cache_paths: Mapping[str, str | os.PathLike[str]] | None = None,
    referenced_data: Mapping[str, Sequence[str]] | None = None,
    reusable_cache_ids: Sequence[str] | None = None,
    selected: Sequence[str] | None = None,
    confirm: bool | None = None,
) -> CacheCleanupPlan:
    """Build a secret-free cache classification and cleanup dry-run.

    The normal source is a :class:`LegacyInventory`; external cache roots are
    deliberately absent until a caller supplies them by exact candidate ID.
    This function only observes paths and never copies or deletes them.
    """

    if not isinstance(inventory, LegacyInventory):
        raise LegacyInventoryError("cache cleanup requires a LegacyInventory")
    policy = options or CacheCleanupOptions()
    if migration_completed is not None:
        policy = CacheCleanupOptions(
            migration_completed=migration_completed,
            confirm=policy.confirm,
            selected=policy.selected,
            cache_paths=policy.cache_paths,
            referenced_data=policy.referenced_data,
            reusable_cache_ids=policy.reusable_cache_ids,
        )
    if confirm is not None or selected is not None or cache_paths is not None or referenced_data is not None:
        policy = CacheCleanupOptions(
            migration_completed=policy.migration_completed,
            confirm=policy.confirm if confirm is None else confirm,
            selected=policy.selected if selected is None else tuple(str(value) for value in selected),
            cache_paths=policy.cache_paths if cache_paths is None else cache_paths,
            referenced_data=policy.referenced_data if referenced_data is None else referenced_data,
            reusable_cache_ids=policy.reusable_cache_ids,
        )
    if reusable_cache_ids is not None:
        policy = CacheCleanupOptions(
            migration_completed=policy.migration_completed,
            confirm=policy.confirm,
            selected=policy.selected,
            cache_paths=policy.cache_paths,
            referenced_data=policy.referenced_data,
            reusable_cache_ids=tuple(str(value) for value in reusable_cache_ids),
        )

    source_root = _lexical_absolute(inventory.legacy_root)
    selected_ids = {str(value).casefold() for value in policy.selected}
    reusable_ids = {str(value).casefold() for value in policy.reusable_cache_ids}
    entries: list[CacheCleanupEntry] = []
    diagnostics: list[str] = []
    references_for_root = tuple(
        entry.path
        for entry in inventory.entries
        if entry.exists and entry.category in {CATEGORY_PROJECT, CATEGORY_MEDIA, CATEGORY_OUTPUT}
    )

    # The root is intentionally represented so callers can see why it is not a
    # valid cleanup target.  Deleting it would also delete user project/media/
    # output data, even after a successful migration.
    root_observation = _root_observation(source_root)
    entries.append(
        CacheCleanupEntry(
            candidate_id="legacy:app-root",
            kind=CACHE_KIND_LEGACY_APP_SOURCE,
            path=str(source_root),
            state=CACHE_STATE_PRESERVE,
            exists=root_observation.exists,
            safe=root_observation.safe,
            size_bytes=None,
            reclaimable_bytes=0,
            cleanup_allowed=False,
            protection_reason=CACHE_REASON_LEGACY_ROOT_PRESERVED,
            referenced_data=tuple(references_for_root),
            diagnostic=root_observation.diagnostic,
            scope=SCOPE_LEGACY_ROOT,
        )
    )

    for entry in inventory.entries:
        kind = _cleanup_kind(entry)
        if kind is None:
            continue
        explicit_path = _lookup_cache_path(entry, kind, policy.cache_paths)
        path = Path(entry.path) if entry.scope == SCOPE_LEGACY_ROOT else explicit_path
        observation = _cleanup_observation(entry, path)
        reusable = (entry.candidate_id or "").casefold() in reusable_ids
        state, cleanup_allowed, protection_reason = _cleanup_entry_state(
            kind,
            entry,
            observation,
            migration_completed=policy.migration_completed,
            reusable=reusable,
        )
        if selected_ids and (entry.candidate_id or "").casefold() not in selected_ids:
            cleanup_allowed = False
            if state == CACHE_STATE_REMOVABLE_AFTER_SUCCESS:
                protection_reason = CACHE_REASON_NOT_SELECTED
        if observation.diagnostic:
            diagnostics.append(f"{entry.candidate_id or entry.relative_path}: {observation.diagnostic}")
        custom_references = bool(policy.referenced_data.get(entry.candidate_id or "", ()))
        references = tuple(policy.referenced_data.get(entry.candidate_id or "", ()))
        if not references:
            references = _cleanup_reference_data(kind, path, inventory)
        if custom_references and kind not in {
            CACHE_KIND_PROJECT,
            CACHE_KIND_MEDIA,
            CACHE_KIND_OUTPUT,
            CACHE_KIND_LEGACY_APP_SOURCE,
        }:
            cleanup_allowed = False
            protection_reason = CACHE_REASON_REFERENCED_DATA
        if references and kind in {
            CACHE_KIND_PROJECT,
            CACHE_KIND_MEDIA,
            CACHE_KIND_OUTPUT,
            CACHE_KIND_LEGACY_APP_SOURCE,
        }:
            cleanup_allowed = False
        if cleanup_allowed and observation.status == STATUS_PRESENT and observation.safe:
            reclaimable = observation.size_bytes or 0
        else:
            reclaimable = 0
        entries.append(
            CacheCleanupEntry(
                candidate_id=entry.candidate_id or f"legacy:{entry.relative_path}",
                kind=kind,
                path=str(path) if path is not None else "",
                state=state,
                exists=observation.exists,
                safe=observation.safe,
                size_bytes=observation.size_bytes,
                reclaimable_bytes=reclaimable,
                cleanup_allowed=cleanup_allowed,
                protection_reason=protection_reason,
                referenced_data=tuple(sorted(set(references))),
                diagnostic=observation.diagnostic,
                scope=entry.scope,
            )
        )
    return CacheCleanupPlan(
        schema_version=CACHE_CLEANUP_SCHEMA_VERSION,
        source=str(source_root),
        inventory_schema_version=inventory.schema_version,
        migration_completed=policy.migration_completed,
        confirm=policy.confirm,
        selected=tuple(policy.selected),
        entries=tuple(entries),
        reclaimable_bytes=sum(entry.reclaimable_bytes for entry in entries),
        diagnostics=tuple(sorted(set(diagnostics))),
    )


def _cleanup_path_nodes(root: Path, target: Path) -> tuple[tuple[Path, bool, int], ...]:
    """Preflight a cleanup target without following links.

    The returned nodes are all regular files or directories under ``target``.
    No mutation occurs until every selected target has passed this check.
    """

    root = _lexical_absolute(root)
    target = _lexical_absolute(target)
    if root == target:
        raise MigrationError("legacy root itself is never a cleanup target")
    if not _path_is_within(root, target):
        raise MigrationError(f"cleanup target escapes selected root: {target}")
    root_observation = _root_observation(root)
    if not root_observation.exists or not root_observation.safe or root_observation.node_type != "directory":
        raise MigrationError(f"cleanup root is not a safe directory: {root}")
    # Compare resolved roots as well as lexical paths to catch a symlinked
    # parent that could make an apparently contained path escape the root.
    try:
        if root.resolve(strict=True) != root or target.resolve(strict=False) != target:
            raise MigrationError(f"cleanup path contains a symbolic link or junction: {target}")
    except OSError as exc:
        raise MigrationError(f"unable to resolve cleanup path: {target}") from exc
    target_metadata, target_error = _safe_lstat(target)
    if target_error:
        raise MigrationError(f"unable to inspect cleanup target: {target}")
    if target_metadata is None:
        return ()
    if _is_link_like(target, target_metadata):
        raise MigrationError(f"cleanup target is a symbolic link or junction: {target}")
    pending = [target]
    nodes: list[tuple[Path, bool, int]] = []
    while pending:
        current = pending.pop()
        metadata, error = _safe_lstat(current)
        if error or metadata is None:
            raise MigrationError(f"unable to inspect cleanup path: {current}")
        if _is_link_like(current, metadata):
            raise MigrationError(f"cleanup path contains a symbolic link or junction: {current}")
        if stat.S_ISREG(metadata.st_mode):
            nodes.append((current, False, metadata.st_size))
            continue
        if not stat.S_ISDIR(metadata.st_mode):
            raise MigrationError(f"cleanup path contains a special filesystem node: {current}")
        nodes.append((current, True, 0))
        try:
            with os.scandir(current) as children:
                child_paths = [Path(child.path) for child in children]
        except (OSError, ValueError, TypeError) as exc:
            raise MigrationError(f"unable to enumerate cleanup path: {current}") from exc
        pending.extend(child_paths)
    return tuple(nodes)


def _remove_cleanup_nodes(nodes: Sequence[tuple[Path, bool, int]]) -> int:
    reclaimed = 0
    for path, is_directory, size in sorted(nodes, key=lambda item: len(item[0].parts), reverse=True):
        metadata, error = _safe_lstat(path)
        if error or metadata is None:
            continue
        if _is_link_like(path, metadata):
            raise MigrationError(f"cleanup path changed to a symbolic link or junction: {path}")
        if is_directory:
            path.rmdir()
        else:
            path.unlink()
            reclaimed += size
    return reclaimed


def apply_cache_cleanup(
    plan: CacheCleanupPlan,
    *,
    confirm: bool | None = None,
    selected: Sequence[str] | None = None,
    now: datetime | None = None,
) -> CacheCleanupResult:
    """Delete only selected, plan-approved paths after explicit confirmation.

    This is deliberately a post-migration operation.  It has no rollback
    claim; callers must only set ``migration_completed`` after the settings
    transaction and runtime setup have succeeded.
    """

    if not isinstance(plan, CacheCleanupPlan):
        raise LegacyInventoryError("cache cleanup apply requires a CacheCleanupPlan")
    confirmed = plan.confirm if confirm is None else confirm
    if not confirmed:
        raise MigrationError("explicit confirmation is required for cache cleanup")
    requested = plan.selected if selected is None else tuple(str(value) for value in selected)
    requested_ids = {value.casefold() for value in requested}
    # An empty selection is intentionally a no-op.  Cleanup is destructive and
    # must never widen an omitted selection to every plan-approved candidate.
    # Callers must name at least one candidate ID explicitly, either when the
    # plan is built or when it is applied.
    if not requested_ids:
        skipped = tuple(
            CacheCleanupSkip(
                candidate_id=entry.candidate_id,
                path=entry.path,
                reason=CACHE_REASON_NOT_SELECTED,
                diagnostic="an explicit candidate ID is required for cleanup",
            )
            for entry in plan.entries
            if entry.cleanup_allowed
        )
        timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return CacheCleanupResult(
            schema_version=CACHE_CLEANUP_SCHEMA_VERSION,
            source=plan.source,
            removed=(),
            skipped=skipped,
            reclaimed_bytes=0,
            diagnostics=plan.diagnostics + ("cleanup skipped: explicit candidate selection is required",),
            completed_at=timestamp,
        )
    candidates = {
        entry.candidate_id.casefold(): entry for entry in plan.entries if entry.candidate_id.casefold() in requested_ids
    }
    skipped: list[CacheCleanupSkip] = []
    prepared: list[tuple[CacheCleanupEntry, tuple[tuple[Path, bool, int], ...]]] = []
    for entry in candidates.values():
        if not entry.cleanup_allowed:
            skipped.append(
                CacheCleanupSkip(
                    candidate_id=entry.candidate_id,
                    path=entry.path,
                    reason=entry.protection_reason or CACHE_REASON_NOT_CLEANUP_TARGET,
                    diagnostic=entry.diagnostic,
                )
            )
            continue
        if not plan.migration_completed or entry.state not in {
            CACHE_STATE_REBUILD,
            CACHE_STATE_REMOVABLE_AFTER_SUCCESS,
        }:
            skipped.append(
                CacheCleanupSkip(
                    candidate_id=entry.candidate_id,
                    path=entry.path,
                    reason=CACHE_REASON_MIGRATION_INCOMPLETE,
                )
            )
            continue
        if not entry.path:
            skipped.append(
                CacheCleanupSkip(
                    candidate_id=entry.candidate_id,
                    path=entry.path,
                    reason=CACHE_REASON_NOT_DISCOVERABLE,
                    diagnostic=entry.diagnostic,
                )
            )
            continue
        root = _lexical_absolute(plan.source)
        target = _lexical_absolute(entry.path)
        if entry.scope == SCOPE_EXTERNAL:
            # External cache targets are accepted only as an exact explicitly
            # supplied path.  Its parent is the containment root; the target
            # itself is never treated as a broad filesystem root.
            root = target.parent
            if target == Path(target.anchor):
                raise MigrationError(f"external cleanup target is too broad: {target}")
        try:
            nodes = _cleanup_path_nodes(root, target)
        except MigrationError as exc:
            raise MigrationError(f"unsafe cleanup target {target}: {exc}") from exc
        if not nodes:
            skipped.append(
                CacheCleanupSkip(
                    candidate_id=entry.candidate_id,
                    path=entry.path,
                    reason=CACHE_REASON_SOURCE_MISSING,
                )
            )
            continue
        prepared.append((entry, nodes))

    # Preflight all selected targets before deleting any one of them.  Nested
    # candidates such as ``.cache`` and ``.cache/pip`` are de-duplicated by
    # path, while their individual plan entries remain auditable.
    unique_nodes: dict[Path, tuple[tuple[Path, bool, int], ...]] = {}
    for entry, nodes in prepared:
        target = _lexical_absolute(entry.path)
        unique_nodes.setdefault(target, nodes)
    removed: list[str] = []
    reclaimed_bytes = 0
    for target, nodes in sorted(unique_nodes.items(), key=lambda item: len(item[0].parts), reverse=True):
        reclaimed_bytes += _remove_cleanup_nodes(nodes)
        removed.append(str(target))
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return CacheCleanupResult(
        schema_version=CACHE_CLEANUP_SCHEMA_VERSION,
        source=plan.source,
        removed=tuple(sorted(set(removed))),
        skipped=tuple(skipped),
        reclaimed_bytes=reclaimed_bytes,
        diagnostics=plan.diagnostics,
        completed_at=timestamp,
    )


def build_legacy_cache_cleanup_plan(
    legacy_root: str | os.PathLike[str],
    *,
    options: CacheCleanupOptions | None = None,
    migration_completed: bool | None = None,
    cache_paths: Mapping[str, str | os.PathLike[str]] | None = None,
    referenced_data: Mapping[str, Sequence[str]] | None = None,
    reusable_cache_ids: Sequence[str] | None = None,
    selected: Sequence[str] | None = None,
    confirm: bool | None = None,
) -> CacheCleanupPlan:
    return build_cache_cleanup_plan(
        build_legacy_inventory(legacy_root),
        options=options,
        migration_completed=migration_completed,
        cache_paths=cache_paths,
        referenced_data=referenced_data,
        reusable_cache_ids=reusable_cache_ids,
        selected=selected,
        confirm=confirm,
    )


def apply_legacy_cache_cleanup(
    plan: CacheCleanupPlan,
    *,
    confirm: bool | None = None,
    selected: Sequence[str] | None = None,
    now: datetime | None = None,
) -> CacheCleanupResult:
    return apply_cache_cleanup(plan, confirm=confirm, selected=selected, now=now)


def apply_cache_cleanup_plan(
    plan: CacheCleanupPlan,
    *,
    confirm: bool | None = None,
    selected: Sequence[str] | None = None,
    now: datetime | None = None,
) -> CacheCleanupResult:
    return apply_cache_cleanup(plan, confirm=confirm, selected=selected, now=now)


# Generic aliases for callers that do not need to mention the legacy source in
# their service boundary.  They all route to the one implementation above.
build_cleanup_plan = build_cache_cleanup_plan
apply_cleanup_plan = apply_cache_cleanup
