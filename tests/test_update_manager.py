from __future__ import annotations

import hashlib
import io
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src import updater
from src.update_manager import (
    UpdateDownloadCancelled,
    UpdatePackageError,
    build_installer_helper_command,
    download_package,
    validate_package,
)


def _installer(path: Path) -> str:
    path.write_bytes(b"MZ" + b"fake installer payload")
    return hashlib.sha256(path.read_bytes()).hexdigest()


class UpdateManagerTests(unittest.TestCase):
    def test_download_verifies_size_hash_and_reports_byte_progress(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source = tmp_path / "SubtitleEditBay-Setup.exe"
            digest = _installer(source)
            info = SimpleNamespace(
                download_url=str(source),
                latest_version="v1.2.3",
                package_size=source.stat().st_size,
                sha256=digest,
                checksum_url="",
            )
            progress = []
            destination = tmp_path / "cache" / source.name
            result = download_package(
                info,
                destination,
                progress_callback=lambda downloaded, total, speed: progress.append((downloaded, total, speed)),
            )
            self.assertEqual(result, destination.resolve())
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertFalse(destination.with_name(destination.name + ".partial").exists())
            self.assertEqual(progress[-1][0], source.stat().st_size)

    def test_cancel_removes_partial_package(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source = tmp_path / "SubtitleEditBay-Setup.exe"
            digest = _installer(source)
            info = SimpleNamespace(
                download_url=str(source), latest_version="v1.0.0", package_size=source.stat().st_size, sha256=digest
            )
            cancel = threading.Event()
            cancel.set()
            destination = tmp_path / "cache" / source.name
            with self.assertRaises(UpdateDownloadCancelled):
                download_package(info, destination, cancel_event=cancel)
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_name(destination.name + ".partial").exists())

    def test_hash_mismatch_and_unsafe_zip_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source = tmp_path / "SubtitleEditBay-Setup.exe"
            _installer(source)
            info = SimpleNamespace(
                download_url=str(source),
                latest_version="v1.0.0",
                package_size=source.stat().st_size,
                sha256="0" * 64,
            )
            with self.assertRaisesRegex(UpdatePackageError, "SHA-256"):
                download_package(info, tmp_path / "bad.exe")

            archive = tmp_path / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("../escape.txt", "unsafe")
            with self.assertRaisesRegex(UpdatePackageError, "unsafe path"):
                validate_package(archive, expected_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())

    def test_installer_helper_command_is_hidden_and_carries_transaction_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            with patch("src.update_manager.sys.platform", "win32"):
                command = build_installer_helper_command(
                    tmp_path,
                    tmp_path / "package.exe",
                    expected_version="v1.2.3",
                    expected_sha256="a" * 64,
                    result_path=tmp_path / "result.json",
                )
        self.assertEqual(command[0], "powershell.exe")
        self.assertIn("-WindowStyle", command)
        self.assertIn("Hidden", command)
        self.assertIn("-ParentPid", command)
        self.assertIn("-ExpectedSha256", command)
        restart_index = command.index("-RestartExecutable")
        self.assertEqual(Path(command[restart_index + 1]), tmp_path / "SubtitleEditBayLauncher.exe")
        self.assertNotIn(str(tmp_path / "SubtitleEditBay.exe"), command)

    def test_release_asset_metadata_selects_installer_and_checksum(self):
        payload = {
            "tag_name": "v1.2.3",
            "body": "notes",
            "assets": [
                {"name": "SubtitleEditBay-Setup.exe", "size": 42, "browser_download_url": "https://example.test/setup.exe"},
                {"name": "SubtitleEditBay-Setup.exe.sha256", "browser_download_url": "https://example.test/setup.exe.sha256"},
                {"name": "SubtitleEditBay-Setup.exe.manifest.json", "browser_download_url": "https://example.test/setup.exe.manifest.json"},
            ],
        }
        with patch("src.updater.resolve_application_version", return_value="v1.0.0"), patch(
            "src.updater.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(payload).encode("utf-8"))
        ):
            info = updater.fetch_latest_release(Path("."))
        self.assertEqual(info.package_type, "installer")
        self.assertEqual(info.package_size, 42)
        self.assertTrue(info.checksum_url.endswith("setup.exe.sha256"))
        self.assertTrue(info.manifest_url.endswith("setup.exe.manifest.json"))
