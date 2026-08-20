from __future__ import annotations

import pytest

from src.timeline_interchange import (
    TimelineInterchangeError,
    export_edl,
    export_timeline_json,
    export_warnings,
    import_timeline_json,
)


def _project():
    return {
        "revision": 7,
        "name": "日本語 project",
        "source": [{"id": "source-1", "path": "C:/素材/録画.mkv"}],
        "clips": [
            {"id": "clip-1", "source_id": "source-1", "source_start": 1.0, "source_end": 4.0, "timeline_start": 0.0, "timeline_end": 3.0}
        ],
        "transitions": [{"type": "dissolve", "duration": 0.25}],
        "subtitles": [{"id": "sub-1", "start": 0.2, "end": 1.0, "text": "字幕"}],
        "audio": [{"id": "track-1", "gain": -3}],
    }


def test_timeline_json_is_lossless_and_versioned(tmp_path):
    destination = tmp_path / "編集 timeline.json"
    export_timeline_json(_project(), destination)
    assert import_timeline_json(destination) == _project()
    with pytest.raises(TimelineInterchangeError):
        export_timeline_json(_project(), destination)


def test_edl_contains_source_and_timeline_timecodes(tmp_path):
    destination = tmp_path / "timeline.edl"
    export_edl(_project(), destination, fps=30)
    text = destination.read_text(encoding="utf-8")
    assert "FCM: NON-DROP FRAME" in text
    assert "00:00:01:00 00:00:04:00 00:00:00:00 00:00:03:00" in text
    assert "C:/素材/録画.mkv" in text
    assert export_warnings(_project()) == []


def test_edl_reports_unrepresentable_features():
    project = _project() | {"transitions": [{"type": "wipe"}]}
    assert "unsupported transition: wipe" in export_warnings(project)
    with pytest.raises(TimelineInterchangeError):
        export_edl(project | {"clips": [{"start": 0}]}, "out.edl")
