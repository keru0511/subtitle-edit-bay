from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import subprocess
import time
from typing import Any, Iterable
from uuid import uuid4

from .process_utils import hidden_subprocess_kwargs


AUDIO_PREVIEW_CACHE_VERSION = 2
MAX_CACHE_SIZE_BYTES = 2 * 1024 * 1024 * 1024
MAX_CACHE_AGE_SECONDS = 30 * 24 * 60 * 60


def _cache_version_prefix() -> str:
    return f"v{AUDIO_PREVIEW_CACHE_VERSION}-"


@dataclass(frozen=True)
class AudioPreviewCacheEntry:
    channel_id: str
    source_path: Path
    selector: str
    output_path: Path


@dataclass(frozen=True)
class AudioPreviewCacheResult:
    paths: dict[str, str]
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class AudioPreviewCacheStats:
    file_count: int
    total_bytes: int
    max_bytes: int
    max_age_seconds: int


def _safe_stat(path: Path) -> tuple[int, float]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime


def _source_cache_key(source_path: Path, selector: str) -> str:
    resolved = source_path.resolve()
    try:
        stat = resolved.stat()
        fingerprint = f"{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        fingerprint = "missing"
    payload = (
        f"{AUDIO_PREVIEW_CACHE_VERSION}\0{str(resolved).casefold()}\0"
        f"{fingerprint}\0{selector}"
        )
    return sha256(payload.encode("utf-8")).hexdigest()[:24]


def _cache_filename(channel_kind: str, cache_key: str) -> str:
    return f"{_cache_version_prefix()}{channel_kind}-{cache_key}.mka"


def audio_preview_cache_entries(
    project: dict[str, Any],
    cache_root: str | Path,
) -> list[AudioPreviewCacheEntry]:
    video_value = str(project.get("video", {}).get("path", "")).strip()
    root = Path(cache_root)
    entries: list[AudioPreviewCacheEntry] = []
    channels = project.get("audio_mix", {}).get("channels", [])
    for channel in channels:
        if not isinstance(channel, dict) or not str(channel.get("id", "")).strip():
            continue
        is_external = channel.get("kind") == "external"
        source_value = (
            str(channel.get("path", "")).strip()
            if is_external
            else video_value
        )
        if not source_value:
            continue
        source_path = Path(source_value)
        selector = (
            "0:a:0"
            if is_external
            else str(channel.get("selector") or "0:a:0")
        )
        cache_key = _source_cache_key(source_path, selector)
        kind = "external" if is_external else "video"
        entries.append(
            AudioPreviewCacheEntry(
                channel_id=str(channel["id"]),
                source_path=source_path.resolve(),
                selector=selector,
                output_path=root / _cache_filename(kind, cache_key),
            )
        )
    return entries


def _valid_cache_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def cached_audio_preview_paths(
    entries: Iterable[AudioPreviewCacheEntry],
) -> dict[str, str]:
    output_paths: dict[str, str] = {}
    for entry in entries:
        if not _valid_cache_file(entry.output_path):
            continue
        output_path = entry.output_path.resolve()
        try:
            output_path.touch()
        except OSError:
            pass
        output_paths[entry.channel_id] = str(output_path)
    return output_paths


def prune_audio_preview_cache(
    cache_root: str | Path,
    *,
    protected_paths: Iterable[Path] | None = None,
    max_bytes: int = MAX_CACHE_SIZE_BYTES,
    max_age_seconds: int = MAX_CACHE_AGE_SECONDS,
) -> tuple[int, int]:
    root = Path(cache_root)
    if not root.is_dir():
        return 0, 0

    protected = {Path(path).resolve() for path in (protected_paths or ())}
    current_files: list[tuple[int, float, Path]] = []
    prefix = _cache_version_prefix()
    now = time.time()
    removed_files = 0
    removed_bytes = 0

    def _remove(path: Path) -> None:
        nonlocal removed_files, removed_bytes
        try:
            removed_bytes += path.stat().st_size
            path.unlink()
            removed_files += 1
        except OSError:
            pass

    for path in root.glob("*.mka"):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved in protected:
            continue
        try:
            size, mtime = _safe_stat(resolved)
        except OSError:
            continue
        if not path.name.startswith(prefix):
            _remove(resolved)
            continue
        if max_age_seconds > 0 and now - mtime > max_age_seconds:
            _remove(resolved)
            continue
        current_files.append((int(size), mtime, resolved))

    current_total = sum(size for size, _mtime, _path in current_files)
    current_files.sort(key=lambda item: item[1])
    if max_bytes == 0:
        for size, _mtime, path in current_files:
            _remove(path)
            current_total -= size
    elif max_bytes > 0 and current_total > max_bytes:
        for size, _mtime, path in current_files:
            if current_total <= max_bytes:
                break
            _remove(path)
            current_total -= size

    return removed_files, removed_bytes


