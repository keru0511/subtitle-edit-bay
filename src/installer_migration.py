from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .color_config import normalize_rgb_color
from .runtime_config_schema import validate_runtime_config_payload
from .transcription_context import TranscriptionContextError, normalize_transcription_context
from .transcription_dictionary import TranscriptionDictionaryError, load_transcription_dictionary


MIGRATION_SCHEMA_VERSION = 1
LEGACY_MARKERS = ("setup.bat", "start.bat", "src")
PROJECT_DIRECTORIES = ("video_import", "video_export", "out")
PROJECT_FILE_SUFFIXES = (".seb-project.json", ".subtitle-project.json")
SECRET_KEY_PARTS = ("api_key", "apikey", "password", "secret", "token")


class MigrationError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimeCapabilities:
    cuda: bool
    nvenc: bool


@dataclass(frozen=True)
class MigrationOptions:
    runtime_config: bool = True
    speaker_colors: bool = True
    workspace_reference: bool = True
    overwrite: bool = False


@dataclass(frozen=True)
class MigrationResult:
    schema_version: int
    source: str
    destination: str
    runtime_reused: bool
    copied: tuple[str, ...]
    preserved: tuple[str, ...]
    adjusted: tuple[str, ...]
    workspace_references: tuple[str, ...]
    cleanup_blockers: tuple[str, ...]
    reclaimable_venv_bytes: int
    completed_at: str


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def validate_legacy_workspace(source: str | Path, destination: str | Path) -> Path:
    source_path = _resolved(source)
    destination_path = _resolved(destination)
    if source_path == destination_path:
        raise MigrationError("migration source and destination must be different")
    if not source_path.is_dir():
        raise MigrationError(f"legacy workspace does not exist: {source_path}")
    missing = [marker for marker in LEGACY_MARKERS if not (source_path / marker).exists()]
    if missing:
        raise MigrationError(f"not a Subtitle Edit Bay BAT/ZIP workspace; missing: {', '.join(missing)}")
    return source_path


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationError(f"invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise MigrationError(f"JSON root must be an object: {path}")
    return payload


def _contains_secret(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if any(part in normalized for part in SECRET_KEY_PARTS):
                return True
            if _contains_secret(child):
                return True
    elif isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return False


def validated_runtime_config(
    source_path: str | Path,
    capabilities: RuntimeCapabilities,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    path = Path(source_path)
    if path.is_symlink():
        raise MigrationError(f"runtime config must not be a symbolic link: {path}")
    source = _load_object(path)
    if _contains_secret(source):
        raise MigrationError("runtime config contains a secret-like key and cannot be migrated")
    try:
        migrated = validate_runtime_config_payload(source, discard_unknown=True)
    except ValueError as exc:
        raise MigrationError(str(exc)) from exc
    adjusted: list[str] = []

    old_craig = source.get("craig_pipeline")
    if isinstance(old_craig, dict) and "transcription_context" in old_craig:
        try:
            context = normalize_transcription_context(old_craig["transcription_context"])
        except TranscriptionContextError as exc:
            raise MigrationError("invalid transcription context in runtime config") from exc
        dictionary_value = context.get("dictionary_path")
        if isinstance(dictionary_value, str) and dictionary_value:
            dictionary_path = Path(dictionary_value).expanduser()
            if not dictionary_path.is_absolute():
                dictionary_path = path.parent.parent / dictionary_path
            dictionary_path = dictionary_path.resolve()
            if context.get("dictionary_confirmed"):
                try:
                    load_transcription_dictionary(dictionary_path)
                except (OSError, TranscriptionDictionaryError) as exc:
                    raise MigrationError(f"confirmed transcription dictionary is invalid: {dictionary_path}") from exc
            context["dictionary_path"] = str(dictionary_path)
        migrated.setdefault("craig_pipeline", {})["transcription_context"] = context

    shared = migrated.get("shared")
    if isinstance(shared, dict) and not capabilities.cuda and shared.get("device") == "cuda":
        shared["device"] = "cpu"
        shared["compute_type"] = "int8"
        adjusted.append("shared.device=cuda -> cpu/int8")
    if not capabilities.nvenc:
        for section_name, section in migrated.items():
            if isinstance(section, dict) and section.get("video_codec") == "h264_nvenc":
                section["video_codec"] = "libx264"
                adjusted.append(f"{section_name}.video_codec=h264_nvenc -> libx264")
    return migrated, tuple(adjusted)


def validated_speaker_colors(source_path: str | Path) -> dict[str, Any]:
    path = Path(source_path)
    if path.is_symlink():
        raise MigrationError(f"speaker color config must not be a symbolic link: {path}")
    payload = _load_object(path)
    for section_name in ("speakers", "files"):
        section = payload.get(section_name, {})
        if not isinstance(section, dict):
            raise MigrationError(f"speaker color section must be an object: {section_name}")
        for name, entry in section.items():
            if not isinstance(name, str) or not name.strip():
                raise MigrationError(f"speaker color name must be a non-empty string: {section_name}")
            color: object
            if isinstance(entry, str):
                color = entry
            elif isinstance(entry, dict):
                color = entry.get("color")
                aliases = entry.get("aliases", [])
                if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
                    raise MigrationError(f"speaker color aliases must be strings: {section_name}.{name}")
            else:
                raise MigrationError(f"speaker color entry must be a string or object: {section_name}.{name}")
            try:
                normalize_rgb_color(color)
            except ValueError as exc:
                raise MigrationError(f"invalid speaker color: {section_name}.{name}") from exc
    return payload


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.restore.tmp")
    try:
        temporary.write_bytes(payload)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _directory_size(path: Path) -> int:
    total = 0
    if not path.is_dir():
        return total
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _workspace_references(source: Path) -> tuple[str, ...]:
    references: list[str] = []
    for directory in PROJECT_DIRECTORIES:
        candidate = source / directory
        if candidate.is_dir() and any(candidate.iterdir()):
            references.append(str(candidate))
    for item in source.iterdir():
        if item.is_file() and item.name.endswith(PROJECT_FILE_SUFFIXES):
            references.append(str(item))
    return tuple(sorted(references))


def _normalized_path_key(value: str | Path) -> str:
    return str(_resolved(value)).casefold()


def _merged_workspace_registry(path: Path, source: Path, references: Sequence[str]) -> dict[str, Any]:
    workspaces: dict[str, dict[str, Any]] = {}
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise MigrationError(f"legacy workspace registry must be a regular file: {path}")
        payload = _load_object(path)
        if payload.get("schema_version") != MIGRATION_SCHEMA_VERSION:
            raise MigrationError(f"unsupported legacy workspace registry schema: {path}")
        entries = payload.get("workspaces")
        if not isinstance(entries, list):
            raise MigrationError(f"legacy workspace registry must contain an array: {path}")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise MigrationError(f"invalid legacy workspace entry: {path}")
            resources = entry.get("resources")
            if not isinstance(resources, list) or not all(isinstance(item, str) for item in resources):
                raise MigrationError(f"invalid legacy workspace resources: {path}")
            normalized_path = str(_resolved(entry["path"]))
            workspaces[_normalized_path_key(normalized_path)] = {
                "path": normalized_path,
                "resources": sorted({str(_resolved(item)) for item in resources}),
            }
    normalized_source = str(source)
    workspaces[_normalized_path_key(normalized_source)] = {
        "path": normalized_source,
        "resources": sorted({str(_resolved(item)) for item in references}),
    }
    return {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "workspaces": [workspaces[key] for key in sorted(workspaces)],
    }


def _snapshot_targets(paths: Sequence[Path]) -> tuple[dict[Path, bytes | None], tuple[Path, ...]]:
    snapshots: dict[Path, bytes | None] = {}
    parent_candidates: set[Path] = set()
    for path in paths:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise MigrationError(f"migration target must be a regular file: {path}")
        snapshots[path] = path.read_bytes() if path.exists() else None
        parent = path.parent
        while not parent.exists():
            parent_candidates.add(parent)
            parent = parent.parent
    return snapshots, tuple(sorted(parent_candidates, key=lambda item: len(item.parts), reverse=True))


def _restore_targets(snapshots: Mapping[Path, bytes | None], created_parents: Sequence[Path]) -> None:
    failures: list[str] = []
    for path, original in reversed(tuple(snapshots.items())):
        try:
            if original is None:
                path.unlink(missing_ok=True)
            else:
                _write_bytes_atomic(path, original)
        except OSError as exc:
            failures.append(f"{path}: {exc}")
    for parent in created_parents:
        try:
            parent.rmdir()
        except OSError:
            pass
    if failures:
        raise MigrationError("migration rollback failed: " + "; ".join(failures))


def _cleanup_blockers(source: Path, references: Sequence[str]) -> tuple[str, ...]:
    blockers = list(references)
    for name in (".gui", "assets"):
        path = source / name
        if path.exists():
            blockers.append(str(path))
    return tuple(sorted(set(blockers)))


def migrate_legacy_workspace(
    source: str | Path,
    destination: str | Path,
    *,
    capabilities: RuntimeCapabilities,
    options: MigrationOptions = MigrationOptions(),
    now: datetime | None = None,
) -> MigrationResult:
    destination_path = _resolved(destination)
    source_path = validate_legacy_workspace(source, destination_path)
    copied: list[str] = []
    preserved: list[str] = []
    adjusted: list[str] = []

    old_config = source_path / ".gui" / "runtime_config.json"
    new_config = destination_path / ".gui" / "runtime_config.json"
    prepared_config: dict[str, Any] | None = None
    if options.runtime_config and old_config.is_file() and not old_config.is_symlink():
        if new_config.exists() and not options.overwrite:
            preserved.append(str(new_config))
        else:
            prepared_config, changes = validated_runtime_config(old_config, capabilities)
            adjusted.extend(changes)
    elif options.runtime_config and old_config.is_symlink():
        raise MigrationError(f"runtime config must not be a symbolic link: {old_config}")

    old_colors = source_path / "assets" / "speaker_colors.json"
    new_colors = destination_path / "assets" / "speaker_colors.json"
    prepared_colors: dict[str, Any] | None = None
    if options.speaker_colors and old_colors.is_file():
        if new_colors.exists() and not options.overwrite:
            preserved.append(str(new_colors))
        else:
            prepared_colors = validated_speaker_colors(old_colors)

    # Complete all reads, validation, enumeration and merge preparation before
    # changing the destination. The writes below form one rollback boundary.
    references = _workspace_references(source_path) if options.workspace_reference else ()
    workspace_path = destination_path / ".gui" / "legacy_workspaces.json"
    workspace_payload: dict[str, Any] | None = None
    if references:
        workspace_payload = _merged_workspace_registry(workspace_path, source_path, references)

    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    result = MigrationResult(
        schema_version=MIGRATION_SCHEMA_VERSION,
        source=str(source_path),
        destination=str(destination_path),
        runtime_reused=False,
        copied=tuple(copied),
        preserved=tuple(preserved),
        adjusted=tuple(adjusted),
        workspace_references=references,
        cleanup_blockers=_cleanup_blockers(source_path, references),
        reclaimable_venv_bytes=_directory_size(source_path / ".venv"),
        completed_at=timestamp,
    )
    record_name = timestamp.replace(":", "").replace("-", "")
    record_path = destination_path / ".local" / "migration" / f"migration-{record_name}.json"
    writes: list[tuple[Path, object]] = []
    if prepared_config is not None:
        writes.append((new_config, prepared_config))
        copied.append(str(new_config))
    if prepared_colors is not None:
        writes.append((new_colors, prepared_colors))
        copied.append(str(new_colors))
    if workspace_payload is not None:
        writes.append((workspace_path, workspace_payload))
        copied.append(str(workspace_path))
    result = MigrationResult(**{**asdict(result), "copied": tuple(copied)})
    writes.append((record_path, asdict(result)))
    snapshots, created_parents = _snapshot_targets([path for path, _payload in writes])
    try:
        for path, payload in writes:
            _write_json_atomic(path, payload)
    except Exception as exc:
        try:
            _restore_targets(snapshots, created_parents)
        except MigrationError as rollback_exc:
            raise MigrationError(f"migration failed and rollback was incomplete: {rollback_exc}") from exc
        raise MigrationError(f"migration changes were rolled back: {exc}") from exc
    return result


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate a BAT/ZIP workspace into an Installer installation.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--cuda", action="store_true")
    parser.add_argument("--nvenc", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--skip-runtime-config", action="store_true")
    parser.add_argument("--skip-speaker-colors", action="store_true")
    parser.add_argument("--skip-workspace-reference", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = migrate_legacy_workspace(
            args.source,
            args.destination,
            capabilities=RuntimeCapabilities(cuda=args.cuda, nvenc=args.nvenc),
            options=MigrationOptions(
                runtime_config=not args.skip_runtime_config,
                speaker_colors=not args.skip_speaker_colors,
                workspace_reference=not args.skip_workspace_reference,
                overwrite=args.overwrite,
            ),
        )
    except MigrationError as exc:
        print(f"Migration failed: {exc}")
        return 2
    print(json.dumps(asdict(result), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
