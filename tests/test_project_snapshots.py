from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.project_recovery import RecoveryJournal, RecoveryError
from src.project_snapshots import SnapshotStore


def _project():
    return {
        "revision": 3,
        "title": "字幕",
        "source": {"path": "C:/動画/recording.mkv", "name": "recording"},
        "api_key": "do-not-store",
        "settings": {"font": "Noto Sans JP", "video_path": "C:/動画/recording.mkv"},
    }


class ProjectSnapshotTests(unittest.TestCase):
    def test_snapshot_excludes_media_and_secrets_and_validates_checksum(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = SnapshotStore(root / "snapshots")
            snapshot = store.create(_project(), 3, "before-bulk-edit")
            self.assertTrue(store.validate(snapshot.snapshot_id))
            restored = store.restore(snapshot.snapshot_id)
            self.assertNotIn("api_key", restored)
            self.assertNotIn("path", restored["source"])
            self.assertNotIn("video_path", restored["settings"])
            self.assertEqual(restored["title"], "字幕")

            file_path = root / "snapshots" / f"snapshot-{snapshot.snapshot_id}.json"
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            payload["project"]["title"] = "破損"
            file_path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertFalse(store.validate(snapshot.snapshot_id))
            self.assertEqual(store.list(), [])

    def test_restore_relinks_current_media_references_without_storing_them(self):
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

            self.assertEqual(restored["source"]["path"], current["source"]["path"])
            self.assertEqual(restored["settings"]["video_path"], current["settings"]["video_path"])
            written = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(written["source"]["path"], current["source"]["path"])
            self.assertEqual(written["settings"]["video_path"], current["settings"]["video_path"])

    def test_restore_creates_pre_restore_snapshot_and_diff(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SnapshotStore(Path(temp_dir) / "snapshots")
            original = store.create({"title": "original"}, 1, "manual")
            changed = store.create({"title": "changed"}, 2, "manual")
            self.assertEqual(
                store.diff(original.snapshot_id, changed.snapshot_id),
                [{"path": "/title", "before": "original", "after": "changed"}],
            )
            self.assertEqual(
                store.restore(original.snapshot_id, current_project={"title": "current"}, current_revision=3),
                {"title": "original"},
            )
            self.assertTrue(any(item.reason == "pre-restore" for item in store.list()))

    def test_retention_keeps_pinned_snapshot(self):
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

    def test_restore_loads_target_before_pre_restore_pruning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SnapshotStore(
                Path(temp_dir) / "snapshots",
                retention_count=1,
                retention_days=None,
                retention_bytes=None,
            )
            target = store.create({"title": "target"}, 1, "manual")
            self.assertEqual(
                store.restore(target.snapshot_id, current_project={"title": "current"}),
                {"title": "target"},
            )

    def test_recovery_only_returns_newer_state_and_clears_after_restore(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = RecoveryJournal(Path(temp_dir) / "recovery")
            journal.record(_project(), 5)
            self.assertIsNone(journal.candidate(5))
            self.assertEqual(journal.candidate(4)["revision"], 5)
            restored = journal.restore_if_newer(_project(), 4)
            self.assertEqual(restored["title"], "字幕")
            self.assertEqual(restored["source"]["path"], _project()["source"]["path"])
            self.assertEqual(restored["settings"]["video_path"], _project()["settings"]["video_path"])
            self.assertNotIn("api_key", restored)
            self.assertIsNone(journal.pending())
            with self.assertRaises(RecoveryError):
                journal.path.write_text("{}", encoding="utf-8")
                journal.pending()
