"""旧環境からのユーザー設定移行とロールバックを担当する。"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Mapping, Sequence

from . import installer_migration as _installer_migration
from .data_boundary import decode_json, is_object_dict, is_object_list, is_object_mapping
from .legacy_migration_types import (
    CATEGORY_RUNTIME_CONFIG,
    CATEGORY_SPEAKER_COLORS,
    CATEGORY_USER_SETTINGS,
    ChannelPreset,
    ChannelPresetError,
    InventoryEntry,
    LegacyInventory,
    LegacyInventoryError,
    LegacySettingsMigrationPlan,
    LegacySettingsMigrationResult,
    MIGRATION_ITEM_INVALID,
    MIGRATION_ITEM_READY,
    MIGRATION_ITEM_SKIPPED,
    MIGRATION_OPERATION_COPY,
    MIGRATION_OPERATION_SKIP,
    MIGRATION_REASON_CONFIRMATION_REQUIRED,
    MIGRATION_REASON_DESTINATION_EXISTS,
    MIGRATION_REASON_EXTERNAL_REFERENCE,
    MIGRATION_REASON_INVALID,
    MIGRATION_REASON_NOT_SELECTED,
    MIGRATION_REASON_SOURCE_MISSING,
    MIGRATION_REASON_UNSAFE_SOURCE,
    MigrationError,
    PRESET_SCHEMA_VERSION,
    RuntimeCapabilities,
    SCOPE_LEGACY_ROOT,
    SETTINGS_MIGRATION_SCHEMA_VERSION,
    SETTING_DICTIONARY,
    SETTING_PRESET,
    SETTING_RUNTIME_CONFIG,
    SETTING_SPEAKER_COLORS,
    SETTING_USER_SETTINGS,
    STATUS_MISSING,
    STATUS_PRESENT,
    SettingsMigrationItem,
    SettingsMigrationOptions,
    SettingsMigrationSkip,
    TranscriptionDictionaryError,
    load_transcription_dictionary,
)
from .legacy_migration_inventory import (
    _is_link_like,
    _lexical_absolute,
    _safe_lstat,
    _path_is_within,
    _ensure_safe_destination,
    _json_bytes,
    build_legacy_inventory,
)


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
        str(value).casefold() for value in (entry.candidate_id or "", entry.relative_path, kind, entry.category)
    }
    return bool(candidates & selected)


def _json_key_paths(value: object, prefix: str = "") -> tuple[str, ...]:
    paths: list[str] = []
    if is_object_mapping(value):
        for key in sorted(value, key=str):
            child = f"{prefix}.{key}" if prefix else str(key)
            paths.append(child)
            paths.extend(_json_key_paths(value[key], child))
    elif is_object_list(value):
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
) -> tuple[dict[str, object], tuple[str, ...]]:
    payload, adjusted = _installer_migration.validated_runtime_config(source_path, capabilities)
    changes = list(adjusted)
    pipeline = payload.get("craig_pipeline")
    context = pipeline.get("transcription_context") if is_object_dict(pipeline) else None
    if is_object_dict(context):
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
        raw_json_payload = decode_json(source_path.read_text(encoding="utf-8-sig"))
        _assert_safe_user_setting_payload(raw_json_payload)
        if kind == SETTING_DICTIONARY:
            normalized = load_transcription_dictionary(source_path).to_json()
            return _json_bytes(normalized), ("dictionary.normalized",), _json_key_paths(normalized)
        elif kind == SETTING_PRESET:
            _validate_preset_payload(raw_json_payload, source_path)
        elif kind == SETTING_USER_SETTINGS:
            if not isinstance(raw_json_payload, Mapping):
                raise MigrationError(f"user settings root must be an object: {source_path}")
        return source_path.read_bytes(), (), _json_key_paths(raw_json_payload)
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
                MIGRATION_REASON_SOURCE_MISSING if entry.status == STATUS_MISSING else MIGRATION_REASON_UNSAFE_SOURCE
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


def _path_depth(path: Path) -> int:
    return len(path.parts)


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
    return snapshots, tuple(sorted(parents, key=_path_depth, reverse=True))


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
                            f"{item.candidate_id}:{relative.as_posix()}" if source_path.is_dir() else item.candidate_id
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
