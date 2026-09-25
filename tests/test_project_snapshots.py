from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.project_recovery import RecoveryJournal, RecoveryError
from src.data_boundary import decode_json, is_object_mapping
from src.project_snapshots import (
    SnapshotStore,
    SnapshotChange,
    SnapshotError,
    sanitize_project,
    _merge_media_references,
)


def _project() -> dict[object, object]:
    return {
        "revision": 3,
        "title": "字幕",
        "source": {"path": "C:/動画/recording.mkv", "name": "recording"},
        "api_key": "do-not-store",
        "settings": {"font": "Noto Sans JP", "video_path": "C:/動画/recording.mkv"},
    }


def _mapping(value: object) -> dict[object, object]:
    if not is_object_mapping(value):
        raise AssertionError("辞書を保持する必要があります")
    return dict(value)


class ProjectSnapshotTests(unittest.TestCase):
    def test_snapshot_excludes_media_and_secrets_and_validates_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = SnapshotStore(root / "snapshots")
            snapshot = store.create(_project(), 3, "before-bulk-edit")
            self.assertTrue(store.validate(snapshot.snapshot_id))
            restored = store.restore(snapshot.snapshot_id)
            self.assertNotIn("api_key", restored)
            self.assertNotIn("path", _mapping(restored["source"]))
            self.assertNotIn("video_path", _mapping(restored["settings"]))
            self.assertEqual(restored["title"], "字幕")

            file_path = root / "snapshots" / f"snapshot-{snapshot.snapshot_id}.json"
            payload = _mapping(decode_json(file_path.read_text(encoding="utf-8")))
            corrupted_project = _mapping(payload["project"])
            corrupted_project["title"] = "破損"
            payload["project"] = corrupted_project
            file_path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertFalse(store.validate(snapshot.snapshot_id))
            self.assertFalse(store.list())

    def test_restore_relinks_current_media_references_without_storing_them(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = SnapshotStore(root / "snapshots")
            current = _project()
            snapshot = store.create(current, 3, "before-edit")
            destination = root / "restored.subtitle-project.json"

            restored = store.restore(
                snapshot.snapshot_id,
                current_project=current,
                current_revision=4,
                destination=destination,
                overwrite=True,
            )

            self.assertEqual(_mapping(restored["source"])["path"], _mapping(current["source"])["path"])
            self.assertEqual(_mapping(restored["settings"])["video_path"], _mapping(current["settings"])["video_path"])
            written = _mapping(decode_json(destination.read_text(encoding="utf-8")))
            self.assertEqual(_mapping(written["source"])["path"], _mapping(current["source"])["path"])
            self.assertEqual(_mapping(written["settings"])["video_path"], _mapping(current["settings"])["video_path"])

    def test_restore_creates_pre_restore_snapshot_and_diff(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SnapshotStore(Path(temp_dir) / "snapshots")
            original = store.create({"title": "original"}, 1, "manual")
            changed = store.create({"title": "changed"}, 2, "manual")
            expected_changes: list[SnapshotChange] = [{"path": "/title", "before": "original", "after": "changed"}]
            self.assertEqual(store.diff(original.snapshot_id, changed.snapshot_id), expected_changes)
            expected = {"title": "original"}
            self.assertEqual(
                store.restore(original.snapshot_id, current_project={"title": "current"}, current_revision=3),
                expected,
            )
            self.assertTrue(any(item.reason == "pre-restore" for item in store.list()))

    def test_retention_keeps_pinned_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SnapshotStore(
                Path(temp_dir) / "snapshots",
                retention_count=1,
                retention_days=None,
                retention_bytes=None,
            )
            first = store.create({"n": 1}, 1, "manual", pinned=True)
            store.create({"n": 2}, 2, "manual")
            store.create({"n": 3}, 3, "manual")
            ids = {item.snapshot_id for item in store.list()}
            self.assertIn(first.snapshot_id, ids)
            self.assertEqual(len(ids), 2)

    def test_restore_loads_target_before_pre_restore_pruning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SnapshotStore(
                Path(temp_dir) / "snapshots",
                retention_count=1,
                retention_days=None,
                retention_bytes=None,
            )
            target = store.create({"title": "target"}, 1, "manual")
            expected = {"title": "target"}
            self.assertEqual(store.restore(target.snapshot_id, current_project={"title": "current"}), expected)

    def test_recovery_only_returns_newer_state_and_clears_after_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = RecoveryJournal(Path(temp_dir) / "recovery")
            journal.record(_project(), 5)
            self.assertIsNone(journal.candidate(5))
            candidate = journal.candidate(4)
            if candidate is None:
                self.fail("新しい復旧候補が必要です")
            self.assertEqual(candidate["revision"], 5)
            restored = journal.restore_if_newer(_project(), 4)
            if restored is None:
                self.fail("新しい復旧候補を復元する必要があります")
            self.assertEqual(restored["title"], "字幕")
            self.assertEqual(_mapping(restored["source"])["path"], _mapping(_project()["source"])["path"])
            self.assertEqual(
                _mapping(restored["settings"])["video_path"], _mapping(_project()["settings"])["video_path"]
            )
            self.assertNotIn("api_key", restored)
            self.assertIsNone(journal.pending())
            with self.assertRaises(RecoveryError):
                journal.path.write_text("{}", encoding="utf-8")
                journal.pending()

    def test_non_object_snapshot_json_is_invalid_and_does_not_break_listing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = SnapshotStore(root, retention_days=None)
            valid = store.create({"title": "valid"}, 1, "manual")
            for encoded in ("null", "[]", '"text"', "3", "true", "{"):
                with self.subTest(encoded=encoded):
                    (root / "snapshot-broken.json").write_text(encoded, encoding="utf-8")
                    self.assertFalse(store.validate("broken"))
                    with self.assertRaises(SnapshotError):
                        store.get("broken")
                    ids = [snapshot.snapshot_id for snapshot in store.list()]
                    expected_ids = [valid.snapshot_id]
                    self.assertEqual(ids, expected_ids)
                    store.prune()

    def test_recovery_invalid_json_is_reported_without_erasing_pending_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            journal = RecoveryJournal(temporary)
            for encoded in ("null", "[]", '"text"', "3", "true", "{", "{}"):
                with self.subTest(encoded=encoded):
                    journal.path.write_text(encoded, encoding="utf-8")
                    with self.assertRaises(RecoveryError):
                        journal.restore_if_newer({}, 1)
                    self.assertEqual(journal.path.read_text(encoding="utf-8"), encoded)
            journal.record({"title": "original"}, 2)
            payload = _mapping(decode_json(journal.path.read_text(encoding="utf-8")))
            payload["project"] = {"title": "tampered"}
            journal.path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(RecoveryError, "checksum mismatch"):
                journal.candidate(1)
            self.assertTrue(journal.path.exists())

    def test_sanitization_and_media_merge_keep_recursive_contract(self) -> None:
        original: dict[object, object] = {
            "title": "stored",
            "api-key": "secret",
            "items": ({"path": "old.mp4", "text": "stored", "access_token": "secret"},),
        }
        clean = sanitize_project(original)
        expected: dict[object, object] = {"title": "stored", "items": [{"text": "stored"}]}
        self.assertEqual(clean, expected)
        current: dict[object, object] = {
            "title": "live",
            "items": [{"path": "new.mp4", "text": "live", "password": "secret"}],
        }
        restored = _merge_media_references(clean, current)
        expected_restored: dict[object, object] = {
            "title": "stored",
            "items": [{"path": "new.mp4", "text": "stored"}],
        }
        self.assertEqual(restored, expected_restored)
        self.assertEqual(clean, expected)
        self.assertIn("api-key", original)

    def test_diff_keeps_nested_order_and_opaque_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = SnapshotStore(temporary)
            revision = {"branch": "draft", "number": 3}
            first = store.create({"settings": {"z": 1, "a": 2}}, revision, "manual")
            second = store.create({"settings": {"z": 3, "a": 4}}, None, "manual")
            self.assertEqual(store.get(first.snapshot_id).revision, revision)
            changes = store.diff(first.snapshot_id, second.snapshot_id)
            expected_changes: list[SnapshotChange] = [
                {"path": "/settings/a", "before": 2, "after": 4},
                {"path": "/settings/z", "before": 1, "after": 3},
            ]
            self.assertEqual(changes, expected_changes)
            pinned = store.pin(first.snapshot_id)
            self.assertTrue(pinned.pinned)
            self.assertEqual(pinned.revision, revision)

    def test_restore_refuses_to_overwrite_destination_and_keeps_existing_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = SnapshotStore(root / "snapshots")
            snapshot = store.create({"title": "snapshot"}, 1, "manual")
            destination = root / "existing.json"
            destination.write_text("existing", encoding="utf-8")
            with self.assertRaisesRegex(SnapshotError, "already exists"):
                store.restore(snapshot.snapshot_id, destination=destination)
            self.assertEqual(destination.read_text(encoding="utf-8"), "existing")

    def test_retention_days_and_bytes_keep_pinned_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            now = datetime.now(timezone.utc)
            old_date = (now - timedelta(days=3)).isoformat()
            store = SnapshotStore(temporary, retention_days=None, retention_bytes=None)
            pinned = store.create({"title": "pinned"}, 1, "manual", pinned=True, created_at=old_date)
            expired = store.create({"title": "expired"}, 2, "manual", created_at=old_date)
            recent = store.create({"title": "recent"}, 3, "manual", created_at=now.isoformat())
            store.retention_days = 1
            store.prune(now=now)
            self.assertFalse(store.validate(expired.snapshot_id))
            self.assertTrue(store.validate(recent.snapshot_id))
            store.retention_bytes = 0
            store.prune(now=now)
            self.assertFalse(store.validate(recent.snapshot_id))
            self.assertTrue(store.validate(pinned.snapshot_id))

    def test_recovery_revision_parsing_preserves_numeric_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            journal = RecoveryJournal(temporary)
            journal.record({}, "5")
            self.assertIsNotNone(journal.candidate("4"))
            self.assertIsNone(journal.candidate(5.9))
            invalid_values: tuple[object, ...] = (True, None, "invalid", {}, float("nan"))
            for value in invalid_values:
                with self.subTest(value=value):
                    journal.record({}, "5")
                    self.assertIsNone(journal.candidate(value))
                    journal.record({}, value)
                    self.assertIsNone(journal.candidate(0))
            self.assertTrue(journal.path.exists())

    def test_string_keyed_project_api_is_typed_without_widening_or_casts(self) -> None:
        project: dict[str, object] = {"title": "字幕"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = SnapshotStore(root / "snapshots")
            snapshot = store.create(project, 1, "manual")
            restored = store.restore(snapshot.snapshot_id, current_project=project)
            self.assertEqual(restored, project)
            journal = RecoveryJournal(root / "recovery")
            journal.record(project, 2)
            self.assertEqual(journal.restore_if_newer(project, 1), project)
