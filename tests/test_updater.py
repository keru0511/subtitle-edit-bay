import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from src import updater


class VersionComparisonTests(unittest.TestCase):
    def test_development_is_not_newer_than_release(self) -> None:
        self.assertFalse(updater.is_newer_version("development", "development"))
        self.assertTrue(updater.is_newer_version("development", "v0.2.0"))
        self.assertFalse(updater.is_newer_version("v0.2.0", "development"))

    def test_newer_release_reported_correctly(self) -> None:
        self.assertTrue(updater.is_newer_version("v0.1.0", "v0.2.0"))
        self.assertTrue(updater.is_newer_version("v0.1.9", "v0.2.0"))
        self.assertFalse(updater.is_newer_version("v0.2.0", "v0.1.0"))
        self.assertFalse(updater.is_newer_version("v0.2.0", "v0.2.0"))

    def test_missing_prefix_accepted(self) -> None:
        self.assertTrue(updater.is_newer_version("0.1.0", "v0.2.0"))
        self.assertTrue(updater.is_newer_version("v0.1.0", "0.2.0"))


class FetchLatestReleaseTests(unittest.TestCase):
    def test_fetch_latest_release_parses_github_response(self) -> None:
        payload = {
            "tag_name": "v0.2.0",
            "body": "Release notes",
            "assets": [
                {"name": "subtitle-edit-bay.zip", "browser_download_url": "https://example.com/app.zip"},
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "VERSION").write_text("v0.1.0\n", encoding="utf-8")

            def fake_urlopen(request, **_kwargs):
                self.assertIn("api.github.com", request.full_url)
                return BytesIO(json.dumps(payload).encode("utf-8"))

            with patch("src.updater.urllib.request.urlopen", side_effect=fake_urlopen):
                info = updater.fetch_latest_release(root)

        self.assertEqual(info.current_version, "v0.1.0")
        self.assertEqual(info.latest_version, "v0.2.0")
        self.assertEqual(info.release_notes, "Release notes")
        self.assertEqual(info.download_url, "https://example.com/app.zip")
        self.assertTrue(info.available)

    def test_fetch_latest_release_falls_back_to_archive_url(self) -> None:
        payload = {
            "tag_name": "v0.2.0",
            "body": "",
            "assets": [],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "VERSION").write_text("v0.1.0\n", encoding="utf-8")

            with patch("src.updater.urllib.request.urlopen") as urlopen:
                urlopen.return_value = BytesIO(json.dumps(payload).encode("utf-8"))
                info = updater.fetch_latest_release(root)

        self.assertIn("github.com/keru0511/subtitle-edit-bay/archive/refs/tags/v0.2.0.zip", info.download_url)

    def test_fetch_latest_release_raises_on_network_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch("src.updater.urllib.request.urlopen", side_effect=OSError("network down")):
                with self.assertRaises(updater.UpdaterError):
                    updater.fetch_latest_release(root)


class ApplyZipUpdateTests(unittest.TestCase):
    @staticmethod
    def _setup_script_content(*, fail: bool = False) -> str:
        if sys.platform == "win32" and shutil.which("powershell.exe"):
            return 'throw "setup failed"\n' if fail else "Write-Output 'ok'\n"
        return "raise RuntimeError('fail')\n" if fail else "print('ok')\n"

    def _build_archive(self, root: Path, version: str = "v0.2.0", *, fail: bool = False) -> Path:
        archive_root = root / "archive" / "subtitle-edit-bay-main"
        (archive_root / "src").mkdir(parents=True)
        (archive_root / "scripts").mkdir(parents=True)
        (archive_root / "VERSION").write_text(f"{version}\n", encoding="utf-8")
        (archive_root / "README.md").write_text("new readme", encoding="utf-8")
        (archive_root / "src" / "app.py").write_text("new code", encoding="utf-8")
        (archive_root / "scripts" / "setup.ps1").write_text(self._setup_script_content(fail=fail), encoding="utf-8")

        zip_path = root / "latest.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as handle:
            for file in archive_root.rglob("*"):
                if file.is_file():
                    handle.write(file, file.relative_to(archive_root.parent))
        return zip_path

    def test_apply_zip_update_installs_files_and_updates_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            distribution = base / "distribution"
            (distribution / "src").mkdir(parents=True)
            (distribution / "scripts").mkdir(parents=True)
            (distribution / ".local").mkdir(parents=True)
            (distribution / "VERSION").write_text("v0.1.0\n", encoding="utf-8")
            (distribution / "README.md").write_text("old readme", encoding="utf-8")
            (distribution / "src" / "app.py").write_text("old code", encoding="utf-8")
            (distribution / ".local" / "update-manifest.json").write_text(
                '["README.md", "src/app.py"]\n',
                encoding="utf-8",
            )

            zip_path = self._build_archive(base)
            backup, installed = updater.apply_zip_update(distribution, str(zip_path))

            self.assertTrue(backup.is_dir())
            self.assertIn("README.md", installed)
            self.assertIn("src/app.py", installed)
            self.assertEqual((distribution / "README.md").read_text(encoding="utf-8"), "new readme")
            self.assertEqual((distribution / "src" / "app.py").read_text(encoding="utf-8"), "new code")
            self.assertEqual((distribution / "VERSION").read_text(encoding="utf-8").strip(), "v0.2.0")

    def test_apply_zip_update_rolls_back_on_setup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            distribution = base / "distribution"
            (distribution / "src").mkdir(parents=True)
            (distribution / "scripts").mkdir(parents=True)
            (distribution / ".local").mkdir(parents=True)
            (distribution / "VERSION").write_text("v0.1.0\n", encoding="utf-8")
            (distribution / "README.md").write_text("old readme", encoding="utf-8")
            (distribution / "src" / "app.py").write_text("old code", encoding="utf-8")

            zip_path = self._build_archive(base, fail=True)

            with self.assertRaises(updater.UpdaterError):
                updater.apply_zip_update(distribution, str(zip_path))

            self.assertEqual((distribution / "README.md").read_text(encoding="utf-8"), "old readme")
            self.assertEqual((distribution / "src" / "app.py").read_text(encoding="utf-8"), "old code")


class LaunchUpdateScriptTests(unittest.TestCase):
    def test_launch_update_script_uses_powershell_on_windows(self) -> None:
        with patch("sys.platform", "win32"), patch("shutil.which", return_value="powershell.exe"):
            command = updater.launch_update_script(Path("/app"), "https://example.com/app.zip")
        self.assertEqual(command[0], "powershell.exe")
        self.assertIn("update.ps1", command[command.index("-File") + 1])
        self.assertIn("https://example.com/app.zip", command)

    def test_launch_update_script_falls_back_to_python_module(self) -> None:
        with patch("sys.platform", "linux"), patch("shutil.which", return_value=None):
            command = updater.launch_update_script(Path("/app"), "https://example.com/app.zip")
        self.assertEqual(command[1:-2], ["-m", "src.updater", "apply"])
        self.assertEqual(command[-2:], ["--archive-url", "https://example.com/app.zip"])


if __name__ == "__main__":
    unittest.main()
