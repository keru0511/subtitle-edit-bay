"""Installer first-run migration orchestration.

The migration slices own the safety decisions in :mod:`legacy_migration`.
This module is deliberately a thin entrypoint for the Installer: it builds a
secret-free plan for the review UI, applies the already validated settings
transaction, and records the resulting diagnostics.  It never executes or
copies anything from the legacy runtime and it never removes legacy data.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from .installer_migration import MigrationError, RuntimeCapabilities, validate_legacy_workspace
from .legacy_migration import (
    CACHE_STATE_REMOVABLE_AFTER_SUCCESS,
    CATEGORY_MEDIA,
    CATEGORY_OUTPUT,
    CATEGORY_PROJECT,
    CacheCleanupOptions,
    CacheCleanupPlan,
    LegacyInventory,
    LegacySettingsMigrationPlan,
    LegacySettingsMigrationResult,
    SettingsMigrationOptions,
    apply_settings_migration,
    build_cache_cleanup_plan,
    build_legacy_inventory,
    build_settings_migration_plan,
)


ENTRYPOINT_SCHEMA_VERSION = 1
PLAN_DIGEST_FIELD = "plan_sha256"


@dataclass(frozen=True, slots=True)
class InstallerMigrationRequest:
    """Immutable choices captured by the Installer migration request."""

    source: str
    destination: str
    runtime_config: bool = True
    speaker_colors: bool = True
    workspace_reference: bool = True
    dictionaries: bool = True
    presets: bool = True
    user_settings: bool = True
    overwrite: bool = False
    confirm: bool = False
    cuda: bool = False
    nvenc: bool = False

    @property
    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(cuda=self.cuda, nvenc=self.nvenc)

    @property
    def source_path(self) -> Path:
        return Path(self.source).expanduser().resolve()

    @property
    def destination_path(self) -> Path:
        return Path(self.destination).expanduser().resolve()

    def settings_options(self) -> SettingsMigrationOptions:
        return SettingsMigrationOptions(
            runtime_config=self.runtime_config,
            speaker_colors=self.speaker_colors,
            dictionaries=self.dictionaries,
            presets=self.presets,
            user_settings=self.user_settings,
            overwrite=self.overwrite,
            confirm=self.confirm,
            capabilities=self.capabilities,
        )


@dataclass(frozen=True, slots=True)
class InstallerMigrationPlan:
    """Reviewable plan assembled from the three migration core services."""

    request: InstallerMigrationRequest
    inventory: LegacyInventory
    settings: LegacySettingsMigrationPlan
    cleanup_before_success: CacheCleanupPlan
    workspace_references: tuple[str, ...]
    diagnostics: tuple[str, ...]
    filesystem_snapshot: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        request = {
            "source": self.request.source,
            "destination": self.request.destination,
            "runtime_config": self.request.runtime_config,
            "speaker_colors": self.request.speaker_colors,
            "workspace_reference": self.request.workspace_reference,
            "dictionaries": self.request.dictionaries,
            "presets": self.request.presets,
            "user_settings": self.request.user_settings,
            "overwrite": self.request.overwrite,
            "confirm": self.request.confirm,
            "capabilities": {"cuda": self.request.cuda, "nvenc": self.request.nvenc},
        }
        return {
            "schema_version": ENTRYPOINT_SCHEMA_VERSION,
            "request": request,
            "source": str(self.inventory.legacy_root),
            "destination": self.request.destination,
            "inventory": self.inventory.to_dict(),
            "settings": self.settings.to_dict(),
            "cleanup": self.cleanup_before_success.to_dict(),
            "workspace_references": list(self.workspace_references),
            "diagnostics": list(self.diagnostics),
            "filesystem_snapshot": [dict(item) for item in self.filesystem_snapshot],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True) + "\n"


def _timestamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _plan_identity_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Normalize apply-time confirmation that has no effect without overwrite."""

    normalized = dict(payload)
    request = payload.get("request")
    if isinstance(request, Mapping) and not bool(request.get("overwrite", False)):
        normalized_request = dict(request)
        normalized_request["confirm"] = False
        normalized["request"] = normalized_request
        settings = payload.get("settings")
        if isinstance(settings, Mapping):
            normalized_settings = dict(settings)
            normalized_settings["confirm"] = False
            normalized["settings"] = normalized_settings
    return normalized


def plan_sha256(payload: Mapping[str, object]) -> str:
    """Return the stable identity of a reviewable plan payload."""

    return hashlib.sha256(_canonical_json_bytes(_plan_identity_payload(payload))).hexdigest()


