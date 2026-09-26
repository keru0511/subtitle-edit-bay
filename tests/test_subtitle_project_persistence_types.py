"""保存境界と波形生成の型付き契約テスト。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.data_boundary import is_object_mapping, is_object_sequence
from src.subtitle_project import (
    SubtitleProjectError,
    build_waveform,
    create_project,
    load_project,
    project_from_transcript,
    project_to_transcript,
    project_to_view_payload,
    resolve_render_output_path,
    save_project,
    validate_project,
)


class SubtitleProjectPersistenceTypeTests(unittest.TestCase):
    def test_read_only_converters_accept_string_key_projects(self) -> None:
        project: dict[str, object] = {"video": {"path": "game.mkv"}, "segments": []}
        view = project_to_view_payload(project)
        self.assertIn("video", view)
        transcript = project_to_transcript(project)
        self.assertEqual(len(transcript["segments"]), 0)

    def test_save_load_keeps_unknown_fields_and_updates_original(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = create_project(
                video_path=root / "game.mkv",
                output_dir=root,
                segments=[
                    {
                        "start": 0,
                        "end": 1,
                        "text": "字幕",
                        "speaker": "Oz",
                        "extension": {"level": 3},
                    }
                ],
            )
            custom_section: dict[str, object] = {"enabled": True}
            project["custom_section"] = custom_section
            project["updated_at"] = "old"
            path = root / "game.subtitle-project.json"
            self.assertEqual(resolve_render_output_path(path, project), root.resolve() / "game.edited.subtitled.mp4")
            self.assertEqual(save_project(path, project), path)
            loaded = load_project(path)

        self.assertNotEqual(project["updated_at"], "old")
        self.assertEqual(loaded["custom_section"], custom_section)
        raw_segments = loaded["segments"]
        if not is_object_sequence(raw_segments) or not raw_segments:
            self.fail("保存した字幕が見つかりません")
        segment = raw_segments[0]
        if not is_object_mapping(segment):
            self.fail("保存した字幕が辞書ではありません")
        expected_extension: dict[str, object] = {"level": 3}
        self.assertEqual(segment["extension"], expected_extension)
        self.assertEqual(project_to_transcript(loaded)["segments"][0]["text"], "字幕")

        view = project_to_view_payload(loaded)
        self.assertNotIn("custom_section", view)
        view_segments = view["segments"]
        if not is_object_sequence(view_segments) or not view_segments:
            self.fail("表示用の字幕が見つかりません")
        view_segment = view_segments[0]
        if not is_object_mapping(view_segment):
            self.fail("表示用の字幕が辞書ではありません")
        self.assertEqual(view_segment["text"], "字幕")

    def test_transcript_import_validates_array_and_keeps_segment_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "transcript.json"
            document: dict[str, object] = {
                "segments": [{"start": "1", "end": "2", "text": "文字起こし", "speaker": "Oz", "extension": "kept"}]
            }
            source.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            project = project_from_transcript(source, video_path=root / "game.mkv")
            raw_segments = project["segments"]
            if not is_object_sequence(raw_segments) or not raw_segments:
                self.fail("取り込んだ字幕が見つかりません")
            segment = raw_segments[0]
            if not is_object_mapping(segment):
                self.fail("取り込んだ字幕が辞書ではありません")
            self.assertEqual(segment["start"], 1.0)
            self.assertEqual(segment["extension"], "kept")

            malformed: dict[str, object] = {"segments": None}
            source.write_text(json.dumps(malformed), encoding="utf-8")
            with self.assertRaisesRegex(SubtitleProjectError, "segments array"):
                project_from_transcript(source, video_path=root / "game.mkv")

    def test_waveform_uses_numpy_array_contract(self) -> None:
        samples = np.asarray([0.0, 0.5, -1.0, 0.25])
        waveform = build_waveform(
            "voice.wav",
            speaker="Oz",
            style="Oz",
            color="#7FD957",
            offset_seconds=0.25,
            samples=samples,
            sample_rate=4,
            bins=2,
        )
        self.assertEqual(waveform["duration_seconds"], 1.0)
        raw_peaks = waveform["peaks"]
        if not is_object_sequence(raw_peaks):
            self.fail("波形ピークが配列ではありません")
        self.assertEqual(len(raw_peaks), 2)
        self.assertTrue(all(isinstance(peak, float) and 0.0 <= peak <= 1.0 for peak in raw_peaks))

    def test_invalid_outline_thickness_keeps_value_type_in_error(self) -> None:
        project = create_project(video_path="game.mkv", segments=[])
        raw_settings = project["subtitle_settings"]
        if not is_object_mapping(raw_settings):
            self.fail("字幕設定が辞書ではありません")
        invalid = dict(raw_settings)
        invalid["outline_thickness"] = None
        project["subtitle_settings"] = invalid
        with self.assertRaisesRegex(SubtitleProjectError, "not 'NoneType'"):
            validate_project(project)
