from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from src.subtitle_export import SubtitleExportError, export_csv, export_srt, export_vtt


def _segments():
    return [
        {"id": "b", "start": 2.0, "end": 3.25, "speaker": "keru", "text": '日本語, "引用"\n改行'},
        {"id": "a", "start": 0.0, "end": 1.005, "speaker": "yuki", "text": "先頭"},
    ]


class SubtitleExportTests(unittest.TestCase):
    def test_srt_and_vtt_sort_and_preserve_text(self):
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

    def test_csv_uses_stable_columns_and_escaping(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "字幕.csv"
            export_csv(_segments(), destination)
            with destination.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(list(rows[0]), ["id", "start", "end", "speaker", "text"])
            self.assertEqual(rows[0]["id"], "a")
            self.assertEqual(rows[1]["text"], '日本語, "引用"\n改行')

    def test_export_refuses_implicit_overwrite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "existing.srt"
            destination.write_text("original", encoding="utf-8")
            with self.assertRaises(SubtitleExportError):
                export_srt(_segments(), destination)
            self.assertEqual(destination.read_text(encoding="utf-8"), "original")
            export_srt(_segments(), destination, overwrite=True)
            self.assertNotEqual(destination.read_text(encoding="utf-8"), "original")
