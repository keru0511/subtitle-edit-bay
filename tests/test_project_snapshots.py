from __future__ import annotations

import json

import pytest

from src.project_recovery import RecoveryJournal, RecoveryError
from src.project_snapshots import SnapshotError, SnapshotStore


def _project():
    return {
        "revision": 3,
        "title": "字幕",
        "source": {"path": "C:/動画/recording.mkv", "name": "recording"},
        "api_key": "do-not-store",
        "settings": {"font": "Noto Sans JP", "video_path": "C:/動画/recording.mkv"},
    }


def test_snapshot_excludes_media_and_secrets_and_validates_checksum(tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    snapshot = store.create(_project(), 3, "before-bulk-edit")
    assert store.validate(snapshot.snapshot_id)
    restored = store.restore(snapshot.snapshot_id)
    assert "api_key" not in restored
    assert "path" not in restored["source"]
    assert "video_path" not in restored["settings"]
    assert restored["title"] == "字幕"

    file_path = tmp_path / "snapshots" / f"snapshot-{snapshot.snapshot_id}.json"
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    payload["project"]["title"] = "破損"
    file_path.write_text(json.dumps(payload), encoding="utf-8")
    assert not store.validate(snapshot.snapshot_id)
    assert store.list() == []


def test_restore_creates_pre_restore_snapshot_and_diff(tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    original = store.create({"title": "original"}, 1, "manual")
    changed = store.create({"title": "changed"}, 2, "manual")
    assert store.diff(original.snapshot_id, changed.snapshot_id) == [{"path": "/title", "before": "original", "after": "changed"}]
    assert store.restore(original.snapshot_id, current_project={"title": "current"}, current_revision=3) == {"title": "original"}
    assert any(item.reason == "pre-restore" for item in store.list())


def test_retention_keeps_pinned_snapshot(tmp_path):
    store = SnapshotStore(tmp_path / "snapshots", retention_count=1, retention_days=None, retention_bytes=None)
    first = store.create({"n": 1}, 1, "manual", pinned=True)
    store.create({"n": 2}, 2, "manual")
    store.create({"n": 3}, 3, "manual")
    ids = {item.snapshot_id for item in store.list()}
    assert first.snapshot_id in ids
    assert len(ids) == 2


def test_recovery_only_returns_newer_state_and_clears_after_restore(tmp_path):
    journal = RecoveryJournal(tmp_path / "recovery")
    journal.record(_project(), 5)
    assert journal.candidate(5) is None
    assert journal.candidate(4)["revision"] == 5
    assert journal.restore_if_newer({"title": "old"}, 4)["title"] == "字幕"
    assert journal.pending() is None
    with pytest.raises(RecoveryError):
        journal.path.write_text("{}", encoding="utf-8")
        journal.pending()
