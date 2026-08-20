from __future__ import annotations

import csv

import pytest

from src.subtitle_export import SubtitleExportError, export_csv, export_srt, export_vtt


def _segments():
    return [
        {"id": "b", "start": 2.0, "end": 3.25, "speaker": "keru", "text": '日本語, "引用"\n改行'},
        {"id": "a", "start": 0.0, "end": 1.005, "speaker": "yuki", "text": "先頭"},
    ]


def test_srt_and_vtt_sort_and_preserve_text(tmp_path):
    srt = tmp_path / "字幕 日本語.srt"
    vtt = tmp_path / "字幕 日本語.vtt"
    export_srt(_segments(), srt)
    export_vtt(_segments(), vtt)
    srt_text = srt.read_text(encoding="utf-8")
    vtt_text = vtt.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:01,005" in srt_text
    assert "00:00:00.000 --> 00:00:01.005" in vtt_text
    assert srt_text.index("先頭") < srt_text.index("日本語")
    assert '日本語, "引用"\n改行' in srt_text


def test_csv_uses_stable_columns_and_escaping(tmp_path):
    destination = tmp_path / "字幕.csv"
    export_csv(_segments(), destination)
    with destination.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == ["id", "start", "end", "speaker", "text"]
    assert rows[0]["id"] == "a"
    assert rows[1]["text"] == '日本語, "引用"\n改行'


def test_export_refuses_implicit_overwrite(tmp_path):
    destination = tmp_path / "existing.srt"
    destination.write_text("original", encoding="utf-8")
    with pytest.raises(SubtitleExportError):
        export_srt(_segments(), destination)
    assert destination.read_text(encoding="utf-8") == "original"
    export_srt(_segments(), destination, overwrite=True)
    assert destination.read_text(encoding="utf-8") != "original"