def clear_audio_preview_cache(
    cache_root: str | Path,
    *,
    protected_paths: Iterable[Path] | None = None,
) -> tuple[int, int]:
    return prune_audio_preview_cache(
        cache_root,
        protected_paths=protected_paths,
        max_bytes=0,
        max_age_seconds=0,
    )


def audio_preview_cache_stats(
    cache_root: str | Path,
) -> AudioPreviewCacheStats:
    root = Path(cache_root)
    file_count = 0
    total = 0
    if root.is_dir():
        for path in root.glob("*.mka"):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size <= 0:
                continue
            file_count += 1
            total += size
    return AudioPreviewCacheStats(
        file_count=file_count,
        total_bytes=total,
        max_bytes=MAX_CACHE_SIZE_BYTES,
        max_age_seconds=MAX_CACHE_AGE_SECONDS,
    )


def _run_cache_group(entries: list[AudioPreviewCacheEntry]) -> str | None:
    source_path = entries[0].source_path
    if not source_path.is_file():
        return f"{source_path.name}: source file was not found"

    unique_entries = list(
        {
            entry.output_path: entry
            for entry in entries
            if not _valid_cache_file(entry.output_path)
        }.values()
    )
    if not unique_entries:
        return None

    temporary_paths: list[Path] = []
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-y",
        "-fflags",
        "+genpts",
        "-i",
        str(source_path),
    ]
    for entry in unique_entries:
        entry.output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = entry.output_path.with_name(
            f".{entry.output_path.stem}.{uuid4().hex}.tmp.mka"
        )
        temporary_paths.append(temporary_path)
        command.extend(
            [
                "-map",
                entry.selector,
                "-vn",
                "-c:a",
                "flac",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-compression_level",
                "5",
                "-map_metadata",
                "-1",
                str(temporary_path),
            ]
        )

    try:
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            **hidden_subprocess_kwargs(),
        )
        for temporary_path, entry in zip(temporary_paths, unique_entries):
            if not _valid_cache_file(temporary_path):
                raise OSError(f"FFmpeg did not create {entry.output_path.name}")
            temporary_path.replace(entry.output_path)
    except (OSError, subprocess.SubprocessError) as error:
        stderr = str(getattr(error, "stderr", "") or "").strip()
        detail = stderr.splitlines()[-1] if stderr else str(error)
        return f"{source_path.name}: {detail}"
    finally:
        for temporary_path in temporary_paths:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
    return None


def prepare_audio_preview_cache(
    project: dict[str, Any],
    cache_root: str | Path,
    protected_paths: Iterable[Path] | None = None,
) -> AudioPreviewCacheResult:
    entries = audio_preview_cache_entries(project, cache_root)
    required_paths = {entry.output_path for entry in entries}
    if protected_paths is not None:
        required_paths.update(Path(path).resolve() for path in protected_paths)
    prune_audio_preview_cache(cache_root, protected_paths=required_paths)
    groups: dict[Path, list[AudioPreviewCacheEntry]] = {}
    for entry in entries:
        if not _valid_cache_file(entry.output_path):
            groups.setdefault(entry.source_path, []).append(entry)

    errors = [
        error
        for group in groups.values()
        if (error := _run_cache_group(group)) is not None
    ]
    prune_audio_preview_cache(
        cache_root,
        protected_paths=required_paths,
    )
    return AudioPreviewCacheResult(
        paths=cached_audio_preview_paths(entries),
        errors=tuple(errors),
    )

