from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .color_config import normalize_rgb_color
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


def _validate_like_template(value: object, template: object, path: str) -> object:
    if isinstance(template, dict):
        if not isinstance(value, dict):
            raise MigrationError(f"runtime config value must be an object: {path}")
        result: dict[str, object] = {}
        for key, child in value.items():
            if key not in template:
                continue
            result[key] = _validate_like_template(child, template[key], f"{path}.{key}")
        return result
    if isinstance(template, bool):
        if not isinstance(value, bool):
            raise MigrationError(f"runtime config value must be boolean: {path}")
    elif isinstance(template, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise MigrationError(f"runtime config value must be integer: {path}")
    elif isinstance(template, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MigrationError(f"runtime config value must be numeric: {path}")
    elif isinstance(template, str) and not isinstance(value, str):
        raise MigrationError(f"runtime config value must be string: {path}")
    elif isinstance(template, list) and not isinstance(value, list):
        raise MigrationError(f"runtime config value must be an array: {path}")
    return value


def validated_runtime_config(
    source_path: str | Path,
    template_path: str | Path,
    capabilities: RuntimeCapabilities,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    path = Path(source_path)
    if path.is_symlink():
        raise MigrationError(f"runtime config must not be a symbolic link: {path}")
    source = _load_object(path)
    if _contains_secret(source):
        raise MigrationError("runtime config contains a secret-like key and cannot be migrated")
    template = _load_object(Path(template_path))
    migrated = _validate_like_template(source, template, "runtime_config")
    assert isinstance(migrated, dict)
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
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


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
            prepared_config, changes = validated_runtime_config(
                old_config,
                destination_path / "assets" / "runtime_config.json",
                capabilities,
            )
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

    # Validate every selected input before committing any destination change.
    if prepared_config is not None:
        _write_json_atomic(new_config, prepared_config)
        copied.append(str(new_config))
    if prepared_colors is not None:
        _write_json_atomic(new_colors, prepared_colors)
        copied.append(str(new_colors))

    references = _workspace_references(source_path) if options.workspace_reference else ()
    if references:
        _write_json_atomic(
            destination_path / ".gui" / "legacy_workspaces.json",
            {
                "schema_version": MIGRATION_SCHEMA_VERSION,
                "workspaces": [{"path": str(source_path), "resources": list(references)}],
            },
        )
        copied.append(str(destination_path / ".gui" / "legacy_workspaces.json"))

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
    _write_json_atomic(destination_path / ".local" / "migration" / f"migration-{record_name}.json", asdict(result))
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
