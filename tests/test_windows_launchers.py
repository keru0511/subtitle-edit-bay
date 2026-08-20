import hashlib
import json
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
        self.assertIn("archive/refs/tags", updater)
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
            (distribution / ".local").mkdir()
            shutil.copy2(ROOT / "scripts" / "update.ps1", distribution / "scripts" / "update.ps1")
            (distribution / "README.md").write_text("old readme", encoding="utf-8")
            (distribution / "src" / "app.py").write_text("old code", encoding="utf-8")
            (distribution / "legacy.txt").write_text("remove me", encoding="utf-8")
            (distribution / ".env").write_text("keep me", encoding="utf-8")
            (distribution / ".local" / "update-manifest.json").write_text(
                '["README.md", "src/app.py", "legacy.txt"]\n',
                encoding="utf-8",
            )
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
            self.assertEqual((distribution / ".env").read_text(encoding="utf-8"), "keep me")
            self.assertFalse((distribution / "legacy.txt").exists())
            self.assertEqual((distribution / "assets" / "speaker_colors.json").read_text(encoding="utf-8"), "old colors")
            self.assertEqual((distribution / ".gui" / "runtime_config.json").read_text(encoding="utf-8"), "old gui")
            self.assertEqual((distribution / ".venv" / "marker.txt").read_text(encoding="utf-8"), "old venv")
            self.assertTrue((distribution / "video_import" / "keep.mkv").is_file())
            self.assertFalse((distribution / "video_import" / "replace.txt").exists())
            self.assertEqual((distribution / "setup-ran.txt").read_text(encoding="utf-8"), "ok")

            backups = list((distribution / ".local" / "update_backups").glob("*/README.md"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "old readme")

    @unittest.skipUnless(shutil.which("powershell.exe"), "Windows PowerShell is required")
    def test_zip_update_rolls_back_when_setup_fails(self) -> None:
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
            (distribution / ".local").mkdir()
            shutil.copy2(ROOT / "scripts" / "update.ps1", distribution / "scripts" / "update.ps1")
            (distribution / "README.md").write_text("old readme", encoding="utf-8")
            (distribution / "src" / "app.py").write_text("old code", encoding="utf-8")
            (distribution / "legacy.txt").write_text("remove me", encoding="utf-8")
            (distribution / ".env").write_text("keep me", encoding="utf-8")
            (distribution / ".local" / "update-manifest.json").write_text(
                '["README.md", "src/app.py", "legacy.txt"]\n',
                encoding="utf-8",
            )
            (distribution / "assets" / "speaker_colors.json").write_text("old colors", encoding="utf-8")
            (distribution / "video_import" / "keep.mkv").write_bytes(b"video")
            (distribution / ".gui" / "runtime_config.json").write_text("old gui", encoding="utf-8")
            (distribution / ".venv" / "marker.txt").write_text("old venv", encoding="utf-8")
            (distribution / "VERSION").write_text("v0.1.0\n", encoding="utf-8")

            (archive_root / "scripts").mkdir(parents=True)
            (archive_root / "src").mkdir()
            (archive_root / "README.md").write_text("new readme", encoding="utf-8")
            (archive_root / "src" / "app.py").write_text("new code", encoding="utf-8")
            (archive_root / "VERSION").write_text("v0.2.0\n", encoding="utf-8")
            (archive_root / "scripts" / "setup.ps1").write_text(
                'throw "setup failed"\n',
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

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Update failed", result.stdout)
            self.assertIn("Restoring files", result.stdout)
            self.assertEqual((distribution / "README.md").read_text(encoding="utf-8"), "old readme")
            self.assertEqual((distribution / "src" / "app.py").read_text(encoding="utf-8"), "old code")
            self.assertTrue((distribution / "legacy.txt").is_file())
            self.assertEqual((distribution / "legacy.txt").read_text(encoding="utf-8"), "remove me")
            self.assertEqual((distribution / ".env").read_text(encoding="utf-8"), "keep me")
            self.assertEqual(
                (distribution / "assets" / "speaker_colors.json").read_text(encoding="utf-8"),
                "old colors",
            )
            self.assertTrue((distribution / "video_import" / "keep.mkv").is_file())
            self.assertFalse((distribution / "setup-ran.txt").exists())

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


    def test_update_script_reports_version_before_and_after_zip_update(self) -> None:
        script = (Path(__file__).resolve().parents[1] / "scripts" / "update.ps1").read_text(encoding="utf-8")

        self.assertIn("Source version before update", script)
        self.assertIn("Source version after update", script)
        self.assertIn("$postUpdateVersion", script)
        self.assertIn("Write-InstalledManifest", script)

    @unittest.skipUnless(shutil.which("powershell.exe"), "Windows PowerShell is required")
    def test_installer_helper_restores_snapshot_after_partial_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            install = base / "distribution"
            (install / "scripts").mkdir(parents=True)
            (install / "src").mkdir()
            (install / "VERSION").write_text("v0.1.0\n", encoding="utf-8")
            (install / "scripts" / "launch.ps1").write_text("old launcher", encoding="utf-8")
            (install / "src" / "app.py").write_text("old app", encoding="utf-8")
            restart_executable = install / "SubtitleEditBayLauncher.exe"
            restart_executable.write_bytes(b"old launcher binary")

            fake_script = base / "fake-installer.ps1"
            fake_script.write_text(
                "$root = (Get-Location).Path\n"
                "[IO.File]::WriteAllText((Join-Path $root 'VERSION'), 'v9.9.9`n')\n"
                "[IO.File]::WriteAllText((Join-Path $root 'scripts\\launch.ps1'), 'new launcher')\n"
                "[IO.File]::WriteAllText((Join-Path $root 'src\\new.py'), 'new file')\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_installer = base / "fake-installer.cmd"
            fake_installer.write_text(
                '@echo off\r\n'
                'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0fake-installer.ps1" %*\r\n'
                'exit /b %ERRORLEVEL%\r\n',
                encoding="utf-8",
            )
            result_path = base / "update-result.json"
            digest = hashlib.sha256(fake_installer.read_bytes()).hexdigest()
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ROOT / "scripts" / "apply_installer_update.ps1"),
                    "-PackagePath",
                    str(fake_installer),
                    "-ParentPid",
                    "-1",
                    "-InstallRoot",
                    str(install),
                    "-RestartExecutable",
                    str(restart_executable),
                    "-ExpectedVersion",
                    "v9.9.9",
                    "-ExpectedSha256",
                    digest,
                    "-ResultPath",
                    str(result_path),
                ],
                cwd=install,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((install / "VERSION").read_text(encoding="utf-8"), "v0.1.0\n")
            self.assertEqual((install / "scripts" / "launch.ps1").read_text(encoding="utf-8"), "old launcher")
            self.assertEqual((install / "src" / "app.py").read_text(encoding="utf-8"), "old app")
            self.assertFalse((install / "src" / "new.py").exists())
            update_result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(update_result["status"], "rollback")
            self.assertTrue(update_result["rollback_restored"])


if __name__ == "__main__":
    unittest.main()
