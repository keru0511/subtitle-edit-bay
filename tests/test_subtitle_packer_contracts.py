"""字幕パッカーの入力境界・時刻・拡張フィールドの契約を検証する。"""

from __future__ import annotations

import unittest
from copy import deepcopy

from src import subtitle_packer as packer
from tests.typed_case import TypedTestCase


class SubtitlePackerContractTests(TypedTestCase):
    def test_character_timing_skips_incomplete_words_and_limits_long_alignment(self) -> None:
        words: list[dict[str, object]] = [
            {"word": "字幕", "start": "1.0", "end": "9.0"},
            {"word": "", "start": 0, "end": 1},
            {"word": "欠落", "start": None, "end": 1},
            {"word": "逆順", "start": 2, "end": 1},
        ]
        timeline = packer.build_character_timeline(words)
        self.assertEqual(len(timeline), 2)
        self.assertEqual(timeline[0]["start"], 1.0)
        self.assertAlmostEqual(timeline[0]["end"], 1.65)
        self.assertAlmostEqual(timeline[1]["end"], 2.3)
        self.assertFalse(packer.build_character_timeline(None))

    def test_word_gap_split_keeps_original_words_and_extension_references(self) -> None:
        extension = object()
        first: dict[str, object] = {"word": "字幕", "start": "1", "end": "2", "extension": extension}
        second: dict[str, object] = {"word": "確認", "start": "3", "end": "4"}
        words = [first, second]
        segment: dict[str, object] = {"text": "字幕確認", "words": words, "extension": extension}
        groups = packer.split_words_on_gaps(words, 0.5)
        self.assertEqual(len(groups), 2)
        self.assertIs(groups[0][0], first)
        pages = packer.split_segment_by_word_gaps(segment, 0.5)
        self.assertEqual(pages[0]["text"], "字幕")
        self.assertEqual(pages[1]["start"], 3.0)
        self.assertIs(pages[0]["extension"], extension)
        pages[0]["checked"] = True
        groups[0][0]["checked"] = True
        self.assertIs(first["checked"], True)
        self.assertNotIn("checked", segment)
        unchanged = packer.split_segment_by_word_gaps(segment, 2.0)
        self.assertIs(unchanged[0], segment)
        self.assertIs(segment["words"], words)

    def test_gap_boundaries_use_effective_end_but_grouping_uses_original_end(self) -> None:
        words = [
            {"word": "字", "start": 0.0, "end": 2.0},
            {"word": "幕", "start": 2.1, "end": 2.5},
        ]
        expected = {1}
        self.assertEqual(packer.gap_boundary_indices(words, 0.5), expected)
        self.assertEqual(len(packer.split_words_on_gaps(words, 0.5)), 1)

    def test_width_timing_retains_extensions_and_forced_break(self) -> None:
        extension = object()
        segment = {"extension": extension}
        entries: list[packer.AtomicUnitEntry] = [
            {"text": "字幕"},
            {"text": "A", "force_break_before": True},
        ]
        units = packer.build_timed_units_from_width(segment, entries, 1.0, 6.0)
        self.assertEqual(units[0]["start"], 1.0)
        self.assertEqual(units[0]["end"], 5.0)
        self.assertEqual(units[1]["end"], 6.0)
        self.assertIs(units[0]["extension"], extension)
        self.assertIs(units[0]["force_break_before"], False)
        self.assertIs(units[1]["force_break_before"], True)

    def test_word_timing_clips_to_segment_and_can_fall_back_to_width(self) -> None:
        segment = {"words": [{"word": "字幕", "start": 0.0, "end": 1.0}]}
        entries: list[packer.AtomicUnitEntry] = [{"text": "字"}, {"text": "幕"}]
        units = packer.build_timed_units_from_words(segment, entries, 0.2, 0.8)
        self.assertEqual(units[0]["start"], 0.2)
        self.assertEqual(units[1]["end"], 0.8)
        self.assertFalse(packer.build_timed_units_from_words({}, entries, 0.0, 1.0))

    def test_event_coerces_numbers_and_preserves_metadata(self) -> None:
        segment: dict[str, object] = {
            "text": "字幕", "start": "1.25", "end": b"2.5", "speaker": "Oz",
            "emphasis": "strong", "max_width": "28", "layout_row": "2",
            "subtitle_font_scale": "1.2", "source_speaker": "話者1",
        }
        original = deepcopy(segment)
        event = packer.pack_event(segment)
        self.assertIsNotNone(event)
        if event is None:
            self.fail("字幕イベントが生成されませんでした")
        self.assertEqual(event.start, 1.25)
        self.assertEqual(event.end, 2.5)
        self.assertEqual(event.layer, 2)
        self.assertEqual(event.emphasis, "strong")
        self.assertEqual(event.metadata["subtitle_font_scale"], 1.2)
        self.assertEqual(event.metadata["source_speaker"], "話者1")
        self.assertEqual(segment, original)

    def test_already_packed_segments_skip_pagination_and_keep_order(self) -> None:
        segments = [
            {"text": "字幕", "start": 1, "end": 2, "layout_packed": True},
            {"text": "", "layout_packed": True},
            {"text": "確認", "start": 3, "end": 4, "layout_packed": True},
        ]
        events = packer.pack_segments({"segments": segments})
        texts = [event.text for event in events]
        expected = ["字幕", "確認"]
        self.assertEqual(texts, expected)
        self.assertFalse(packer.pack_segments({}))

    def test_invalid_payloads_are_rejected_at_the_boundary(self) -> None:
        invalid_payloads: tuple[object, ...] = (None, "字幕", 42, [])
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(TypeError):
                    packer.pack_event(payload)
        for words in ("字幕", b"text", {"word": "字幕"}, [42]):
            with self.subTest(words=words):
                with self.assertRaises(TypeError):
                    packer.build_character_timeline(words)
        with self.assertRaises(TypeError):
            packer.pack_segments({"segments": None})
        for field in ("text", "speaker", "emphasis"):
            segment: dict[str, object] = {"text": "字幕", "start": 0, "end": 1, field: None}
            with self.subTest(field=field):
                with self.assertRaises(TypeError):
                    packer.pack_event(segment)
        with self.assertRaises(ValueError):
            packer.pack_event({"text": "字幕", "start": "bad", "end": 1})


if __name__ == "__main__":
    unittest.main()
