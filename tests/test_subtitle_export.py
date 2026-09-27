from __future__ import annotations

import tempfile
from pathlib import Path

from src.subtitle_export import SubtitleExportError, export_csv, export_srt, export_vtt
from tests.typed_case import TypedTestCase


def _segments() -> list[dict[str, object]]:
    return [
        {"id": "b", "start": 2.0, "end": 3.25, "speaker": "keru", "text": '日本語, "引用"\n改行'},
        {"id": "a", "start": 0.0, "end": 1.005, "speaker": "yuki", "text": "先頭"},
    ]


class SubtitleExportTests(TypedTestCase):
    def test_srt_and_vtt_sort_and_preserve_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            srt = tmp_path / "字幕 日本語.srt"
            vtt = tmp_path / "字幕 日本語.vtt"
            export_srt(_segments(), srt)
            export_vtt(_segments(), vtt)
            srt_text = srt.read_text(encoding="utf-8")
            vtt_text = vtt.read_text(encoding="utf-8")
            self.assertIn("00:00:00,000 --> 00:00:01,005", srt_text)
            self.assertIn("00:00:00.000 --> 00:00:01.005", vtt_text)
            self.assertLess(srt_text.index("先頭"), srt_text.index("日本語"))
            self.assertIn('日本語, "引用"\n改行', srt_text)

    def test_csv_uses_stable_columns_and_escaping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "字幕.csv"
            export_csv(_segments(), destination)
            content = destination.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("id,start,end,speaker,text\n"))
            self.assertIn("a,0.0,1.005,yuki,先頭\n", content)
            self.assertIn('b,2.0,3.25,keru,"日本語, ""引用""\n改行"\n', content)
            self.assertLess(content.index("a,0.0,1.005"), content.index("b,2.0,3.25"))

    def test_export_refuses_implicit_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "existing.srt"
            destination.write_text("original", encoding="utf-8")
            with self.assertRaises(SubtitleExportError):
                export_srt(_segments(), destination)
            self.assertEqual(destination.read_text(encoding="utf-8"), "original")
            export_srt(_segments(), destination, overwrite=True)
            self.assertNotEqual(destination.read_text(encoding="utf-8"), "original")

    def test_export_preserves_numeric_times_and_arbitrary_csv_fields(self) -> None:
        segments: list[object] = [
            {"start": "1.5", "end": 2, "text": None, "id": 7, "speaker": None},
            {"start": 1.5, "end": 3, "text": "後続", "id": "later", "speaker": 42},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            export_srt(segments, root / "output.srt")
            export_csv(segments, root / "output.csv")
            srt = (root / "output.srt").read_text(encoding="utf-8")
            csv_text = (root / "output.csv").read_text(encoding="utf-8")
        self.assertIn("1\n00:00:01,500 --> 00:00:02,000\n", srt)
        self.assertLess(srt.index("00:00:02,000"), srt.index("後続"))
        self.assertEqual(csv_text.splitlines()[1], "7,1.5,2.0,,")
        self.assertEqual(csv_text.splitlines()[2], "later,1.5,3.0,42,後続")

    def test_export_rejects_non_mapping_segments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "invalid.srt"
            with self.assertRaisesRegex(SubtitleExportError, "segment 0 must be an object"):
                export_srt([None], destination)
            self.assertFalse(destination.exists())

    def test_export_accepts_buffer_time_values(self) -> None:
        segments: list[object] = [{"start": memoryview(b"1.5"), "end": b"2.0", "text": "字幕"}]
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "buffer.srt"
            export_srt(segments, destination)
            self.assertIn("00:00:01,500 --> 00:00:02,000", destination.read_text(encoding="utf-8"))
