import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class WindowsLauncherTests(unittest.TestCase):
    def test_start_uses_project_virtual_environment(self) -> None:
        launcher = (ROOT / "start.bat").read_text(encoding="utf-8")

        self.assertIn(r".venv\Scripts\python.exe", launcher)
        self.assertIn("-m src.gui", launcher)
        self.assertIn(r".local\ffmpeg_path.txt", launcher)

    def test_setup_uses_module_pip_and_winget_fallbacks(self) -> None:
        setup = (ROOT / "scripts" / "setup.ps1").read_text(encoding="utf-8")

        self.assertIn('"Python.Python.3.10"', setup)
        self.assertIn('"Gyan.FFmpeg"', setup)
        self.assertIn("-m pip install", setup)
        self.assertIn("check_runtime_dependencies", setup)
        self.assertIn('Get-Command "nvidia-smi.exe"', setup)
        self.assertIn("https://download.pytorch.org/whl/cu128", setup)
        self.assertIn('$whisperXVersion = "3.8.6"', setup)
        self.assertIn('$torchVersion = "2.8.0"', setup)
        self.assertIn("--force-reinstall", setup)
        self.assertIn("--no-deps", setup)
        self.assertIn("-m pip check", setup)
        self.assertIn('$ErrorActionPreference = "Continue"', setup)
        self.assertIn('$PSDefaultParameterValues["*:ErrorAction"] = "Stop"', setup)

    def test_update_supports_git_and_zip_distributions(self) -> None:
        launcher = (ROOT / "update.bat").read_text(encoding="utf-8")
        updater = (ROOT / "scripts" / "update.ps1").read_text(encoding="utf-8")

        self.assertIn(r"scripts\update.ps1", launcher)
        self.assertIn("pull --ff-only", updater)
        self.assertIn("--untracked-files=no", updater)
        self.assertIn("Invoke-WebRequest", updater)
        self.assertIn("subtitle-edit-bay/archive/refs/heads/main.zip", updater)
        self.assertIn('"video_import"', updater)
        self.assertIn('"video_export"', updater)
        self.assertIn("assets/speaker_colors.json", updater)
        self.assertIn("update_backups", updater)
        self.assertIn('Join-Path $projectRoot "scripts\\setup.ps1"', updater)
        self.assertNotIn("reset --hard", updater)

    @unittest.skipUnless(shutil.which("powershell.exe"), "Windows PowerShell is required")
    def test_zip_update_preserves_local_data_and_runs_new_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            distribution = base / "distribution"
            archive_parent = base / "archive"
            archive_root = archive_parent / "subtitle-edit-bay-main"

            (distribution / "scripts").mkdir(parents=True)
            (distribution / "src").mkdir()
            (distribution / "assets").mkdir()
            (distribution / "video_import").mkdir()
            (distribution / ".gui").mkdir()
            (distribution / ".venv").mkdir()
            shutil.copy2(ROOT / "scripts" / "update.ps1", distribution / "scripts" / "update.ps1")
            (distribution / "README.md").write_text("old readme", encoding="utf-8")
            (distribution / "src" / "app.py").write_text("old code", encoding="utf-8")
            (distribution / "assets" / "speaker_colors.json").write_text("old colors", encoding="utf-8")
            (distribution / "video_import" / "keep.mkv").write_bytes(b"video")
            (distribution / ".gui" / "runtime_config.json").write_text("old gui", encoding="utf-8")
            (distribution / ".venv" / "marker.txt").write_text("old venv", encoding="utf-8")

            (archive_root / "scripts").mkdir(parents=True)
            (archive_root / "src").mkdir()
            (archive_root / "assets").mkdir()
            (archive_root / "video_import").mkdir()
            (archive_root / ".gui").mkdir()
            (archive_root / ".venv").mkdir()
            (archive_root / "README.md").write_text("new readme", encoding="utf-8")
            (archive_root / "src" / "app.py").write_text("new code", encoding="utf-8")
            (archive_root / "assets" / "speaker_colors.json").write_text("new colors", encoding="utf-8")
            (archive_root / "video_import" / "replace.txt").write_text("replace", encoding="utf-8")
            (archive_root / ".gui" / "runtime_config.json").write_text("new gui", encoding="utf-8")
            (archive_root / ".venv" / "marker.txt").write_text("new venv", encoding="utf-8")
            (archive_root / "scripts" / "setup.ps1").write_text(
                '$root = Split-Path -Parent $PSScriptRoot\n'
                '[IO.File]::WriteAllText((Join-Path $root "setup-ran.txt"), "ok")\n',
                encoding="utf-8",
            )

            zip_path = Path(shutil.make_archive(str(base / "latest"), "zip", root_dir=archive_parent))
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(distribution / "scripts" / "update.ps1"),
                    "-ArchiveUrl",
                    str(zip_path),
                ],
                cwd=distribution,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((distribution / "README.md").read_text(encoding="utf-8"), "new readme")
            self.assertEqual((distribution / "src" / "app.py").read_text(encoding="utf-8"), "new code")
            self.assertEqual((distribution / "assets" / "speaker_colors.json").read_text(encoding="utf-8"), "old colors")
            self.assertEqual((distribution / ".gui" / "runtime_config.json").read_text(encoding="utf-8"), "old gui")
            self.assertEqual((distribution / ".venv" / "marker.txt").read_text(encoding="utf-8"), "old venv")
            self.assertTrue((distribution / "video_import" / "keep.mkv").is_file())
            self.assertFalse((distribution / "video_import" / "replace.txt").exists())
            self.assertEqual((distribution / "setup-ran.txt").read_text(encoding="utf-8"), "ok")

            backups = list((distribution / ".local" / "update_backups").glob("*/README.md"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "old readme")

    def test_local_install_artifacts_are_ignored(self) -> None:
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn(".venv/", ignore)
        self.assertIn(".local/", ignore)

    def test_documentation_uses_launchers_and_virtual_environment(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        usage = (ROOT / "docs" / "USAGE.md").read_text(encoding="utf-8")

        self.assertIn("setup.bat", readme)
        self.assertIn("start.bat", readme)
        self.assertIn("update.bat", readme)
        self.assertIn(r".\.venv\Scripts\python.exe", readme)
        self.assertIn(r".\.venv\Scripts\python.exe", usage)
        self.assertIn("update.bat", usage)
        self.assertNotIn("\npython -m src.gui", readme)
        self.assertNotIn("\npython -m src.gui", usage)


if __name__ == "__main__":
    unittest.main()
