"""Safe download, verification, and handoff primitives for GUI updates."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


class UpdatePackageError(RuntimeError):
    """Raised when a package cannot be downloaded or verified."""


class UpdateDownloadCancelled(UpdatePackageError):
    """Raised when the user cancels an in-progress package download."""


ProgressCallback = Callable[[int, int, float], None]


def update_download_directory(project_root: Path) -> Path:
    """Return a user-data directory outside the installed application."""

    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or tempfile.gettempdir()
    return Path(base) / "SubtitleEditBay" / "updates"


def _info_value(info: Any, name: str, default: Any = None) -> Any:
    if isinstance(info, Mapping):
        return info.get(name, default)
    return getattr(info, name, default)


def _checksum_from_text(value: str) -> str:
    match = re.search(r"\b([0-9a-fA-F]{64})\b", value)
    if not match:
        raise UpdatePackageError("checksum file does not contain a SHA-256 value")
    return match.group(1).lower()


def resolve_expected_sha256(info: Any, *, timeout: float = 30.0) -> str:
    expected = str(_info_value(info, "sha256", "") or "").strip().lower()
    if expected:
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise UpdatePackageError("release checksum is not a SHA-256 value")
        return expected
    checksum_url = str(_info_value(info, "checksum_url", "") or "").strip()
    if not checksum_url:
        raise UpdatePackageError("release does not provide a package checksum")
    request = urllib.request.Request(checksum_url, headers={"User-Agent": "subtitle-edit-bay-updater"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return _checksum_from_text(response.read().decode("utf-8", errors="replace"))
    except OSError as error:
        raise UpdatePackageError(f"checksumを取得できません: {error}") from error


def _sha256(path: Path, *, cancel_event: Any = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise UpdateDownloadCancelled("ダウンロードをキャンセルしました")
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _validate_zip_layout(path: Path, expected_version: str = "") -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if not names:
                raise UpdatePackageError("package is empty")
            top_levels: set[str] = set()
            for name in names:
                candidate = Path(name)
                if candidate.is_absolute() or ".." in candidate.parts:
                    raise UpdatePackageError("package contains an unsafe path")
                info = archive.getinfo(name)
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise UpdatePackageError("package contains a symbolic link")
                if candidate.parts:
                    top_levels.add(candidate.parts[0])
            if len(top_levels) != 1:
                raise UpdatePackageError("package must contain one distribution root")
            root = next(iter(top_levels))
            version_name = f"{root}/VERSION"
            if version_name in names and expected_version:
                version = archive.read(version_name).decode("utf-8").strip().lstrip("v")
                if version != expected_version.lstrip("v"):
                    raise UpdatePackageError(f"package version mismatch: {version}")
    except zipfile.BadZipFile as error:
        raise UpdatePackageError(f"package ZIPを読み込めません: {error}") from error


def validate_package(
    package_path: Path,
    *,
    expected_sha256: str,
    expected_size: int = 0,
    expected_version: str = "",
) -> None:
    if not package_path.is_file():
        raise UpdatePackageError("downloaded package is missing")
    if expected_size and package_path.stat().st_size != expected_size:
        raise UpdatePackageError("package size does not match the release metadata")
    actual = _sha256(package_path)
    if actual.lower() != expected_sha256.lower():
        raise UpdatePackageError("package SHA-256 does not match the release checksum")
    package_name = package_path.name.lower()
    if package_name.endswith((".zip", ".zip.partial")):
        _validate_zip_layout(package_path, expected_version)
    elif package_name.endswith((".exe", ".exe.partial")):
        with package_path.open("rb") as handle:
            if handle.read(2) != b"MZ":
                raise UpdatePackageError("installer package is not a Windows executable")
    else:
        raise UpdatePackageError("unsupported update package type")


def download_package(
    info: Any,
    destination: Path,
    *,
    cancel_event: Any = None,
    progress_callback: ProgressCallback | None = None,
    timeout: float = 120.0,
) -> Path:
    """Download and verify a release package, keeping partial data separate."""

    url = str(_info_value(info, "download_url", "") or "").strip()
    if not url:
        raise UpdatePackageError("release package URL is empty")
    expected_sha256 = resolve_expected_sha256(info, timeout=timeout)
    expected_size = int(_info_value(info, "package_size", 0) or 0)
    expected_version = str(_info_value(info, "latest_version", "") or "")
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    partial.unlink(missing_ok=True)
    started = time.monotonic()
    downloaded = 0
    response = None
    try:
        if Path(url).is_file():
            source = Path(url)
            total = source.stat().st_size
            with source.open("rb") as source_handle, partial.open("wb") as target:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise UpdateDownloadCancelled("ダウンロードをキャンセルしました")
                    chunk = source_handle.read(1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total, downloaded / max(time.monotonic() - started, 0.001))
        else:
            request = urllib.request.Request(url, headers={"User-Agent": "subtitle-edit-bay-updater"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                total = int(response.headers.get("Content-Length", expected_size) or expected_size or 0)
                with partial.open("wb") as target:
                    while True:
                        if cancel_event is not None and cancel_event.is_set():
                            raise UpdateDownloadCancelled("ダウンロードをキャンセルしました")
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        target.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            progress_callback(downloaded, total, downloaded / max(time.monotonic() - started, 0.001))
    except UpdateDownloadCancelled:
        partial.unlink(missing_ok=True)
        raise
    except OSError as error:
        partial.unlink(missing_ok=True)
        raise UpdatePackageError(f"packageのダウンロードに失敗しました: {error}") from error
    finally:
        del response
    try:
        validate_package(partial, expected_sha256=expected_sha256, expected_size=expected_size, expected_version=expected_version)
        os.replace(partial, destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    if progress_callback:
        progress_callback(downloaded, downloaded, downloaded / max(time.monotonic() - started, 0.001))
    return destination


def build_installer_helper_command(
    project_root: Path,
    package_path: Path,
    *,
    expected_version: str,
    expected_sha256: str,
    result_path: Path,
) -> list[str]:
    helper = project_root / "scripts" / "apply_installer_update.ps1"
    restart_executable = project_root / "SubtitleEditBayLauncher.exe"
    if not restart_executable.is_file():
        restart_executable = project_root / "SubtitleEditBay.exe"
    if sys.platform == "win32":
        powershell = "powershell.exe"
        return [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(helper),
            "-PackagePath",
            str(package_path),
            "-ParentPid",
            str(os.getpid()),
            "-InstallRoot",
            str(project_root),
            "-RestartExecutable",
            str(restart_executable),
            "-ExpectedVersion",
            expected_version,
            "-ExpectedSha256",
            expected_sha256,
            "-ResultPath",
            str(result_path),
        ]
    return [sys.executable, "-m", "src.updater", "apply", "--archive-url", str(package_path)]


def read_update_result(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "invalid", "message": "更新結果を読み込めません"}
    return result if isinstance(result, dict) else {"status": "invalid", "message": "更新結果の形式が不正です"}