def _plan_file_payload(plan: InstallerMigrationPlan) -> dict[str, object]:
    payload = plan.to_dict()
    payload[PLAN_DIGEST_FIELD] = plan_sha256(payload)
    return payload


def _filesystem_snapshot_for_path(
    path: Path,
    *,
    scope: str,
    root: Path,
) -> list[dict[str, object]]:
    """Capture only metadata/content hashes used by the migration transaction."""

    try:
        relative_root = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise MigrationError(f"migration snapshot path escapes selected root: {path}") from exc

    snapshots: list[dict[str, object]] = []

    def record(candidate: Path, relative_path: str) -> None:
        if candidate.is_symlink():
            snapshots.append(
                {
                    "scope": scope,
                    "relative_path": relative_path,
                    "exists": True,
                    "node_type": "symlink",
                    "size_bytes": None,
                    "sha256": None,
                }
            )
            return
        if not candidate.exists():
            snapshots.append(
                {
                    "scope": scope,
                    "relative_path": relative_path,
                    "exists": False,
                    "node_type": "missing",
                    "size_bytes": None,
                    "sha256": None,
                }
            )
            return
        if candidate.is_file():
            try:
                payload = candidate.read_bytes()
            except OSError as exc:
                raise MigrationError(f"unable to snapshot migration file: {candidate}") from exc
            snapshots.append(
                {
                    "scope": scope,
                    "relative_path": relative_path,
                    "exists": True,
                    "node_type": "file",
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
            return
        if not candidate.is_dir():
            snapshots.append(
                {
                    "scope": scope,
                    "relative_path": relative_path,
                    "exists": True,
                    "node_type": "other",
                    "size_bytes": None,
                    "sha256": None,
                }
            )
            return

        snapshots.append(
            {
                "scope": scope,
                "relative_path": relative_path,
                "exists": True,
                "node_type": "directory",
                "size_bytes": None,
                "sha256": None,
            }
        )
        try:
            children = sorted(candidate.iterdir(), key=lambda item: item.name.casefold())
        except OSError as exc:
            raise MigrationError(f"unable to snapshot migration directory: {candidate}") from exc
        for child in children:
            child_relative = f"{relative_path}/{child.name}" if relative_path else child.name
            record(child, child_relative)

    record(path, relative_root)
    return snapshots


def _build_filesystem_snapshot(
    settings: LegacySettingsMigrationPlan,
    *,
    source: Path,
    destination: Path,
    include_workspace_registry: bool,
) -> tuple[dict[str, object], ...]:
    paths: dict[tuple[str, str], Path] = {}
    for item in settings.items:
        paths[("source", item.source_path)] = Path(item.source_path)
        paths[("destination", item.destination_path)] = Path(item.destination_path)
    if include_workspace_registry:
        registry = _workspace_registry_path(destination)
        paths[("destination", str(registry))] = registry

    snapshots: list[dict[str, object]] = []
    for (scope, _path_text), path in sorted(paths.items(), key=lambda item: (item[0][0], item[0][1])):
        root = source if scope == "source" else destination
        snapshots.extend(_filesystem_snapshot_for_path(path, scope=scope, root=root))
    return tuple(
        sorted(
            snapshots,
            key=lambda item: (str(item["scope"]), str(item["relative_path"]), str(item["node_type"])),
        )
    )


def _read_reviewed_plan_digest(path: Path) -> str:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise MigrationError(f"reviewed migration plan must be a regular file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationError(f"reviewed migration plan could not be read: {path}") from exc
    if not isinstance(payload, Mapping):
        raise MigrationError("reviewed migration plan must contain an object")
    if payload.get("schema_version") != ENTRYPOINT_SCHEMA_VERSION:
        raise MigrationError("reviewed migration plan has an unsupported schema")
    digest = payload.get(PLAN_DIGEST_FIELD)
    if not isinstance(digest, str) or len(digest) != hashlib.sha256().digest_size * 2:
        raise MigrationError("reviewed migration plan is missing a valid plan identity")
    unsigned_payload = {key: value for key, value in payload.items() if key != PLAN_DIGEST_FIELD}
    if not hmac.compare_digest(plan_sha256(unsigned_payload), digest):
        raise MigrationError("reviewed migration plan identity is invalid")
    return digest


def _atomic_json_write(path: Path, payload: object) -> None:
    path = path.expanduser()
    current = path
    while current != current.parent:
        if current.is_symlink():
            raise MigrationError(f"migration result path contains a symbolic link: {current}")
        current = current.parent
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise MigrationError(f"migration result target must be a regular file: {path}")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _workspace_references(inventory: LegacyInventory) -> tuple[str, ...]:
    """Return existing project/media/output paths without following links."""

    return tuple(
        sorted(
            {
                entry.path
                for entry in inventory.entries
                if entry.exists
                and entry.safe
                and entry.category in {CATEGORY_PROJECT, CATEGORY_MEDIA, CATEGORY_OUTPUT}
            }
        )
    )


def _path_is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _workspace_registry_path(destination: Path) -> Path:
    path = destination / ".gui" / "legacy_workspaces.json"
    if not _path_is_within(destination, path):
        raise MigrationError(f"workspace registry escapes destination: {path}")
    return path


def _merge_workspace_registry(path: Path, source: Path, references: Sequence[str]) -> dict[str, object]:
    """Merge one source into the existing registry without copying user data."""

    workspaces: dict[str, dict[str, object]] = {}
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise MigrationError(f"legacy workspace registry must be a regular file: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MigrationError(f"invalid legacy workspace registry: {path}") from exc
        if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
            raise MigrationError(f"unsupported legacy workspace registry schema: {path}")
        entries = payload.get("workspaces")
        if not isinstance(entries, list):
            raise MigrationError(f"legacy workspace registry must contain an array: {path}")
        for entry in entries:
            if not isinstance(entry, Mapping) or not isinstance(entry.get("path"), str):
                raise MigrationError(f"invalid legacy workspace entry: {path}")
            resources = entry.get("resources", [])
            if not isinstance(resources, list) or not all(isinstance(item, str) for item in resources):
                raise MigrationError(f"invalid legacy workspace resources: {path}")
            normalized = str(Path(entry["path"]).expanduser().resolve())
            workspaces[normalized.casefold()] = {
                "path": normalized,
                "resources": sorted({str(Path(item).expanduser().resolve()) for item in resources}),
            }
    normalized_source = str(source.resolve())
    workspaces[normalized_source.casefold()] = {
        "path": normalized_source,
        "resources": sorted({str(Path(item).expanduser().resolve()) for item in references}),
    }
    return {
        "schema_version": 1,
        "workspaces": [workspaces[key] for key in sorted(workspaces)],
    }


def _validate_request(request: InstallerMigrationRequest) -> tuple[Path, Path]:
    source = request.source_path
    destination = request.destination_path
    if source == destination:
        raise MigrationError("migration source and installation destination must be different")
    validate_legacy_workspace(source, destination)
    if destination.exists() and (destination.is_symlink() or not destination.is_dir()):
        raise MigrationError(f"migration destination must be a regular directory: {destination}")
    return source, destination


def build_installer_migration_plan(request: InstallerMigrationRequest) -> InstallerMigrationPlan:
    """Build the first-run review model without mutating either workspace."""

    source, destination = _validate_request(request)
    inventory = build_legacy_inventory(source)
    settings = build_settings_migration_plan(
        inventory,
        destination,
        options=request.settings_options(),
        capabilities=request.capabilities,
    )
    cleanup = build_cache_cleanup_plan(
        inventory,
        options=CacheCleanupOptions(migration_completed=False),
    )
    filesystem_snapshot = _build_filesystem_snapshot(
        settings,
        source=source,
        destination=destination,
        include_workspace_registry=request.workspace_reference,
    )
    references = _workspace_references(inventory) if request.workspace_reference else ()
    diagnostics = tuple(
        sorted(
            set(
                (
                    *inventory.diagnostics,
                    *settings.diagnostics,
                    *cleanup.diagnostics,
                )
            )
        )
    )
    return InstallerMigrationPlan(
        request=request,
        inventory=inventory,
        settings=settings,
        cleanup_before_success=cleanup,
        workspace_references=references,
        diagnostics=diagnostics,
        filesystem_snapshot=filesystem_snapshot,
    )


def apply_installer_migration(
    plan: InstallerMigrationPlan,
    *,
    now: datetime | None = None,
    expected_plan_sha256: str | None = None,
) -> dict[str, object]:
    """Apply only the reviewed settings and registry reference transaction.

    Cleanup is intentionally never part of this operation.  The returned
    post-success plan is informational and requires a separate explicit
    candidate selection and confirmation through ``legacy_migration``.
    """

    if not isinstance(plan, InstallerMigrationPlan):
        raise MigrationError("installer migration apply requires an InstallerMigrationPlan")
    reviewed_plan_sha256 = expected_plan_sha256 or plan_sha256(plan.to_dict())
    if not hmac.compare_digest(plan_sha256(plan.to_dict()), reviewed_plan_sha256):
        raise MigrationError("reviewed migration plan identity is invalid")
    request = plan.request
    _validate_request(request)
    current_plan = build_installer_migration_plan(request)
    if not hmac.compare_digest(plan_sha256(current_plan.to_dict()), reviewed_plan_sha256):
        raise MigrationError("reviewed migration plan is stale; source or destination changed")
    plan = current_plan
    # Validate and prepare the registry before the settings transaction starts.
    # A malformed existing registry must not leave a partially applied settings
    # migration behind; the core settings transaction remains the only writer
    # for settings files.
    registry_path: Path | None = None
    registry_payload: dict[str, object] | None = None
    if request.workspace_reference and plan.workspace_references:
        registry_path = _workspace_registry_path(request.destination_path)
        registry_payload = _merge_workspace_registry(
            registry_path,
            request.source_path,
            plan.workspace_references,
        )
    settings_result: LegacySettingsMigrationResult = apply_settings_migration(
        plan.settings,
        confirm=request.confirm,
        now=now,
    )
    if registry_path is not None and registry_payload is not None:
        _atomic_json_write(registry_path, registry_payload)
    completed_at = _timestamp(now)
    cleanup_after_success = build_cache_cleanup_plan(
        plan.inventory,
        options=CacheCleanupOptions(migration_completed=True),
    )
    applied = list(settings_result.applied)
    if registry_path is not None:
        applied.append(str(registry_path))
    result: dict[str, object] = {
        "schema_version": ENTRYPOINT_SCHEMA_VERSION,
        "source": request.source,
        "destination": request.destination,
        "applied": sorted(set(applied)),
        "settings": settings_result.to_dict(),
        "workspace_references": list(plan.workspace_references),
        "workspace_registry": str(registry_path) if registry_path is not None else None,
        "cleanup": cleanup_after_success.to_dict(),
        "cleanup_candidates": [
            entry.to_dict()
            for entry in cleanup_after_success.entries
            if entry.state == CACHE_STATE_REMOVABLE_AFTER_SUCCESS
        ],
        "diagnostics": list(
            sorted(set((*plan.diagnostics, *settings_result.diagnostics, *cleanup_after_success.diagnostics)))
        ),
        "completed_at": completed_at,
    }
    record_name = completed_at.replace(":", "").replace("-", "")
    record_path = request.destination_path / ".local" / "migration" / f"migration-{record_name}.json"
    _atomic_json_write(record_path, result)
    result["record_path"] = str(record_path)
    return result


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review or apply Installer first-run migration.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--cuda", action="store_true")
    parser.add_argument("--nvenc", action="store_true")
    parser.add_argument("--skip-runtime-config", action="store_true")
    parser.add_argument("--skip-speaker-colors", action="store_true")
    parser.add_argument("--skip-workspace-reference", action="store_true")
    parser.add_argument("--plan-output")
    parser.add_argument("--plan-input")
    parser.add_argument("--result-output")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    request = InstallerMigrationRequest(
        source=args.source,
        destination=args.destination,
        runtime_config=not args.skip_runtime_config,
        speaker_colors=not args.skip_speaker_colors,
        workspace_reference=not args.skip_workspace_reference,
        overwrite=args.overwrite,
        confirm=args.confirm,
        cuda=args.cuda,
        nvenc=args.nvenc,
    )
    try:
        plan = build_installer_migration_plan(request)
        reviewed_plan_sha256: str | None = None
        if args.apply:
            if not args.plan_input:
                raise MigrationError("--plan-input is required when applying a migration plan")
            reviewed_plan_sha256 = _read_reviewed_plan_digest(Path(args.plan_input))
            if not hmac.compare_digest(plan_sha256(plan.to_dict()), reviewed_plan_sha256):
                raise MigrationError("reviewed migration plan is stale; source or destination changed")
        plan_json = plan.to_json()
        if args.plan_output:
            _atomic_json_write(Path(args.plan_output), _plan_file_payload(plan))
        print(plan_json, end="")
        if not args.apply:
            return 0
        result = apply_installer_migration(plan, expected_plan_sha256=reviewed_plan_sha256)
        result_json = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.result_output:
            _atomic_json_write(Path(args.result_output), result)
        print(result_json, end="")
        return 0
    except (MigrationError, OSError, ValueError) as exc:
        print(json.dumps({"schema_version": ENTRYPOINT_SCHEMA_VERSION, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ENTRYPOINT_SCHEMA_VERSION",
    "InstallerMigrationPlan",
    "InstallerMigrationRequest",
    "apply_installer_migration",
    "build_installer_migration_plan",
    "main",
    "plan_sha256",
]
