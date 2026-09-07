from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.installer_migration import (
    MigrationError,
    MigrationOptions,
    RuntimeCapabilities,
    migrate_legacy_workspace,
    validated_runtime_config,
)


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
                    destination / "assets" / "runtime_config.json",
                    RuntimeCapabilities(cuda=False, nvenc=False),
                )

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

    def test_installer_and_setup_connect_migration_after_runtime_verification(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        setup = (repository / "scripts" / "setup.ps1").read_text(encoding="utf-8-sig")
        installer = (repository / "installer" / "SubtitleEditBay.iss").read_text(encoding="utf-8-sig")
        setup_batch = (repository / "setup.bat").read_text(encoding="utf-8-sig")

        self.assertIn("[string]$MigrationSource", setup)
        self.assertLess(setup.index("$torchRuntimeJson"), setup.index("src.installer_migration"))
        self.assertLess(setup.index("-m pip check"), setup.index("src.installer_migration"))
        self.assertLess(setup.index("assert status.ready"), setup.index("src.installer_migration"))
        self.assertIn('Name: "legacymigration"', installer)
        self.assertIn("WizardIsTaskSelected('legacymigration')", installer)
        self.assertIn('--migration-source "', installer)
        self.assertIn("--skip-runtime-config", installer)
        self.assertIn("--skip-speaker-colors", installer)
        self.assertIn("--skip-workspace-reference", installer)
        self.assertIn("function NextButtonClick", installer)
        self.assertIn("not FileExists(AddBackslash(LegacyPath) + 'setup.bat')", installer)
        self.assertIn('setup.ps1" %*', setup_batch)


if __name__ == "__main__":
    unittest.main()
