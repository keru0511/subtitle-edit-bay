"""字幕行数調整の入力境界とイベント出力を検証する。"""

from __future__ import annotations

import unittest

from src.subtitle_line_count import (
    format_segment_text,
    pack_event_with_line_count,
    pack_segments_with_line_count,
    segment_editor_text,
)
from tests.typed_case import TypedTestCase


class SubtitleLineCountContractTests(TypedTestCase):
    def test_numeric_strings_and_optional_fields_are_preserved(self) -> None:
        segment: dict[str, object] = {
            "text": "字幕",
            "start": "1.25",
            "end": b"2.5",
            "speaker": "Oz",
            "emphasis": "strong",
            "max_width": "28",
            "layout_row": "2",
            "subtitle_line_count": "1",
            "subtitle_font_scale": "1.2",
            "source_stream_id": "stream-1",
        }

        event = pack_event_with_line_count(segment)

        self.assertIsNotNone(event)
        if event is None:
            self.fail("字幕イベントが生成されませんでした")
        self.assertEqual(event.start, 1.25)
        self.assertEqual(event.end, 2.5)
        self.assertEqual(event.speaker, "Oz")
        self.assertEqual(event.emphasis, "strong")
        self.assertEqual(event.layer, 2)
        self.assertEqual(event.metadata["subtitle_line_count"], "1")
        self.assertEqual(event.metadata["subtitle_font_scale"], 1.2)
        self.assertEqual(event.metadata["max_width"], 28)
        self.assertEqual(segment["start"], "1.25")

    def test_already_packed_segment_produces_the_same_visible_text_as_preview(self) -> None:
        segment: dict[str, object] = {
            "start": 0,
            "end": 3,
            "text": "alpha beta gamma",
            "max_width": 8,
            "subtitle_line_count": "2",
            "layout_packed": True,
        }

        events = pack_segments_with_line_count({"segments": [segment]})

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].text, format_segment_text(segment))
        self.assertEqual(segment_editor_text(segment), events[0].text.replace(r"\N", "\n"))

    def test_non_string_speaker_and_emphasis_keep_their_rendered_values(self) -> None:
        cases: tuple[tuple[object, object, str, str], ...] = (
            (None, None, "None", "None"),
            (7, 2, "7", "2"),
            (False, True, "False", "True"),
        )
        for speaker, emphasis, expected_speaker, expected_emphasis in cases:
            segment: dict[str, object] = {
                "text": "字幕",
                "start": 0,
                "end": 1,
                "speaker": speaker,
                "emphasis": emphasis,
            }
            with self.subTest(speaker=speaker, emphasis=emphasis):
                event = pack_event_with_line_count(segment)
                self.assertIsNotNone(event)
                if event is None:
                    self.fail("字幕イベントが生成されませんでした")
                self.assertEqual(event.speaker, expected_speaker)
                self.assertEqual(event.emphasis, expected_emphasis)

    def test_rejects_invalid_payload_containers(self) -> None:
        invalid_payloads: tuple[object, ...] = (None, "字幕", 42, [])
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(TypeError):
                    format_segment_text(payload)
        for segments in (None, "字幕", [42], [{"text": "字幕"}]):
            with self.subTest(segments=segments):
                with self.assertRaises((TypeError, KeyError)):
                    pack_segments_with_line_count({"segments": segments})

    def test_empty_segments_remain_empty(self) -> None:
        self.assertFalse(pack_segments_with_line_count({}))
        self.assertIsNone(pack_event_with_line_count({"text": "  ", "start": 0, "end": 1}))


if __name__ == "__main__":
    unittest.main()
