import unittest

from src.subtitle_line_count import (
    format_segment_text,
    pack_segments_with_line_count,
    segment_editor_text,
    segment_preview_text,
)
from src.subtitle_packer import pack_segment_pages, pack_segments as legacy_pack_segments
from src.subtitle_project import SubtitleProjectError, create_project


class SubtitleLineCountTests(unittest.TestCase):
    def test_automatic_pages_preserve_recognized_text_with_uneven_breaks(self) -> None:
        texts = [
            "少し待ってこの問題が解決しなかったら最初から再開しますか",
            "では次にあの設定で開始できなかったら最初から再開しますか",
            "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほ",
        ]
        for packer in [legacy_pack_segments, pack_segments_with_line_count]:
            for source in texts:
                with self.subTest(packer=packer.__name__, source=source):
                    events = packer({"segments": [{"text": source, "start": 0, "end": 3}]})
                    self.assertEqual("".join(event.text.replace(r"\N", "") for event in events), source)
                    self.assertTrue(all(event.text.count(r"\N") <= 1 for event in events))

    def test_oversized_atomic_unit_preserves_words_and_real_times(self) -> None:
        source = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        words = [
            {"word": char, "start": 10 + index * 0.1, "end": 10 + (index + 1) * 0.1}
            for index, char in enumerate(source)
        ]
        events = pack_segments_with_line_count({"segments": [{
            "text": source, "start": 10, "end": 13.6, "max_width": 8, "words": words,
        }]})
        cursor = 0
        for event in events:
            visible = event.text.replace(r"\N", "")
            self.assertEqual(visible, source[cursor:cursor + len(visible)])
            self.assertAlmostEqual(event.start, words[cursor]["start"])
            cursor += len(visible)
            self.assertGreaterEqual(event.end + 1e-9, words[cursor - 1]["end"])
            self.assertLessEqual(event.end, min(13.6, words[cursor - 1]["end"] + 0.080001))
        self.assertEqual(cursor, len(source))
        self.assertTrue(all(left.end <= right.start for left, right in zip(events, events[1:])))

    def test_split_pages_keep_only_their_aligned_words(self) -> None:
        source = "ABCDEFGHIJKLMNOPQRSTUVWX"
        words = [{"word": char, "start": index * 0.1, "end": (index + 1) * 0.1}
                 for index, char in enumerate(source)]
        pages = pack_segment_pages({"text": source, "start": 0, "end": 2.4, "max_width": 8, "words": words})
        self.assertGreater(len(pages), 1)
        self.assertEqual([word["word"] for page in pages for word in page["words"]], list(source))
        for page in pages:
            self.assertEqual("".join(word["word"] for word in page["words"]), page["text"])
        self.assertEqual(len(words), len(source))

    def test_split_inside_aligned_word_keeps_nonduplicated_fragments(self) -> None:
        source = "ABCDEFGHIJKLMNOPQRSTUVWX"
        word = {"word": source, "start": 0.0, "end": 2.4, "confidence": 0.9}
        pages = pack_segment_pages({"text": source, "start": 0, "end": 2.4, "max_width": 8, "words": [word]})
        self.assertGreater(len(pages), 1)
        self.assertEqual("".join(page["words"][0]["word"] for page in pages), source)
        self.assertTrue(all(page["words"][0]["word"] == page["text"] for page in pages))
        self.assertTrue(all(page["words"][0]["confidence"] == 0.9 for page in pages))
        self.assertTrue(all(left["words"][0]["end"] <= right["words"][0]["start"]
                            for left, right in zip(pages, pages[1:])))
        self.assertEqual(word["word"], source)

    def test_incomplete_alignment_is_not_copied_to_every_page(self) -> None:
        source = "ABCDEFGHIJKLMNOPQRSTUVWX"
        word = {"word": "ABC", "start": 0.0, "end": 0.3}
        pages = pack_segment_pages({"text": source, "start": 0, "end": 2.4, "max_width": 8, "words": [word]})
        self.assertGreater(len(pages), 1)
        self.assertEqual([item["word"] for page in pages for item in page["words"]], ["ABC"])
        self.assertAlmostEqual(pages[-1]["end"], 2.4)

    def test_mismatched_long_word_is_not_assigned_outside_a_page(self) -> None:
        source = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        word = {"word": "ABCDEFGHIJKLMNOPQRSTUVWXYY0123456789", "start": 0.0, "end": 3.6}
        pages = pack_segment_pages({"text": source, "start": 0, "end": 3.6, "max_width": 8, "words": [word]})
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(not page["words"] for page in pages))
        self.assertEqual(word["word"], "ABCDEFGHIJKLMNOPQRSTUVWXYY0123456789")

    def test_split_word_times_stay_inside_segment_bounds(self) -> None:
        source = "字幕確認"
        pages = pack_segment_pages({"text": source, "start": 0.2, "end": 0.8, "max_width": 2,
                                    "words": [{"word": source, "start": 0.0, "end": 1.0}]})
        self.assertGreater(len(pages), 1)
        for page in pages:
            for word in page["words"]:
                self.assertGreaterEqual(word["start"], page["start"])
                self.assertLessEqual(word["end"], page["end"])

    def test_punctuation_missing_from_alignment_keeps_page_word_fragments(self) -> None:
        source = "今日はいい天気。明日も晴れです。"
        words = [
            {"word": "今日はいい天気", "start": 0.0, "end": 1.4},
            {"word": "明日も晴れです", "start": 1.5, "end": 2.8},
        ]
        pages = pack_segment_pages({"text": source, "start": 0, "end": 3, "max_width": 4, "words": words})
        self.assertGreater(len(pages), 2)
        for page in pages:
            self.assertEqual("".join(word["word"] for word in page["words"]), page["text"].replace("。", ""))
        self.assertEqual("".join(word["word"] for page in pages for word in page["words"]), source.replace("。", ""))
        final_spoken_page = next(page for page in pages if "晴れです" in page["text"])
        self.assertAlmostEqual(final_spoken_page["words"][-1]["end"], 2.8)
        self.assertGreaterEqual(final_spoken_page["end"], 2.8)

    def test_automatic_one_line_pages_preserve_text(self) -> None:
        source = "明日の予定を確認してから次の作業を始めましょう"
        for line_count in ("1", " 1 ", 1):
            with self.subTest(line_count=line_count):
                events = pack_segments_with_line_count({"segments": [{
                    "text": source, "start": 0, "end": 5, "max_width": 12, "subtitle_line_count": line_count,
                }]})
                self.assertEqual("".join(event.text for event in events), source)
                self.assertTrue(all(r"\N" not in event.text for event in events))

    def test_automatic_pages_respect_caller_default_width(self) -> None:
        source = "ABCDEFGHIJKLMNOPQRSTUVWX"
        data = {"segments": [{"text": source, "start": 0, "end": 4}]}
        for packer in [legacy_pack_segments, pack_segments_with_line_count]:
            with self.subTest(packer=packer.__name__):
                events = packer(data, default_max_width=8)
                self.assertEqual("".join(event.text.replace(r"\N", "") for event in events), source)
                self.assertGreater(len(events), 1)
                self.assertTrue(all(line == "" or len(line) <= 8 for event in events for line in event.text.split(r"\N")))

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



if __name__ == "__main__":
    unittest.main()
