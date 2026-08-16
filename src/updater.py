from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .application_info import normalize_version, resolve_application_version

GITHUB_API_HOST = "api.github.com"
DEFAULT_OWNER = "keru0511"
DEFAULT_REPO = "subtitle-edit-bay"


class UpdaterError(Exception):
    """Raised when the update check or update application fails."""


@dataclass
class UpdateInfo:
    current_version: str
    latest_version: str
    release_notes: str
    download_url: str
    tag_name: str
    available: bool


def _version_tuple(value: str) -> tuple[int, ...]:
    stripped = value.lstrip("v")
    parts = stripped.split(".") if stripped else []
    numeric: list[int] = []
    for part in parts:
        digits = "".join(ch for ch in part if ch.isdigit())
        if digits:
            numeric.append(int(digits))
    if not numeric:
        return (0,)
    return tuple(numeric)


def is_newer_version(current: str, latest: str) -> bool:
    """Return True when *latest* is a newer release than *current*."""
    current_norm = normalize_version(current)
    latest_norm = normalize_version(latest)
    if latest_norm == "development" or current_norm == "development":
        return latest_norm != current_norm and latest_norm != "development"
    try:
        return _version_tuple(latest_norm) > _version_tuple(current_norm)
    except (ValueError, TypeError):
        return latest_norm != current_norm


def fetch_latest_release(
    project_root: Path,
    owner: str = DEFAULT_OWNER,
    repo: str = DEFAULT_REPO,
    timeout: float = 15.0,
) -> UpdateInfo:
    """Fetch the latest GitHub release for this application."""
    current = resolve_application_version(project_root)
    url = f"https://{GITHUB_API_HOST}/repos/{owner}/{repo}/releases/latest"
    request = urllib.request.Request(url, headers={"User-Agent": f"{repo}-updater"})

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise UpdaterError(f"リリース情報を取得できません: {error}") from error

    tag = str(data.get("tag_name", "")).strip()
    if not tag:
        raise UpdaterError("リリースタグが見つかりません")

    latest = normalize_version(tag)
    body = str(data.get("body", "") or "")
    download_url = ""
    for asset in data.get("assets") or []:
        name = str(asset.get("name", ""))
        if name.endswith(".zip"):
            download_url = str(asset.get("browser_download_url", ""))
            break
    if not download_url:
        download_url = f"https://github.com/{owner}/{repo}/archive/refs/tags/{tag}.zip"

    return UpdateInfo(
        current_version=current,
        latest_version=latest,
        release_notes=body,
        download_url=download_url,
        tag_name=tag,
        available=is_newer_version(current, latest),
    )


def _preserved_top_level_items() -> set[str]:
    return {
        ".git",
        ".venv",
        ".local",
        ".gui",
        "video_import",
        "video_export",
        "out",
        "__pycache__",
    }


def _is_preservable(relative: Path) -> bool:
    top = relative.parts[0] if relative.parts else ""
    if top in _preserved_top_level_items():
        return True
    preserved_files = {"assets/speaker_colors.json"}
    return str(relative).replace("\\", "/") in preserved_files


def _load_manifest(project_root: Path) -> set[str]:
    manifest_path = project_root / ".local" / "update-manifest.json"
    if not manifest_path.is_file():
        return set()
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if isinstance(data, list):
        return {str(entry) for entry in data}
    if isinstance(data, dict) and isinstance(data.get("files"), list):
        return {str(entry) for entry in data["files"]}
    return set()


