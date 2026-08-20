from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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


class TimelineInterchangeTests(unittest.TestCase):
    def test_timeline_json_is_lossless_and_versioned(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "編集 timeline.json"
            export_timeline_json(_project(), destination)
            self.assertEqual(import_timeline_json(destination), _project())
            payload = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(set(payload), {"schema_version", "project"})
            payload["clips"] = []
            destination.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(TimelineInterchangeError):
                import_timeline_json(destination)
            with self.assertRaises(TimelineInterchangeError):
                export_timeline_json(_project(), destination)

    def test_edl_contains_source_and_timeline_timecodes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "timeline.edl"
            export_edl(_project(), destination, fps=30)
            text = destination.read_text(encoding="utf-8")
            self.assertIn("FCM: NON-DROP FRAME", text)
            self.assertIn("00:00:01:00 00:00:04:00 00:00:00:00 00:00:03:00", text)
            self.assertIn("C:/素材/録画.mkv", text)
            self.assertEqual(export_warnings(_project()), [])

    def test_edl_reports_unrepresentable_features(self):
        project = _project() | {"transitions": [{"type": "wipe"}]}
        self.assertIn("unsupported transition: wipe", export_warnings(project))
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(TimelineInterchangeError):
                export_edl(project | {"clips": [{"start": 0}]}, Path(temp_dir) / "out.edl")

    def test_edl_rejects_non_positive_source_or_timeline_ranges(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for invalid in (
                {"source_start": 4.0, "source_end": 4.0},
                {"timeline_start": 3.0, "timeline_end": 2.0},
            ):
                clip = _project()["clips"][0] | invalid
                with self.assertRaises(TimelineInterchangeError):
                    export_edl(_project() | {"clips": [clip]}, Path(temp_dir) / f"{len(invalid)}.edl")
