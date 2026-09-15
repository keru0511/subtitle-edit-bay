from __future__ import annotations

import json
import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.installer_migration import MigrationError
from src.installer_migration_entrypoint import (
    InstallerMigrationRequest,
    apply_installer_migration,
    build_installer_migration_plan,
)


class InstallerMigrationEntrypointTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path]:
        source = root / "legacy-bat-zip"
        destination = root / "installer"
        (source / ".gui").mkdir(parents=True)
        (source / "assets").mkdir()
        (source / "src").mkdir()
        (source / ".venv" / "Scripts").mkdir(parents=True)
        (source / "video_import").mkdir()
        (source / "video_export").mkdir()
        (source / "out").mkdir()
        (source / "setup.bat").write_text("legacy setup must never execute", encoding="utf-8")
        (source / "start.bat").write_text("legacy start must never execute", encoding="utf-8")
        (source / ".venv" / "Scripts" / "python.exe").write_bytes(b"old runtime")
        (source / ".gui" / "runtime_config.json").write_text(
            json.dumps(
                {
                    "shared": {"device": "cuda", "compute_type": "float16", "language": "ja"},
                    "craig_pipeline": {"video_codec": "h264_nvenc"},
                }
            ),
            encoding="utf-8",
        )
        (source / "assets" / "speaker_colors.json").write_text(
            json.dumps({"speakers": {"speaker-a": "#12ABEF"}, "files": {}}),
            encoding="utf-8",
        )
        (source / "video_import" / "capture.mp4").write_bytes(b"media")
        (source / "video_export" / "render.mp4").write_bytes(b"output")
        (source / "out" / "subtitle.ass").write_bytes(b"subtitle")
        destination.mkdir()
        return source, destination

    def test_plan_is_read_only_and_contains_settings_cleanup_and_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, destination = self._fixture(Path(temporary))
            old_runtime = (source / ".venv" / "Scripts" / "python.exe").read_bytes()
            plan = build_installer_migration_plan(
                InstallerMigrationRequest(
                    source=str(source),
                    destination=str(destination),
                    cuda=False,
                    nvenc=False,
                )
            )
            payload = plan.to_dict()
            self.assertEqual(payload["schema_version"], 1)
            self.assertIn("settings", payload)
            self.assertIn("cleanup", payload)
            self.assertIn(str(source / "video_import"), payload["workspace_references"])
            self.assertIn(str(source / "video_export"), payload["workspace_references"])
            self.assertTrue(
                any(
                    "shared.device" in item["diff"] and "shared.compute_type" in item["diff"]
                    for item in payload["settings"]["items"]
                )
            )
            self.assertEqual((source / ".venv" / "Scripts" / "python.exe").read_bytes(), old_runtime)
            self.assertFalse((destination / ".gui").exists())
            self.assertNotIn("h264_nvenc", plan.to_json())

    def test_apply_adjusts_cpu_settings_registers_workspace_and_never_cleans_legacy_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, destination = self._fixture(Path(temporary))
            media = (source / "video_import" / "capture.mp4").read_bytes()
            output = (source / "video_export" / "render.mp4").read_bytes()
            plan = build_installer_migration_plan(
                InstallerMigrationRequest(
                    source=str(source),
                    destination=str(destination),
                    cuda=False,
                    nvenc=False,
                )
            )
            result = apply_installer_migration(plan)
            config = json.loads((destination / ".gui" / "runtime_config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["shared"]["device"], "cpu")
            self.assertEqual(config["shared"]["compute_type"], "int8")
            self.assertEqual(config["craig_pipeline"]["video_codec"], "libx264")
            self.assertEqual(
                json.loads((destination / "assets" / "speaker_colors.json").read_text(encoding="utf-8"))["speakers"]["speaker-a"],
                "#12ABEF",
            )
            registry = json.loads((destination / ".gui" / "legacy_workspaces.json").read_text(encoding="utf-8"))
            self.assertEqual(registry["workspaces"][0]["path"], str(source.resolve()))
            self.assertTrue(result["cleanup_candidates"])
            self.assertTrue(all(entry["state"] == "removable_after_success" for entry in result["cleanup_candidates"]))
            self.assertEqual((source / "video_import" / "capture.mp4").read_bytes(), media)
            self.assertEqual((source / "video_export" / "render.mp4").read_bytes(), output)
            self.assertTrue((source / ".venv").exists())
            self.assertFalse((destination / ".venv").exists())
            self.assertTrue(Path(result["record_path"]).is_file())

    def test_plan_rejects_source_directory_link_before_snapshot_walk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, destination = self._fixture(Path(temporary))
            external = Path(temporary) / "external-source"
            external.mkdir()
            (external / "outside.json").write_text('{"outside": true}', encoding="utf-8")
            link = source / ".gui" / "dictionaries"
            try:
                link.symlink_to(external, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlink is unavailable: {exc}")

            with self.assertRaisesRegex(MigrationError, "symbolic link or junction"):
                build_installer_migration_plan(
                    InstallerMigrationRequest(source=str(source), destination=str(destination))
                )

    def test_plan_rejects_destination_parent_link_before_snapshot_walk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, destination = self._fixture(Path(temporary))
            external = Path(temporary) / "external-destination"
            external.mkdir()
            link = destination / ".gui"
            try:
                link.symlink_to(external, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlink is unavailable: {exc}")

            with self.assertRaisesRegex(MigrationError, "symbolic link or junction"):
                build_installer_migration_plan(
                    InstallerMigrationRequest(source=str(source), destination=str(destination))
                )

    @unittest.skipUnless(os.name == "nt", "Windows junction verification is Windows-only")
    def test_plan_rejects_windows_junction_before_snapshot_walk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, destination = self._fixture(Path(temporary))
            external = Path(temporary) / "external-junction-target"
            external.mkdir()
            (external / "outside.json").write_text('{"outside": true}', encoding="utf-8")
            junction = source / ".gui" / "dictionaries"
            created = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(external)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stderr + created.stdout)
            try:
                with self.assertRaisesRegex(MigrationError, "symbolic link or junction"):
                    build_installer_migration_plan(
                        InstallerMigrationRequest(source=str(source), destination=str(destination))
                    )
            finally:
                subprocess.run(
                    ["cmd.exe", "/d", "/c", "rmdir", str(junction)],
                    capture_output=True,
                    check=False,
                )

    def test_skip_workspace_reference_does_not_copy_project_or_media(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, destination = self._fixture(Path(temporary))
            plan = build_installer_migration_plan(
                InstallerMigrationRequest(
                    source=str(source),
                    destination=str(destination),
                    workspace_reference=False,
                )
            )
            result = apply_installer_migration(plan)
            self.assertEqual(result["workspace_references"], [])
            self.assertFalse((destination / ".gui" / "legacy_workspaces.json").exists())
            self.assertFalse((destination / "video_import").exists())
            self.assertFalse((destination / "video_export").exists())

    def test_overwrite_requires_explicit_confirmation_and_cli_plan_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, destination = self._fixture(Path(temporary))
            existing = destination / ".gui" / "runtime_config.json"
            existing.parent.mkdir(parents=True)
            existing.write_text(json.dumps({"shared": {"language": "keep"}}), encoding="utf-8")
            request = InstallerMigrationRequest(
                source=str(source),
                destination=str(destination),
                overwrite=True,
                confirm=False,
            )
            plan = build_installer_migration_plan(request)
            config_item = next(item for item in plan.settings.items if item.relative_path == ".gui/runtime_config.json")
            self.assertEqual(config_item.reason, "confirmation_required")
            with self.assertRaisesRegex(ValueError, "confirmation"):
                apply_installer_migration(plan)
            self.assertEqual(json.loads(existing.read_text(encoding="utf-8"))["shared"]["language"], "keep")

    def test_invalid_registry_is_rejected_before_settings_are_applied(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, destination = self._fixture(Path(temporary))
            registry = destination / ".gui" / "legacy_workspaces.json"
            registry.parent.mkdir(parents=True)
            registry.write_text("not-json", encoding="utf-8")
            plan = build_installer_migration_plan(
                InstallerMigrationRequest(source=str(source), destination=str(destination))
            )
            with self.assertRaisesRegex(ValueError, "invalid legacy workspace registry"):
                apply_installer_migration(plan)
            self.assertFalse((destination / ".gui" / "runtime_config.json").exists())

    def test_cli_plan_and_apply_publish_json_without_exposing_setting_values(self) -> None:
        from src.installer_migration_entrypoint import main

        with tempfile.TemporaryDirectory() as temporary:
            source, destination = self._fixture(Path(temporary))
            plan_path = destination / ".local" / "migration" / "plan.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    main(
                        [
                            "--source",
                            str(source),
                            "--destination",
                            str(destination),
                            "--plan-output",
                            str(plan_path),
                        ]
                    ),
                    0,
                )
            self.assertTrue(plan_path.is_file())
            decoded = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertIn("cleanup", decoded)
            self.assertRegex(decoded["plan_sha256"], r"^[0-9a-f]{64}$")
            self.assertNotIn("#12ABEF", output.getvalue())
            self.assertIn('"cuda": false', output.getvalue())

            result_path = destination / ".local" / "migration" / "latest-result.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "--source",
                            str(source),
                            "--destination",
                            str(destination),
                            "--apply",
                            "--confirm",
                            "--plan-input",
                            str(plan_path),
                            "--result-output",
                            str(result_path),
                        ]
                    ),
                    0,
                )
            self.assertTrue(result_path.is_file())

            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    main(
                        [
                            "--source",
                            str(destination),
                            "--destination",
                            str(destination),
                        ]
                    ),
                    2,
                )
            self.assertIn('"error"', output.getvalue())

    def test_cli_apply_rejects_source_drift_against_reviewed_plan(self) -> None:
        from src.installer_migration_entrypoint import main

        with tempfile.TemporaryDirectory() as temporary:
            source, destination = self._fixture(Path(temporary))
            plan_path = destination / ".local" / "migration" / "plan.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "--source",
                            str(source),
                            "--destination",
                            str(destination),
                            "--plan-output",
                            str(plan_path),
                        ]
                    ),
                    0,
                )

            runtime_config = json.loads((source / ".gui" / "runtime_config.json").read_text(encoding="utf-8"))
            runtime_config["shared"]["language"] = "en"
            (source / ".gui" / "runtime_config.json").write_text(
                json.dumps(runtime_config),
                encoding="utf-8",
            )
            result_path = destination / ".local" / "migration" / "latest-result.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    main(
                        [
                            "--source",
                            str(source),
                            "--destination",
                            str(destination),
                            "--apply",
                            "--confirm",
                            "--plan-input",
                            str(plan_path),
                            "--result-output",
                            str(result_path),
                        ]
                    ),
                    2,
                )
            self.assertIn("stale", output.getvalue())
            self.assertFalse((destination / ".gui" / "runtime_config.json").exists())
            self.assertFalse(result_path.exists())

    def test_existing_registry_is_merged_once_and_written_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, destination = self._fixture(Path(temporary))
            registry = destination / ".gui" / "legacy_workspaces.json"
            registry.parent.mkdir(parents=True)
            existing = Path(temporary) / "already-registered"
            registry.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "workspaces": [
                            {"path": str(existing), "resources": [str(existing / "project")]},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            plan = build_installer_migration_plan(
                InstallerMigrationRequest(source=str(source), destination=str(destination))
            )
            apply_installer_migration(plan)
            apply_installer_migration(build_installer_migration_plan(plan.request))
            merged = json.loads(registry.read_text(encoding="utf-8"))
            self.assertEqual(
                {entry["path"] for entry in merged["workspaces"]},
                {str(existing.resolve()), str(source.resolve())},
            )
            self.assertEqual(
                len([entry for entry in merged["workspaces"] if entry["path"] == str(source.resolve())]),
                1,
            )


if __name__ == "__main__":
    unittest.main()