def _write_manifest(project_root: Path, files: set[str]) -> None:
    manifest_path = project_root / ".local" / "update-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(sorted(files), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _backup_file(source: Path, backup_root: Path, relative: Path) -> Path:
    destination = backup_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def apply_zip_update(
    project_root: Path,
    archive_url: str,
    timeout: float = 120.0,
) -> tuple[Path, set[str]]:
    """Download *archive_url* and install it over *project_root*.

    Returns the backup root path and the set of installed relative file paths.
    Raises UpdaterError on failure after restoring the previous state.
    """
    import tempfile

    project_root = project_root.resolve()
    current_version = resolve_application_version(project_root)
    previous_manifest = _load_manifest(project_root)
    backup_root = project_root / ".local" / "update_backups" / "pending"
    if backup_root.exists():
        shutil.rmtree(backup_root)
    backup_root.mkdir(parents=True, exist_ok=True)

    updated: dict[Path, Path] = {}
    added: set[Path] = set()
    removed: dict[Path, Path] = {}
    temp_root = Path(tempfile.mkdtemp(prefix="subtitle-edit-bay-update-"))

    try:
        try:
            if Path(archive_url).is_file():
                zip_path = Path(archive_url)
            else:
                zip_path = temp_root / "latest.zip"
                request = urllib.request.Request(archive_url, headers={"User-Agent": "subtitle-edit-bay-updater"})
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    zip_path.write_bytes(response.read())

            extract_root = temp_root / "extracted"
            extract_root.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as handle:
                handle.extractall(extract_root)

            source_candidates = [entry for entry in extract_root.iterdir() if entry.is_dir()]
            if not source_candidates:
                raise UpdaterError("アーカイブにディレクトリが含まれていません")
            source_root = source_candidates[0]
            if not (source_root / "src").is_dir() or not (source_root / "scripts" / "setup.ps1").is_file():
                raise UpdaterError("アーカイブが有効な Subtitle Edit Bay 配布物ではありません")

            archive_version_path = source_root / "VERSION"
            downloaded_version = (
                normalize_version(archive_version_path.read_text(encoding="utf-8").strip())
                if archive_version_path.is_file()
                else "development"
            )

            source_files: dict[Path, Path] = {}
            for file in source_root.rglob("*"):
                if not file.is_file():
                    continue
                relative = file.relative_to(source_root)
                if _is_preservable(relative):
                    continue
                source_files[relative] = file

            target_files: dict[Path, Path] = {}
            for relative_str in previous_manifest:
                relative = Path(relative_str.replace("/", "\\"))
                destination = project_root / relative
                if destination.is_file() and not _is_preservable(relative):
                    target_files[relative] = destination

            if (project_root / ".local" / "update-manifest.json").is_file():
                manifest_relative = Path(".local") / "update-manifest.json"
                _backup_file(
                    project_root / manifest_relative,
                    backup_root,
                    manifest_relative,
                )
                updated[manifest_relative] = project_root / manifest_relative
            else:
                added.add(project_root / ".local" / "update-manifest.json")

            for relative, source in source_files.items():
                destination = project_root / relative
                if destination.is_file():
                    _backup_file(destination, backup_root, relative)
                    updated[relative] = destination
                else:
                    added.add(destination)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

            for relative, destination in target_files.items():
                if relative not in source_files:
                    _backup_file(destination, backup_root, relative)
                    destination.unlink()
                    removed[relative] = destination

            version_path = project_root / "VERSION"
            if not (source_root / "VERSION").is_file():
                if version_path.is_file():
                    _backup_file(version_path, backup_root, Path("VERSION"))
                    updated[Path("VERSION")] = version_path
                else:
                    added.add(version_path)
                version_path.write_text(f"{downloaded_version}\n", encoding="utf-8")

            if not source_files:
                raise UpdaterError("アーカイブに更新ファイルが含まれていません")

            setup_script = project_root / "scripts" / "setup.ps1"
            if not setup_script.is_file():
                raise UpdaterError("setup.ps1 が見つかりません")

            _run_setup(setup_script)

            post_version = resolve_application_version(project_root)
            if post_version != downloaded_version:
                raise UpdaterError(f"更新後のバージョンが期待値と一致しません: {post_version}")

            installed = set(str(relative).replace("\\", "/") for relative in source_files.keys())
            _write_manifest(project_root, installed)
            return backup_root, installed

        except Exception as error:
            _restore_update_state(project_root, backup_root, updated, added, removed)
            if isinstance(error, UpdaterError):
                raise
            raise UpdaterError(f"更新に失敗しました: {error}") from error

    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def _run_setup(setup_script: Path) -> None:
    """Run the setup script to refresh dependencies after an update."""
    if sys.platform == "win32" and shutil.which("powershell.exe"):
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(setup_script),
        ]
    else:
        command = [sys.executable, str(setup_script)]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise UpdaterError(
            f"セットアップが失敗しました（終了コード {result.returncode}）:\n{result.stdout}\n{result.stderr}"
        )


def _restore_update_state(
    project_root: Path,
    backup_root: Path,
    updated: dict[Path, Path],
    added: set[Path],
    removed: dict[Path, Path],
) -> None:
    """Roll back files to the state captured in *backup_root*."""
    for relative, destination in removed.items():
        backup = backup_root / relative
        if backup.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, destination)

    for relative, destination in updated.items():
        backup = backup_root / relative
        if backup.is_file():
            shutil.copy2(backup, destination)

    for path in added:
        if path.is_file():
            path.unlink()


def launch_update_script(project_root: Path, archive_url: str | None = None) -> list[str]:
    """Build the command to launch the platform update script."""
    update_script = project_root / "scripts" / "update.ps1"
    if sys.platform == "win32" and shutil.which("powershell.exe"):
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(update_script),
        ]
        if archive_url:
            command.extend(["-ArchiveUrl", archive_url])
        return command
    return [sys.executable, "-m", "src.updater", "apply", "--archive-url", archive_url or ""]


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Subtitle Edit Bay updater")
    subparsers = parser.add_subparsers(dest="command")
    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--archive-url", required=True)
    apply_parser.add_argument("--project-root", default=str(Path.cwd()))
    args = parser.parse_args(argv)
    if args.command == "apply":
        try:
            apply_zip_update(Path(args.project_root), args.archive_url)
        except UpdaterError as error:
            print(f"Update failed: {error}", file=sys.stderr)
            return 1
        print("Update complete")
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
