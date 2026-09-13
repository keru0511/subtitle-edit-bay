from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from src.legacy_migration import (
    CATEGORY_CACHE,
    CATEGORY_LEGACY_RUNTIME,
    CATEGORY_MEDIA,
    CATEGORY_OUTPUT,
    CATEGORY_PROJECT,
    CATEGORY_RUNTIME_CONFIG,
    CATEGORY_SPEAKER_COLORS,
    CATEGORY_USER_SETTINGS,
    SCOPE_EXTERNAL,
    STATUS_NOT_DISCOVERABLE,
    STATUS_MISSING,
    STATUS_UNSAFE,
    build_legacy_inventory,
    build_legacy_migration_plan,
)


class LegacyMigrationInventoryTests(unittest.TestCase):
    def _fixture(self, root: Path) -> Path:
        legacy = root / "legacy"
        (legacy / ".gui").mkdir(parents=True)
        (legacy / "assets").mkdir()
        (legacy / "video_import").mkdir()
        (legacy / "video_export").mkdir()
        (legacy / "out").mkdir()
        (legacy / "cache").mkdir()
        (legacy / ".venv" / "Scripts").mkdir(parents=True)
        (legacy / "setup.bat").write_text("not executed", encoding="utf-8")
        (legacy / "start.bat").write_text("not executed", encoding="utf-8")
        (legacy / ".gui" / "runtime_config.json").write_text(
            json.dumps({"shared": {"device": "cpu", "api_token": "must not be read"}}),
            encoding="utf-8",
        )
        (legacy / "assets" / "speaker_colors.json").write_text("{}", encoding="utf-8")
        (legacy / "video_import" / "capture.mp4").write_bytes(b"media")
        (legacy / "video_export" / "render.mp4").write_bytes(b"rendered")
        (legacy / "out" / "subtitle.ass").write_bytes(b"subtitle")
        (legacy / "cache" / "model.bin").write_bytes(b"cache")
        (legacy / ".venv" / "Scripts" / "python.exe").write_bytes(b"legacy python")
        (legacy / "episode.seb-project.json").write_text("{}", encoding="utf-8")
        return legacy

    def _entry(self, inventory, category: str, relative: str):
        return next(
            entry
            for entry in inventory.entries
            if entry.category == category and entry.relative_path == relative
        )

    def test_inventory_describes_known_candidates_without_reading_or_executing_legacy_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            legacy = self._fixture(Path(temporary))
            before = {
                path: path.read_bytes()
                for path in legacy.rglob("*")
                if path.is_file() and not path.is_symlink()
            }

            with patch("subprocess.run", side_effect=AssertionError("legacy runtime was executed")):
                inventory = build_legacy_inventory(legacy)

            runtime = self._entry(inventory, CATEGORY_LEGACY_RUNTIME, ".venv")
            self.assertTrue(runtime.exists)
            self.assertEqual(runtime.node_type, "directory")
            self.assertEqual(runtime.size_bytes, len(b"legacy python"))

            config = self._entry(inventory, CATEGORY_RUNTIME_CONFIG, ".gui/runtime_config.json")
            self.assertTrue(config.exists)
            self.assertEqual(config.size_bytes, len((legacy / ".gui" / "runtime_config.json").read_bytes()))
            self.assertNotIn("must not be read", inventory.to_json())

            self.assertTrue(self._entry(inventory, CATEGORY_SPEAKER_COLORS, "assets/speaker_colors.json").exists)
            self.assertTrue(self._entry(inventory, CATEGORY_MEDIA, "video_import").exists)
            self.assertTrue(self._entry(inventory, CATEGORY_OUTPUT, "video_export").exists)
            self.assertTrue(self._entry(inventory, CATEGORY_OUTPUT, "out").exists)
            self.assertTrue(self._entry(inventory, CATEGORY_CACHE, "cache").exists)
            self.assertTrue(self._entry(inventory, CATEGORY_PROJECT, "episode.seb-project.json").exists)
            self.assertEqual(before, {
                path: path.read_bytes()
                for path in legacy.rglob("*")
                if path.is_file() and not path.is_symlink()
            })

    def test_inventory_and_plan_are_immutable_and_json_serializable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            legacy = self._fixture(Path(temporary))
            inventory = build_legacy_inventory(legacy)
            with self.assertRaises(FrozenInstanceError):
                inventory.legacy_root = "changed"  # type: ignore[misc]
            with self.assertRaises(TypeError):
                inventory.entries[0] = inventory.entries[0]  # type: ignore[index]

            plan = build_legacy_migration_plan(legacy)
            self.assertEqual(len(plan.actions), len(inventory.entries))
            self.assertTrue(all(action.operation == "inspect_only" for action in plan.actions))
            self.assertTrue(all(not action.destructive for action in plan.actions))
            decoded = json.loads(plan.to_json())
            self.assertEqual(decoded["legacy_root"], str(legacy.resolve()))
            self.assertEqual(decoded["actions"][0]["operation"], "inspect_only")

    def test_external_cache_capabilities_are_not_misclassified_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inventory = build_legacy_inventory(self._fixture(Path(temporary)))

            external = {entry.candidate_id: entry for entry in inventory.external_cache_entries}
            self.assertEqual(
                set(external),
                {
                    "pip-user-cache",
                    "pytorch-user-cache",
                    "huggingface-user-cache",
                    "application-user-cache",
                },
            )
            for candidate_id, entry in external.items():
                self.assertEqual(entry.scope, SCOPE_EXTERNAL)
                self.assertEqual(entry.status, STATUS_NOT_DISCOVERABLE, candidate_id)
                self.assertFalse(entry.exists)
                self.assertTrue(entry.safe)
                self.assertEqual(entry.path, "")
                self.assertIn("not treated as missing", entry.diagnostic or "")

            decoded = json.loads(inventory.to_json())
            external_json = [
                item for item in decoded["entries"] if item["scope"] == SCOPE_EXTERNAL
            ]
            self.assertEqual(
                {item["candidate_id"] for item in external_json},
                set(external),
            )

    def test_missing_root_is_diagnostic_and_does_not_create_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "does-not-exist"
            inventory = build_legacy_inventory(missing)
            self.assertFalse(inventory.root_exists)
            self.assertEqual(len(inventory.external_cache_entries), 4)
            self.assertTrue(all(entry.scope == SCOPE_EXTERNAL for entry in inventory.entries))
            self.assertEqual(inventory.root_status, STATUS_MISSING)
            self.assertFalse(missing.exists())

    def test_symlink_candidate_is_not_followed_outside_legacy_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = self._fixture(root)
            outside = root / "outside-secret"
            outside.mkdir()
            (outside / "private.bin").write_bytes(b"private")
            link = legacy / "video_import" / "outside-link"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symbolic links are unavailable: {exc}")

            inventory = build_legacy_inventory(legacy)
            media = self._entry(inventory, CATEGORY_MEDIA, "video_import")
            self.assertEqual(media.size_bytes, len(b"media"))
            self.assertIn("symbolic link or junction skipped", media.diagnostic or "")
            self.assertNotIn("private.bin", inventory.to_json())

    def test_symlink_root_is_not_traversed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = self._fixture(root)
            linked_root = root / "linked-legacy"
            try:
                linked_root.symlink_to(legacy, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symbolic links are unavailable: {exc}")

            inventory = build_legacy_inventory(linked_root)
            self.assertEqual(inventory.root_status, STATUS_UNSAFE)
            self.assertFalse(inventory.root_safe)
            self.assertEqual(len(inventory.external_cache_entries), 4)
            self.assertTrue(all(entry.scope == SCOPE_EXTERNAL for entry in inventory.entries))
            self.assertTrue(any("not followed" in diagnostic for diagnostic in inventory.diagnostics))

    def test_malformed_path_is_safe_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            malformed = str(Path(temporary) / "legacy") + "\x00invalid"
            inventory = build_legacy_inventory(malformed)
            self.assertFalse(inventory.root_exists)
            self.assertFalse(inventory.root_safe)
            self.assertTrue(inventory.diagnostics)
            json.dumps(inventory.to_dict())

    def test_unreadable_directory_is_reported_without_following_or_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            legacy = self._fixture(Path(temporary))
            permission_target = legacy / "cache"
            original_scandir = os.scandir

            def guarded_scandir(path):
                if Path(path) == permission_target:
                    raise PermissionError("fixture permission denied")
                return original_scandir(path)

            with patch("src.legacy_migration.os.scandir", side_effect=guarded_scandir):
                inventory = build_legacy_inventory(legacy)

            cache = self._entry(inventory, CATEGORY_CACHE, "cache")
            self.assertTrue(cache.exists)
            self.assertEqual(cache.size_bytes, 0)
            self.assertIn("unable to enumerate directory", cache.diagnostic or "")
            self.assertFalse((permission_target / "new-file").exists())


if __name__ == "__main__":
    unittest.main()
