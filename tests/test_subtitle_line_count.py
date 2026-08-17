import unittest

from src.subtitle_line_count import (
    format_segment_text,
    normalize_subtitle_line_count,
    pack_segments_with_line_count,
    segment_editor_text,
    segment_preview_text,
)
from src.subtitle_packer import pack_segments as legacy_pack_segments
from src.subtitle_project import SubtitleProjectError, create_project


class SubtitleLineCountTests(unittest.TestCase):
    def test_project_segments_default_to_auto_line_count(self) -> None:
        project = create_project(
            video_path="video.mkv",
            output_dir="out",
            segments=[{"start": 0, "end": 1, "text": "字幕", "speaker": "Oz"}],
        )

        segment = project["segments"][0]
        self.assertEqual(segment["subtitle_line_count"], "auto")
        self.assertFalse(segment["manual_line_count"])

    def test_project_rejects_invalid_line_count(self) -> None:
        with self.assertRaises(SubtitleProjectError):
            create_project(
                video_path="video.mkv",
                output_dir="out",
                segments=[
                    {
                        "start": 0,
                        "end": 1,
                        "text": "字幕",
                        "speaker": "Oz",
                        "subtitle_line_count": "3",
                    }
                ],
            )

    def test_project_preserves_subtitle_line_count_and_manual_flag(self) -> None:
        one_line = create_project(
            video_path="video.mkv",
            output_dir="out",
            segments=[
                {
                    "start": 0,
                    "end": 1,
                    "text": "alpha beta gamma delta",
                    "speaker": "Oz",
                    "max_width": 8,
                    "subtitle_line_count": "1",
                    "manual_line_count": True,
                }
            ],
        )
        two_line = create_project(
            video_path="video.mkv",
            output_dir="out",
            segments=[
                {
                    "start": 0,
                    "end": 1,
                    "text": "short",
                    "speaker": "Oz",
                    "max_width": 24,
                    "subtitle_line_count": 2,
                }
            ],
        )

        self.assertEqual(one_line["segments"][0]["subtitle_line_count"], "1")
        self.assertEqual(one_line["segments"][0]["layout_row_span"], 1)
        self.assertTrue(one_line["segments"][0]["manual_line_count"])
        self.assertEqual(two_line["segments"][0]["subtitle_line_count"], "2")
        self.assertEqual(two_line["segments"][0]["layout_row_span"], 2)
        self.assertTrue(two_line["segments"][0]["manual_line_count"])

    def test_auto_line_count_matches_legacy_packing(self) -> None:
        segment = {
            "start": 0,
            "end": 3,
            "text": "alpha beta gamma delta",
            "speaker": "Oz",
            "layout_packed": True,
            "max_width": 10,
        }

        legacy = legacy_pack_segments({"segments": [segment]})[0]
        current = pack_segments_with_line_count(
            {"segments": [{**segment, "subtitle_line_count": "auto"}]}
        )[0]

        self.assertEqual(current.text, legacy.text)
        self.assertEqual(current.metadata["subtitle_line_count"], "auto")

    def test_one_line_override_keeps_single_visible_line(self) -> None:
        event = pack_segments_with_line_count(
            {
                "segments": [
                    {
                        "start": 0,
                        "end": 3,
                        "text": "alpha beta gamma delta",
                        "speaker": "Oz",
                        "layout_packed": True,
                        "max_width": 8,
                        "subtitle_line_count": "1",
                    }
                ]
            }
        )[0]

        self.assertNotIn(r"\N", event.text)
        self.assertEqual(event.metadata["subtitle_line_count"], "1")

    def test_two_line_override_uses_existing_two_line_normalizer(self) -> None:
        event = pack_segments_with_line_count(
            {
                "segments": [
                    {
                        "start": 0,
                        "end": 3,
                        "text": "alpha beta gamma",
                        "speaker": "Oz",
                        "layout_packed": True,
                        "max_width": 8,
                        "subtitle_line_count": "2",
                    }
                ]
            }
        )[0]

        self.assertIn(r"\N", event.text)
        self.assertLessEqual(event.text.count(r"\N"), 1)
        self.assertEqual(event.metadata["subtitle_line_count"], "2")

    def test_auto_formatting_uses_ass_breaks_and_real_preview_newlines(self) -> None:
        segment = {
            "start": 0,
            "end": 3,
            "text": "alpha beta gamma",
            "speaker": "Oz",
            "layout_packed": True,
            "max_width": 8,
            "subtitle_line_count": "2",
        }

        ass_text = format_segment_text(segment)
        preview_text = segment_preview_text(segment)

        self.assertIn(r"\N", ass_text)
        self.assertNotIn("\n", ass_text)
        self.assertIn("\n", preview_text)
        self.assertEqual(preview_text, ass_text.replace(r"\N", "\n"))
        self.assertEqual(segment_editor_text(segment), preview_text)

    def test_editor_shows_full_source_with_breaks_when_preview_is_truncated(self) -> None:
        segment = {
            "start": 0,
            "end": 3,
            "text": "alpha beta gamma delta epsilon zeta eta theta",
            "speaker": "Oz",
            "layout_packed": True,
            "max_width": 8,
            "subtitle_line_count": "auto",
        }

        self.assertTrue(segment_preview_text(segment).endswith("…"))
        editor_text = segment_editor_text(segment)
        self.assertIn("\n", editor_text)
        self.assertNotIn("…", editor_text)
        self.assertEqual("".join(editor_text.split()), "".join(segment["text"].split()))

    def test_manual_long_segment_preserves_full_text_for_preview_render(self) -> None:
        segment = {
            "start": 0,
            "end": 3,
            "text": "alpha beta gamma delta epsilon zeta eta theta",
            "speaker": "Oz",
            "layout_packed": True,
            "max_width": 8,
            "manual_text": True,
        }

        events = pack_segments_with_line_count(
            {"segments": [segment]},
            subtitle_max_gap_seconds=0.4,
            subtitle_end_padding_seconds=0.08,
            subtitle_min_duration_seconds=0.35,
        )
        self.assertGreaterEqual(len(events), 1)
        self.assertTrue(any("theta" in event.text for event in events))
        self.assertFalse(any("…" in event.text for event in events))

    def test_manual_break_overrides_automatic_formatting_and_layout_span(self) -> None:
        project = create_project(
            video_path="video.mkv",
            output_dir="out",
            segments=[
                {
                    "start": 0,
                    "end": 3,
                    "text": "short first\nshort second",
                    "speaker": "Oz",
                    "max_width": 24,
                    "subtitle_line_count": "1",
                }
            ],
        )
        segment = project["segments"][0]

        self.assertEqual(format_segment_text(segment), r"short first\Nshort second")
        self.assertEqual(segment_preview_text(segment), "short first\nshort second")
        self.assertEqual(segment["layout_row_span"], 2)

    def test_overlong_manual_lines_wrap_and_reserve_every_layout_row(self) -> None:
        project = create_project(
            video_path="video.mkv",
            output_dir="out",
            segments=[
                {
                    "start": 0,
                    "end": 3,
                    "text": "abcdefgh\nijklmnop",
                    "speaker": "Oz",
                    "max_width": 4,
                }
            ],
        )
        segment = project["segments"][0]

        self.assertEqual(format_segment_text(segment), r"abcd\Nefgh\Nijkl\Nmnop")
        self.assertEqual(segment["layout_row_span"], 4)

    def test_literal_backslash_n_is_preserved_as_text(self) -> None:
        segment = {
            "start": 0,
            "end": 2,
            "text": "first\\nsecond",
            "speaker": "Oz",
            "max_width": 24,
        }

        self.assertEqual(format_segment_text(segment), r"first\nsecond")
        self.assertEqual(segment_preview_text(segment), r"first\nsecond")
        self.assertEqual(segment_editor_text(segment), r"first\nsecond")

    def test_line_count_normalizer_accepts_auto_one_and_two_only(self) -> None:
        self.assertEqual(normalize_subtitle_line_count(None), "auto")
        self.assertEqual(normalize_subtitle_line_count(""), "auto")
        self.assertEqual(normalize_subtitle_line_count(1), "1")
        self.assertEqual(normalize_subtitle_line_count("2"), "2")
        with self.assertRaises(ValueError):
            normalize_subtitle_line_count(True)
        with self.assertRaises(ValueError):
            normalize_subtitle_line_count("3")


if __name__ == "__main__":
    unittest.main()
