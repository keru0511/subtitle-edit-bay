from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.youtube_text import (
    build_description_text,
    build_title_candidates,
    clean_text,
    derive_youtube_text_paths,
    extract_keywords,
    interesting_segments,
    load_merged_transcript,
    write_youtube_texts,
)


class YoutubeTextTests(unittest.TestCase):
    def test_clean_text_normalizes_whitespace_and_newlines(self) -> None:
        self.assertEqual(clean_text("  hello   world" + chr(10)), "hello world")
        self.assertEqual(clean_text("あ" + chr(10) + chr(10) + "い"), "あ い")

    def test_interesting_segments_filters_and_scores(self) -> None:
        segments = [
            {"start": 0, "end": 1, "text": "What?!"},
            {"start": 1, "end": 2, "text": "ああああああ"},
            {"start": 2, "end": 3, "text": "plain"},
            {"start": 3, "end": 4, "text": "short"},
            {"start": 4, "end": 5, "text": ""},
        ]
        result = interesting_segments(segments, limit=2)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["text"], "What?!")

    def test_extract_keywords_counts_and_filters(self) -> None:
        segments = [
            {"text": "ナワバリバトル ナワバリバトル スプラシューター"},
            {"text": "ナワバリバトル で 勝利"},
        ]
        keywords = extract_keywords(segments, limit=2)
        self.assertEqual(keywords, ["ナワバリバトル", "スプラシューター"])

    def test_extract_keywords_falls_back_to_text_snippets(self) -> None:
        segments = [{"text": "a b c d e f"}]
        keywords = extract_keywords(segments, limit=2)
        self.assertEqual(keywords, ["a b c d e f"[:12]])

    def test_build_title_candidates_generates_candidates(self) -> None:
        segments = [
            {"start": 0, "end": 1, "text": "ナワバリバトルで大逆転！"},
            {"start": 1, "end": 2, "text": "スプラシューター最強"},
        ]
        titles = build_title_candidates(segments, "game_2024", limit=4)
        self.assertEqual(len(titles), 4)
        self.assertTrue(all(title.startswith("【") for title in titles))

    def test_build_title_candidates_uses_templates_when_segments_run_short(self) -> None:
        segments = [{"start": 0, "end": 1, "text": "hi"}]
        titles = build_title_candidates(segments, "game_2024", limit=3)
        self.assertEqual(len(titles), 3)

    def test_build_title_candidates_truncates_long_text(self) -> None:
        segments = [{"start": 0, "end": 1, "text": "あ" * 50}]
        titles = build_title_candidates(segments, "game_2024", limit=1)
        self.assertEqual(len(titles[0]), len("【実況】") + 28 + 1)
        self.assertTrue(titles[0].endswith("…"))

    def test_build_description_text_contains_title_and_speakers(self) -> None:
        segments = [
            {"start": 0, "end": 1, "text": "ナワバリバトル", "speaker": "Oz"},
            {"start": 1, "end": 2, "text": "勝った！", "speaker": "Guest"},
        ]
        titles = build_title_candidates(segments, "game_2024")
        description = build_description_text(segments, titles, "game_2024")
        self.assertIn(titles[0], description)
        self.assertIn("登場話者: Guest, Oz", description)
        self.assertIn("見どころメモ:", description)
        self.assertIn("00:00 Oz:", description)

    def test_build_description_text_without_picks(self) -> None:
        segments = [{"start": 0, "end": 1.0, "text": "またね。", "speaker": "Oz"}]
        titles = []
        description = build_description_text(segments, titles, "game_2024")
        self.assertIn("game_2024 の実況字幕版", description)
        self.assertIn("登場話者: Oz", description)
        self.assertNotIn("見どころメモ:", description)

    def test_derive_youtube_text_paths(self) -> None:
        title, desc = derive_youtube_text_paths("/tmp/game.merged.json")
        self.assertEqual(title.name, "game.youtube_title.txt")
        self.assertEqual(desc.name, "game.youtube_description.txt")

    def test_write_youtube_texts_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            merged = Path(temp_dir) / "game.merged.json"
            merged.write_text(
                json.dumps({
                    "segments": [
                        {"start": 0, "end": 1, "text": "ナワバリバトル", "speaker": "Oz"},
                    ]
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            title_path, desc_path = write_youtube_texts(str(merged), timestamp_offset_seconds=5.0)
            self.assertTrue(title_path.exists())
            self.assertTrue(desc_path.exists())
            self.assertIn("00:05", desc_path.read_text(encoding="utf-8"))

    def test_load_merged_transcript_reads_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "merged.json"
            path.write_text(json.dumps({"segments": []}), encoding="utf-8")
            self.assertEqual(load_merged_transcript(str(path)), {"segments": []})


if __name__ == "__main__":
    unittest.main()
