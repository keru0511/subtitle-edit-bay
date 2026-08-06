from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import subprocess
from typing import Any, Iterable
from uuid import uuid4


AUDIO_PREVIEW_CACHE_VERSION = 2


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
                output_path=root / f"{kind}-{cache_key}.mka",
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
    return {
        entry.channel_id: str(entry.output_path.resolve())
        for entry in entries
        if _valid_cache_file(entry.output_path)
    }


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

    run_options: dict[str, Any] = {}
    if os.name == "nt":
        run_options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            **run_options,
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
) -> AudioPreviewCacheResult:
    entries = audio_preview_cache_entries(project, cache_root)
    groups: dict[Path, list[AudioPreviewCacheEntry]] = {}
    for entry in entries:
        if not _valid_cache_file(entry.output_path):
            groups.setdefault(entry.source_path, []).append(entry)

    errors = [
        error
        for group in groups.values()
        if (error := _run_cache_group(group)) is not None
    ]
    return AudioPreviewCacheResult(
        paths=cached_audio_preview_paths(entries),
        errors=tuple(errors),
    )

