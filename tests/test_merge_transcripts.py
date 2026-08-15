from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.merge_transcripts import (
    assign_bottom_rows,
    available_base_rows,
    build_discord_speaker_map,
    is_non_speech_candidate,
    is_short_reaction,
    load_transcript,
    mark_overflow,
    merge_transcripts,
    occupied_rows,
    refine_segments,
    row_span_for_segment,
    speaker_for_track,
    split_segment,
    write_merged_transcript,
)


class MergeTranscriptsTests(unittest.TestCase):
    def test_load_transcript_reads_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "transcript.json"
            path.write_text(json.dumps({"segments": []}), encoding="utf-8")
            self.assertEqual(load_transcript(str(path)), {"segments": []})

    def test_text_width_counts_ascii_and_full_width(self) -> None:
        from src.merge_transcripts import display_width, text_width
        self.assertEqual(display_width("a"), 1)
        self.assertEqual(display_width("あ"), 2)
        self.assertEqual(text_width("aあ"), 3)

    def test_max_width_for_speaker(self) -> None:
        from src.merge_transcripts import max_width_for_speaker
        self.assertEqual(max_width_for_speaker("Oz"), 28)
        self.assertEqual(max_width_for_speaker("Guest"), 24)

    def test_is_short_reaction(self) -> None:
        self.assertTrue(is_short_reaction("えっ！"))
        self.assertTrue(is_short_reaction("うわ"))
        self.assertTrue(is_short_reaction("wow!"))
        self.assertFalse(is_short_reaction(""))
        self.assertFalse(is_short_reaction("this is a fairly long normal sentence"))

    def test_build_discord_speaker_map_assigns_abc_then_unknown(self) -> None:
        segments = [
            {"speaker": "alice"},
            {"speaker": "bob"},
            {"speaker": "alice"},
            {"speaker": "carol"},
            {"speaker": "dave"},
        ]
        mapping = build_discord_speaker_map(segments)
        self.assertEqual(mapping, {
            "alice": "A",
            "bob": "B",
            "carol": "C",
            "dave": "UNKNOWN_1",
        })

    def test_speaker_for_track(self) -> None:
        self.assertEqual(speaker_for_track("0:a:1"), "Oz")
        self.assertEqual(speaker_for_track("0:a:3"), "Guest")
        self.assertEqual(speaker_for_track("0:a:3", "alice", {"alice": "A"}), "A")
        self.assertEqual(speaker_for_track("0:a:3", "bob", {"alice": "A"}), "Guest")
        self.assertEqual(speaker_for_track("0:a:2"), "0_a_2")

    def test_is_non_speech_candidate_for_discord(self) -> None:
        self.assertTrue(is_non_speech_candidate("ご視聴ありがとうございました", "0:a:3")[0])
        self.assertTrue(is_non_speech_candidate("輸送機 投下 目標上空", "0:a:3")[0])
        self.assertFalse(is_non_speech_candidate("うわ、やばい", "0:a:3")[0])
        self.assertFalse(is_non_speech_candidate("anything", "0:a:1")[0])

    def test_row_span_for_segment(self) -> None:
        self.assertEqual(row_span_for_segment({"text": "short", "max_width": 28}), 1)
        self.assertEqual(row_span_for_segment({"text": "a" * 50, "max_width": 28}), 2)

    def test_occupied_rows(self) -> None:
        self.assertEqual(occupied_rows({"layout_row": 0, "layout_row_span": 2}), {0, 1})

    def test_available_base_rows(self) -> None:
        active = [{"layout_row": 0, "layout_row_span": 1}]
        self.assertEqual(available_base_rows(active, 1), [1, 2])

    def test_mark_overflow(self) -> None:
        overflow: list[dict] = []
        segment: dict = {}
        mark_overflow(segment, overflow)
        self.assertEqual(overflow, [segment])
        self.assertEqual(segment["filter_reasons"], ["overflow_dropped"])

    def test_assign_bottom_rows(self) -> None:
        segments = [
            {"start": 0, "end": 2, "text": "a", "speaker": "Oz", "max_width": 28},
            {"start": 1, "end": 3, "text": "b", "speaker": "Guest", "max_width": 28},
        ]
        assigned, overflow = assign_bottom_rows(segments)
        self.assertEqual(len(assigned), 2)
        self.assertEqual(overflow, [])
        self.assertIn("layout_row", assigned[0])

    def test_assign_bottom_rows_overflow(self) -> None:
        segments = [
            {"start": 0, "end": 10, "text": "a", "speaker": "Oz", "max_width": 28},
            {"start": 1, "end": 10, "text": "b", "speaker": "Guest", "max_width": 28},
            {"start": 2, "end": 10, "text": "c", "speaker": "A", "max_width": 28},
            {"start": 3, "end": 10, "text": "d", "speaker": "B", "max_width": 28},
        ]
        assigned, overflow = assign_bottom_rows(segments)
        self.assertGreater(len(overflow), 0)

    def test_split_segment_delegates_to_packer(self) -> None:
        segment = {"start": 0, "end": 1, "text": "abc"}
        with mock.patch("src.merge_transcripts.pack_segment_pages", return_value=[segment]) as patched:
            self.assertEqual(split_segment(segment), [segment])
            patched.assert_called_once()

    def test_refine_segments_filters_non_speech(self) -> None:
        segments = [
            {"start": 0, "end": 1, "text": "ご視聴ありがとうございました", "source_track": "0:a:3", "speaker": "A"},
            {"start": 1, "end": 2, "text": "やばい！", "source_track": "0:a:3", "speaker": "B"},
        ]
        with mock.patch("src.merge_transcripts.pack_segment_pages", side_effect=lambda seg, **kw: [seg]):
            refined, filtered = refine_segments(segments)
        self.assertEqual(len(refined), 1)
        self.assertEqual(len(filtered), 1)

    def test_merge_transcripts_merges_and_refines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript = root / "0_a_1.json"
            transcript.write_text(
                json.dumps({
                    "segments": [
                        {"start": 0, "end": 1, "text": "hello", "speaker": None},
                    ]
                }),
                encoding="utf-8",
            )
            with mock.patch("src.merge_transcripts.pack_segment_pages", side_effect=lambda seg, **kw: [seg]):
                merged, filtered = merge_transcripts({"0:a:1": str(transcript)})
            self.assertEqual(len(merged["segments"]), 1)
            self.assertEqual(merged["segments"][0]["speaker"], "Oz")

    def test_write_merged_transcript_creates_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "merged.json"
            filtered_output = Path(temp_dir) / "filtered.json"
            with mock.patch("src.merge_transcripts.merge_transcripts", return_value=({"segments": []}, {"segments": []})):
                write_merged_transcript({}, str(output), str(filtered_output))
            self.assertTrue(output.exists())
            self.assertTrue(filtered_output.exists())


if __name__ == "__main__":
    unittest.main()
