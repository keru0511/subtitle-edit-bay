from __future__ import annotations

import unittest

from src.data_boundary import is_object_mapping, is_object_sequence
from src.subtitle_project_model import SubtitleProject, migrate_project_payload
from src.subtitle_project_schema import SubtitleProjectError
from tests.typed_case import TypedTestCase


class SubtitleProjectModelTests(TypedTestCase):
    def test_project_model_parses_and_round_trips_payload(self) -> None:
        payload = {
            "schema_version": 1,
            "project_type": "subtitle-edit-project",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:01",
            "video": {"path": "video.mkv"},
            "output_dir": "/tmp/out",
            "audio_sources": [
                {
                    "name": "alice",
                    "style": "Speaker_Alice",
                    "track_key": "craig:alice",
                    "file_name": "1-alice.flac",
                    "path": "/tmp/1-alice.flac",
                    "color": "#445566",
                }
            ],
            "speakers": [
                {
                    "name": "alice",
                    "style": "Speaker_Alice",
                    "track_key": "craig:alice",
                    "file_name": "1-alice.flac",
                    "path": "/tmp/1-alice.flac",
                    "color": "#445566",
                }
            ],
            "waveforms": [
                {
                    "speaker": "Oz",
                    "style": "Oz",
                    "color": "#445566",
                    "source_path": "/tmp/audio.wav",
                    "offset_seconds": 0.2,
                    "duration_seconds": 1.5,
                    "sample_rate": 400,
                    "peaks": [0.1],
                }
            ],
            "subtitle_settings": {"font_size": 64, "outline_color": "#000000", "outline_thickness": 3},
            "render_settings": {},
            "transcription": {},
            "transcription_context": {},
            "segments": [{"start": 0.0, "end": 1.0, "text": "hi", "speaker": "Oz"}],
            "audio_mix": {"version": 1, "customized": False, "channels": []},
        }
        model = SubtitleProject.from_json(payload)
        round_trip = model.to_json()
        video = round_trip["video"]
        segments = round_trip["segments"]
        if not is_object_mapping(video) or not is_object_sequence(segments):
            self.fail("映像と字幕配列を保存する必要があります")
        segment = segments[0]
        if not is_object_mapping(segment):
            self.fail("字幕の辞書を保存する必要があります")
        self.assertEqual(video["path"], "video.mkv")
        self.assertEqual(segment["id"], "subtitle-000001")
        self.assertIsInstance(SubtitleProject.from_json(round_trip), SubtitleProject)

    def test_legacy_migration_preserves_input_and_unknown_data(self) -> None:
        future = ["keep"]
        payload: dict[object, object] = {
            "schema_version": -1,
            "video": {"path": "capture.mp4", "duration_seconds": "3.5"},
            "created_at": "created",
            "updated_at": "updated",
            "future": future,
        }
        migrated = migrate_project_payload(payload)
        self.assertEqual(migrated["schema_version"], 1)
        self.assertEqual(migrated["project_type"], "subtitle-edit-project")
        self.assertEqual(payload["schema_version"], -1)
        self.assertNotIn("project_type", payload)
        model = SubtitleProject.from_json(migrated)
        future.append("changed")
        expected = ["keep"]
        self.assertEqual(model.extras["future"], expected)
        self.assertEqual((model.created_at, model.updated_at), ("created", "updated"))
        self.assertEqual(model.sequence.assets[0].id, "asset-video")
        self.assertEqual(model.sequence.output_duration, 3.5)
        self.assertEqual(SubtitleProject.from_json(model.to_json()).to_json(), model.to_json())

    def test_null_and_unknown_settings_are_preserved(self) -> None:
        nullable = SubtitleProject.from_json(
            {
                "video": {"path": "capture.mp4"},
                "subtitle_settings": None,
                "render_settings": None,
                "transcription": None,
                "transcription_context": None,
            }
        )
        saved = nullable.to_json()
        for field in ("subtitle_settings", "render_settings", "transcription", "transcription_context"):
            self.assertIsNone(saved[field])
        nested = ["keep"]
        model = SubtitleProject.from_json({"video": {"path": "capture.mp4"}, "render_settings": {17: nested}})
        nested.append("changed")
        settings = model.render_settings
        if settings is None:
            self.fail("設定を保持する必要があります")
        expected = ["keep"]
        self.assertEqual(settings[17], expected)
        exported = model.to_json()["render_settings"]
        if not is_object_mapping(exported):
            self.fail("設定の辞書を保存する必要があります")
        self.assertIsNot(exported[17], settings[17])

    def test_collection_filtering_keeps_original_segment_indices(self) -> None:
        model = SubtitleProject.from_json(
            {
                "video": {"path": "capture.mp4"},
                "segments": [None, {"text": "first"}, False, {"text": "second"}],
                "audio_sources": [None, {"name": "source"}],
                "speakers": [0, {"name": "speaker"}],
                "waveforms": ["invalid", {"peaks": [0.5]}],
                "audio_mix": [],
            }
        )
        ids = [segment.id for segment in model.segments]
        expected_ids = ["subtitle-000002", "subtitle-000004"]
        self.assertEqual(ids, expected_ids)
        self.assertEqual(model.audio_sources[0].name, "source")
        self.assertEqual(model.speakers[0].name, "speaker")
        self.assertEqual(model.waveforms[0].peaks[0], 0.5)
        self.assertIsNone(model.audio_mix)
        self.assertNotIn("audio_mix", model.to_json())

    def test_invalid_roots_sections_and_collections_raise_project_errors(self) -> None:
        invalid_roots: tuple[object, ...] = (None, [], "invalid", 1)
        for root in invalid_roots:
            with self.subTest(root=root):
                with self.assertRaisesRegex(SubtitleProjectError, "project root"):
                    SubtitleProject.from_json(root)
        with self.assertRaisesRegex(SubtitleProjectError, "video must be an object"):
            SubtitleProject.from_json({"video": []})
        for field in ("subtitle_settings", "render_settings", "transcription", "transcription_context"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(SubtitleProjectError, field):
                    SubtitleProject.from_json({"video": {"path": "capture.mp4"}, field: []})
        for field in ("segments", "audio_sources", "speakers", "waveforms"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(SubtitleProjectError, field):
                    SubtitleProject.from_json({"video": {"path": "capture.mp4"}, field: None})
        with self.assertRaisesRegex(SubtitleProjectError, "unsupported project schema_version"):
            SubtitleProject.from_json({"schema_version": 2})

    def test_timeline_and_sequence_errors_keep_project_exception(self) -> None:
        for field in ("timeline", "sequence"):
            with self.subTest(field=field):
                with self.assertRaises(SubtitleProjectError):
                    SubtitleProject.from_json({"video": {"path": "capture.mp4"}, field: []})


if __name__ == "__main__":
    unittest.main()
