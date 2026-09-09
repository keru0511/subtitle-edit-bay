from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from src import installer_migration
from src.installer_migration import (
    MigrationError,
    MigrationOptions,
    RuntimeCapabilities,
    migrate_legacy_workspace,
    validated_runtime_config,
)
from src.runtime_config import load_command_runtime_config


class InstallerMigrationTests(unittest.TestCase):
    def _workspaces(self, root: Path) -> tuple[Path, Path]:
        source = root / "legacy"
        destination = root / "installed"
        for marker in ("setup.bat", "start.bat"):
            (source / marker).parent.mkdir(parents=True, exist_ok=True)
            (source / marker).write_text(marker, encoding="utf-8")
        (source / "src").mkdir()
        (source / ".gui").mkdir()
        (source / "assets").mkdir()
        (destination / "assets").mkdir(parents=True)
        template = {
            "shared": {"device": "cuda", "compute_type": "float16", "language": "ja"},
            "craig_pipeline": {"video_codec": "h264_nvenc", "x264_crf": 18},
        }
        (destination / "assets" / "runtime_config.json").write_text(json.dumps(template), encoding="utf-8")
        return source, destination

    def test_migrates_validated_user_data_without_reusing_venv_or_moving_projects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, destination = self._workspaces(Path(temporary))
            old_config = {
                "shared": {
                    "device": "cuda",
                    "compute_type": "float16",
                    "language": "en",
                    "obsolete_setting": "ignored",
                },
                "craig_pipeline": {
                    "video_codec": "h264_nvenc",
                    "x264_crf": 20,
                    "transcription_context": {
                        "game_title": "Test Game",
                        "dictionary_path": "dictionaries/game.json",
                        "dictionary_confirmed": True,
                    },
                },
                "unknown_section": {"anything": True},
            }
            dictionary = source / "dictionaries" / "game.json"
            dictionary.parent.mkdir()
            dictionary.write_text(json.dumps({"game_title": "Test Game", "terms": []}), encoding="utf-8")
            (source / ".gui" / "runtime_config.json").write_text(json.dumps(old_config), encoding="utf-8")
            colors = {"speakers": {"Alice": {"color": "#AABBCC"}}, "files": {}}
            (source / "assets" / "speaker_colors.json").write_text(json.dumps(colors), encoding="utf-8")
            old_python = source / ".venv" / "Scripts" / "python.exe"
            old_python.parent.mkdir(parents=True)
            old_python.write_bytes(b"legacy-runtime")
            media = source / "video_import" / "large-video.mp4"
            media.parent.mkdir()
            media.write_bytes(b"media")

            result = migrate_legacy_workspace(
                source,
                destination,
                capabilities=RuntimeCapabilities(cuda=False, nvenc=False),
                now=datetime(2026, 9, 7, tzinfo=timezone.utc),
            )

            migrated = json.loads((destination / ".gui" / "runtime_config.json").read_text())
            self.assertEqual(migrated["shared"]["device"], "cpu")
            self.assertEqual(migrated["shared"]["compute_type"], "int8")
            self.assertEqual(migrated["craig_pipeline"]["video_codec"], "libx264")
            self.assertEqual(
                migrated["craig_pipeline"]["transcription_context"]["dictionary_path"],
                str(dictionary.resolve()),
            )
            self.assertNotIn("obsolete_setting", migrated["shared"])
            self.assertNotIn("unknown_section", migrated)
            self.assertEqual(json.loads((destination / "assets" / "speaker_colors.json").read_text()), colors)
            self.assertFalse((destination / ".venv").exists())
            self.assertTrue(old_python.exists())
            self.assertTrue(media.exists())
            self.assertFalse((destination / "video_import").exists())
            self.assertFalse(result.runtime_reused)
            self.assertEqual(result.workspace_references, (str(source / "video_import"),))
            self.assertEqual(result.reclaimable_venv_bytes, len(b"legacy-runtime"))
            self.assertIn(str(source / "video_import"), result.cleanup_blockers)
            record = destination / ".local" / "migration" / "migration-20260907T000000Z.json"
            self.assertTrue(record.is_file())
            self.assertFalse(json.loads(record.read_text())["runtime_reused"])

    def test_existing_installer_data_is_preserved_without_explicit_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, destination = self._workspaces(Path(temporary))
            old = {"shared": {"device": "cpu", "compute_type": "int8", "language": "en"}}
            current = {"shared": {"device": "cpu", "compute_type": "int8", "language": "ja"}}
            (source / ".gui" / "runtime_config.json").write_text(json.dumps(old), encoding="utf-8")
            current_path = destination / ".gui" / "runtime_config.json"
            current_path.parent.mkdir()
            current_path.write_text(json.dumps(current), encoding="utf-8")

            result = migrate_legacy_workspace(
                source,
                destination,
                capabilities=RuntimeCapabilities(cuda=False, nvenc=False),
            )

            self.assertEqual(json.loads(current_path.read_text()), current)
            self.assertIn(str(current_path), result.preserved)

    def test_explicit_overwrite_replaces_existing_installer_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, destination = self._workspaces(Path(temporary))
            old = {"shared": {"device": "cpu", "compute_type": "int8", "language": "en"}}
            (source / ".gui" / "runtime_config.json").write_text(json.dumps(old), encoding="utf-8")
            current_path = destination / ".gui" / "runtime_config.json"
            current_path.parent.mkdir()
            current_path.write_text("{}", encoding="utf-8")

            migrate_legacy_workspace(
                source,
                destination,
                capabilities=RuntimeCapabilities(cuda=False, nvenc=False),
                options=MigrationOptions(overwrite=True),
            )

            self.assertEqual(json.loads(current_path.read_text())["shared"]["language"], "en")

    def test_secret_like_runtime_config_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, destination = self._workspaces(Path(temporary))
            old_path = source / ".gui" / "runtime_config.json"
            old_path.write_text(
                json.dumps({"shared": {"device": "cpu", "api_token": "do-not-copy"}}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(MigrationError, "secret-like"):
                validated_runtime_config(
                    old_path,
                    RuntimeCapabilities(cuda=False, nvenc=False),
                )

    def test_explicit_schema_preserves_gui_setting_and_rejects_invalid_nullable_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, _destination = self._workspaces(Path(temporary))
            old_path = source / ".gui" / "runtime_config.json"
            old_path.write_text(
                json.dumps(
                    {
                        "shared": {"codex_model": "gpt-fast", "device": "cpu"},
                        "craig_pipeline": {"reference_audio": None},
                    }
                ),
                encoding="utf-8",
            )
            migrated, _adjusted = validated_runtime_config(
                old_path,
                RuntimeCapabilities(cuda=False, nvenc=False),
            )
            self.assertEqual(migrated["shared"]["codex_model"], "gpt-fast")
            self.assertIsNone(migrated["craig_pipeline"]["reference_audio"])

            old_path.write_text(
                json.dumps({"craig_pipeline": {"reference_audio": {}}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(MigrationError, "reference_audio"):
                validated_runtime_config(old_path, RuntimeCapabilities(cuda=False, nvenc=False))

    def test_cpu_migration_corrects_effective_settings_for_every_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _destination = self._workspaces(root)
            old_path = source / ".gui" / "runtime_config.json"
            old_path.write_text(
                json.dumps(
                    {
                        "shared": {"device": "cuda", "compute_type": "float16"},
                        "pipeline": {"device": "cuda", "compute_type": "float16"},
                        "batch": {"compute_type": "float16"},
                        "craig_pipeline": {"device": "cuda", "compute_type": "float16"},
                    }
                ),
                encoding="utf-8",
            )
            migrated, _adjusted = validated_runtime_config(
                old_path,
                RuntimeCapabilities(cuda=False, nvenc=False),
            )
            migrated_path = root / "migrated.json"
            migrated_path.write_text(json.dumps(migrated), encoding="utf-8")

            for command in ("pipeline", "batch", "craig_pipeline"):
                with self.subTest(command=command):
                    effective = load_command_runtime_config(command, migrated_path)
                    self.assertEqual(effective["device"], "cpu")
                    self.assertEqual(effective["compute_type"], "int8")

    def test_invalid_speaker_colors_fail_before_any_destination_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, destination = self._workspaces(Path(temporary))
            (source / ".gui" / "runtime_config.json").write_text(
                json.dumps({"shared": {"device": "cpu", "compute_type": "int8"}}),
                encoding="utf-8",
            )
            (source / "assets" / "speaker_colors.json").write_text(
                json.dumps({"speakers": {"Alice": {"color": "not-a-color"}}}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(MigrationError, "invalid speaker color"):
                migrate_legacy_workspace(
                    source,
                    destination,
                    capabilities=RuntimeCapabilities(cuda=False, nvenc=False),
                )

            self.assertFalse((destination / ".gui" / "runtime_config.json").exists())
            self.assertFalse((destination / ".local" / "migration").exists())

    def test_write_failure_rolls_back_new_and_overwritten_destination_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, destination = self._workspaces(Path(temporary))
            old_config = {"shared": {"device": "cpu", "language": "en"}}
            (source / ".gui" / "runtime_config.json").write_text(json.dumps(old_config), encoding="utf-8")
            media = source / "video_import" / "clip.mp4"
            media.parent.mkdir()
            media.write_bytes(b"media")
            destination_config = destination / ".gui" / "runtime_config.json"
            destination_config.parent.mkdir()
            original_bytes = b'{"shared":{"language":"ja"}}\n'
            destination_config.write_bytes(original_bytes)

            original_write = installer_migration._write_json_atomic
            write_count = 0

            def fail_on_workspace(path: Path, payload: object) -> None:
                nonlocal write_count
                write_count += 1
                if write_count == 2:
                    raise OSError("injected workspace write failure")
                original_write(path, payload)

            with mock.patch.object(installer_migration, "_write_json_atomic", side_effect=fail_on_workspace):
                with self.assertRaisesRegex(MigrationError, "rolled back"):
                    migrate_legacy_workspace(
                        source,
                        destination,
                        capabilities=RuntimeCapabilities(cuda=False, nvenc=False),
                        options=MigrationOptions(overwrite=True),
                    )

            self.assertEqual(write_count, 2)
            self.assertEqual(destination_config.read_bytes(), original_bytes)
            self.assertFalse((destination / ".gui" / "legacy_workspaces.json").exists())
            self.assertFalse((destination / ".local" / "migration").exists())

    def test_audit_record_failure_rolls_back_all_migration_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, destination = self._workspaces(Path(temporary))
            (source / ".gui" / "runtime_config.json").write_text(
                json.dumps({"shared": {"device": "cpu", "codex_model": "gpt-fast"}}),
                encoding="utf-8",
            )
            original_write = installer_migration._write_json_atomic

            def fail_on_audit(path: Path, payload: object) -> None:
                if path.parent.name == "migration":
                    raise OSError("injected audit write failure")
                original_write(path, payload)

            with mock.patch.object(installer_migration, "_write_json_atomic", side_effect=fail_on_audit):
                with self.assertRaisesRegex(MigrationError, "rolled back"):
                    migrate_legacy_workspace(
                        source,
                        destination,
                        capabilities=RuntimeCapabilities(cuda=False, nvenc=False),
                    )

            self.assertFalse((destination / ".gui" / "runtime_config.json").exists())
            self.assertFalse((destination / ".local" / "migration").exists())

    def test_workspace_registry_merges_distinct_sources_without_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_a, destination = self._workspaces(root / "a")
            source_b, _unused = self._workspaces(root / "b")
            for source, name in ((source_a, "a.mp4"), (source_b, "b.mp4")):
                media = source / "video_import" / name
                media.parent.mkdir()
                media.write_bytes(b"media")

            for source in (source_a, source_b, source_a):
                migrate_legacy_workspace(
                    source,
                    destination,
                    capabilities=RuntimeCapabilities(cuda=False, nvenc=False),
                )

            registry = json.loads((destination / ".gui" / "legacy_workspaces.json").read_text())
            self.assertEqual(
                {entry["path"] for entry in registry["workspaces"]},
                {str(source_a.resolve()), str(source_b.resolve())},
            )
            self.assertEqual(len(registry["workspaces"]), 2)

    def test_rejects_non_product_folder_and_destination_as_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, destination = self._workspaces(root)
            with self.assertRaisesRegex(MigrationError, "must be different"):
                migrate_legacy_workspace(
                    source,
                    source,
                    capabilities=RuntimeCapabilities(cuda=False, nvenc=False),
                )
            unrelated = root / "unrelated"
            unrelated.mkdir()
            with self.assertRaisesRegex(MigrationError, "missing"):
                migrate_legacy_workspace(
                    unrelated,
                    destination,
                    capabilities=RuntimeCapabilities(cuda=False, nvenc=False),
                )

    def test_installer_and_setup_preserve_and_preflight_migration_request(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        setup = (repository / "scripts" / "setup.ps1").read_text(encoding="utf-8-sig")
        installer = (repository / "installer" / "SubtitleEditBay.iss").read_text(encoding="utf-8-sig")
        setup_batch = (repository / "setup.bat").read_text(encoding="utf-8-sig")
        launcher = (repository / "launcher" / "SubtitleEditBayLauncher.c").read_text(encoding="utf-8")
        launcher_build = (repository / "scripts" / "build_launcher.ps1").read_text(encoding="utf-8-sig")

        self.assertIn("[string]$MigrationSource", setup)
        self.assertIn("[switch]$SkipRuntimeConfig", setup)
        self.assertIn("[switch]$SkipSpeakerColors", setup)
        self.assertIn("[switch]$SkipWorkspaceReference", setup)
        self.assertLess(setup.index("$pendingMigrationPath"), setup.index("Enter-SetupMutex"))
        self.assertLess(setup.index("must be different. No setup changes were made"), setup.index("Enter-SetupMutex"))
        self.assertLess(setup.index("$torchRuntimeJson"), setup.index("src.installer_migration"))
        self.assertLess(setup.index("-m pip check"), setup.index("src.installer_migration"))
        self.assertLess(setup.index("assert status.ready"), setup.index("src.installer_migration"))
        self.assertIn('$migrationArguments += "--skip-runtime-config"', setup)
        self.assertIn('$migrationArguments += "--skip-speaker-colors"', setup)
        self.assertIn('$migrationArguments += "--skip-workspace-reference"', setup)
        self.assertIn('Name: "legacymigration"', installer)
        self.assertIn("WizardIsTaskSelected('legacymigration')", installer)
        self.assertIn('--migration-source "', installer)
        self.assertIn("--skip-runtime-config", installer)
        self.assertIn("--skip-speaker-colors", installer)
        self.assertIn("--skip-workspace-reference", installer)
        self.assertIn("function NextButtonClick", installer)
        self.assertIn("CompareText(", installer)
        self.assertIn("GetFinalPathNameByHandle", installer)
        self.assertIn("FinalDirectoryPath(LegacyPath)", installer)
        self.assertIn("FinalDirectoryPath(InstallPath)", installer)
        self.assertIn("not FileExists(AddBackslash(LegacyPath) + 'setup.bat')", installer)
        self.assertIn("procedure SavePendingMigrationRequest", installer)
        self.assertIn("SaveStringToUTF8File(PendingPath", installer)
        self.assertIn("pending-request.json", installer)
        self.assertIn("skip_workspace_reference", installer)
        self.assertLess(
            installer.index("procedure SavePendingMigrationRequest"),
            installer.index("procedure CurStepChanged"),
        )
        self.assertIn("Remove-Item -LiteralPath $pendingMigrationPath", setup)
        self.assertIn("Resolve-FinalDirectoryPath -Path $MigrationSource", setup)
        self.assertIn(r'Filename: "{app}\SubtitleEditBayLauncher.exe"', installer)
        self.assertIn('Parameters: "--setup {code:SetupParameters}"', installer)
        self.assertNotIn(r'Filename: "{app}\setup.bat"', installer)
        self.assertIn("CommandLineToArgvW", launcher)
        self.assertLess(launcher.index("#include <windows.h>"), launcher.index("#include <shellapi.h>"))
        self.assertIn('L"SUBTITLE_EDIT_BAY_MIGRATION_SOURCE"', launcher)
        self.assertIn('L"SUBTITLE_EDIT_BAY_SKIP_RUNTIME_CONFIG"', launcher)
        self.assertIn('L"SUBTITLE_EDIT_BAY_SKIP_SPEAKER_COLORS"', launcher)
        self.assertIn('L"SUBTITLE_EDIT_BAY_SKIP_WORKSPACE_REFERENCE"', launcher)
        self.assertIn("shell32.lib", launcher_build)
        self.assertIn('setup.ps1" %*', setup_batch)

    @unittest.skipUnless(os.name == "nt", "PowerShell setup preflight is Windows-only")
    def test_pending_request_is_reused_before_runtime_setup(self) -> None:
        powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if not powershell:
            self.skipTest("PowerShell is unavailable")
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _destination = self._workspaces(root)
            pending = root / "pending-request.json"
            pending.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source": str(source),
                        "skip_runtime_config": True,
                        "skip_speaker_colors": False,
                        "skip_workspace_reference": True,
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(repository / "scripts" / "setup.ps1"),
                    "-ProbePendingMigrationOnly",
                    "-PendingMigrationRequestPath",
                    str(pending),
                ],
                cwd=repository,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout.strip().splitlines()[-1])
            self.assertEqual(Path(result["source"]), source.resolve())
            self.assertTrue(result["skip_runtime_config"])
            self.assertFalse(result["skip_speaker_colors"])
            self.assertTrue(result["skip_workspace_reference"])
            self.assertTrue(result["pending_request_preserved"])
            self.assertTrue(pending.exists())

            repeated = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(repository / "scripts" / "setup.ps1"),
                    "-ProbePendingMigrationOnly",
                    "-PendingMigrationRequestPath",
                    str(pending),
                ],
                cwd=repository,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(
                json.loads(repeated.stdout.strip().splitlines()[-1]),
                result,
                "a postponed or failed setup must reuse the same pending request on repair",
            )

    @unittest.skipUnless(os.name == "nt", "PowerShell setup preflight is Windows-only")
    def test_setup_rejects_destination_as_source_before_mutating_state(self) -> None:
        powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if not powershell:
            self.skipTest("PowerShell is unavailable")
        repository = Path(__file__).resolve().parents[1]
        protected = [
            repository / ".local" / "runtime-manifest.json",
            repository / ".gui" / "runtime_config.json",
            repository / "setup.bat",
        ]
        before = {path: path.read_bytes() if path.exists() else None for path in protected}
        with tempfile.TemporaryDirectory() as temporary:
            junction = Path(temporary) / "repository-alias"
            created = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(repository)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stderr + created.stdout)
            try:
                completed = subprocess.run(
                    [
                        powershell,
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(repository / "scripts" / "setup.ps1"),
                        "-ProbePendingMigrationOnly",
                        "-MigrationSource",
                        str(junction),
                    ],
                    cwd=repository,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("must be different", completed.stderr + completed.stdout)
                after = {path: path.read_bytes() if path.exists() else None for path in protected}
                self.assertEqual(after, before)
            finally:
                subprocess.run(
                    ["cmd.exe", "/d", "/c", "rmdir", str(junction)],
                    capture_output=True,
                    check=False,
                )


if __name__ == "__main__":
    unittest.main()
