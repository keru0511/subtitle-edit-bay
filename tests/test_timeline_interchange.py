from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.data_boundary import decode_json, is_object_dict, is_object_mapping, is_object_sequence
from src.timeline_interchange import (
    TimelineInterchangeError,
    build_timeline_document,
    export_edl,
    export_timeline_json,
    export_warnings,
    import_timeline_json,
)


def _project() -> dict[str, object]:
    return {
        "revision": 7,
        "name": "日本語 project",
        "source": [{"id": "source-1", "path": "C:/素材/録画.mkv"}],
        "clips": [
            {
                "id": "clip-1",
                "source_id": "source-1",
                "source_start": 1.0,
                "source_end": 4.0,
                "timeline_start": 0.0,
                "timeline_end": 3.0,
            }
        ],
        "transitions": [{"type": "dissolve", "duration": 0.25}],
        "subtitles": [{"id": "sub-1", "start": 0.2, "end": 1.0, "text": "字幕"}],
        "audio": [{"id": "track-1", "gain": -3}],
    }


class TimelineInterchangeTests(unittest.TestCase):
    def test_document_preserves_unknown_fields_and_does_not_mutate_project(self) -> None:
        project = _project()
        project["extension"] = {"enabled": True}
        document = build_timeline_document(project, revision="review-2")
        saved_project = document["project"]
        if not is_object_mapping(saved_project):
            self.fail("タイムラインのプロジェクトが辞書ではありません")
        expected_extension: dict[str, object] = {"enabled": True}
        self.assertEqual(saved_project["extension"], expected_extension)
        self.assertEqual(saved_project["revision"], "review-2")
        self.assertEqual(project["revision"], 7)

    def test_timeline_json_is_lossless_and_versioned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "編集 timeline.json"
            export_timeline_json(_project(), destination)
            self.assertEqual(import_timeline_json(destination), _project())
            payload = decode_json(destination.read_text(encoding="utf-8"))
            if not is_object_dict(payload):
                self.fail("タイムライン文書が辞書ではありません")
            self.assertEqual(len(payload), 2)
            self.assertIn("schema_version", payload)
            self.assertIn("project", payload)
            payload["clips"] = []
            destination.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(TimelineInterchangeError):
                import_timeline_json(destination)
            with self.assertRaises(TimelineInterchangeError):
                export_timeline_json(_project(), destination)

    def test_import_legacy_document_preserves_defined_fields(self) -> None:
        document: dict[str, object] = {
            "schema_version": 1,
            "revision": "legacy-1",
            "source": [{"id": "source-1"}],
            "clips": [{"id": "clip-1"}],
            "extension": "ignored",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            project = import_timeline_json(path)
        self.assertEqual(project["revision"], "legacy-1")
        expected_source: list[object] = [{"id": "source-1"}]
        expected_clips: list[object] = [{"id": "clip-1"}]
        self.assertEqual(project["source"], expected_source)
        self.assertEqual(project["clips"], expected_clips)
        self.assertNotIn("extension", project)

    def test_edl_contains_source_and_timeline_timecodes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "timeline.edl"
            export_edl(_project(), destination, fps=30)
            text = destination.read_text(encoding="utf-8")
            self.assertIn("FCM: NON-DROP FRAME", text)
            self.assertIn("00:00:01:00 00:00:04:00 00:00:00:00 00:00:03:00", text)
            self.assertIn("C:/素材/録画.mkv", text)
            expected_warnings: list[str] = []
            self.assertEqual(export_warnings(_project()), expected_warnings)

    def test_edl_reports_unrepresentable_features(self) -> None:
        project = _project() | {"transitions": [{"type": "wipe"}]}
        self.assertIn("unsupported transition: wipe", export_warnings(project))
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(TimelineInterchangeError):
                export_edl(project | {"clips": [{"start": 0}]}, Path(temp_dir) / "out.edl")

    def test_edl_accepts_numeric_strings_and_single_source_mapping(self) -> None:
        project = _project()
        project["source"] = {"id": "source-1", "path": "C:/素材/録画.mkv"}
        project["clips"] = [
            {
                "id": "clip-1",
                "source_id": "source-1",
                "source_start": "1.0",
                "source_end": "4.0",
                "timeline_start": "0.0",
                "timeline_end": "3.0",
            }
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "timeline.edl"
            export_edl(project, destination, fps=30)
            text = destination.read_text(encoding="utf-8")
        self.assertIn("00:00:01:00 00:00:04:00 00:00:00:00 00:00:03:00", text)
        self.assertIn("* SOURCE FILE: C:/素材/録画.mkv", text)

    def test_edl_rejects_non_positive_source_or_timeline_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            for invalid in (
                {"source_start": 4.0, "source_end": 4.0},
                {"timeline_start": 3.0, "timeline_end": 2.0},
            ):
                raw_clips = _project()["clips"]
                if not is_object_sequence(raw_clips) or not raw_clips:
                    self.fail("テスト用のクリップがありません")
                first_clip = raw_clips[0]
                if not is_object_mapping(first_clip):
                    self.fail("テスト用のクリップが辞書ではありません")
                clip = dict(first_clip)
                clip.update(invalid)
                with self.assertRaises(TimelineInterchangeError):
                    export_edl(_project() | {"clips": [clip]}, Path(temp_dir) / f"{len(invalid)}.edl")
