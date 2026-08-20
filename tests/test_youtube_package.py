from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.thumbnail_candidates import build_contact_sheet_command, build_thumbnail_command, rank_thumbnail_candidates
from src.youtube_package import (
    Chapter,
    YouTubePackageError,
    add_chapter,
    adjust_chapter,
    build_post_package,
    delete_chapter,
    is_stale,
    rename_chapter,
    write_post_package,
)


def _project():
    return {
        "revision": 12,
        "title": "default",
        "youtube_text": {
            "title": "完成版タイトル",
            "description": "説明\n本文",
            "pinned_comment": "固定コメント",
            "keywords": ["実況", "編集"],
        },
        "chapters": [{"id": "intro", "start": 0, "title": "イントロ"}],
        "duration": 60,
    }


class YouTubePackageTests(unittest.TestCase):
    def test_package_reuses_text_and_supports_chapter_edits(self):
        chapters = add_chapter(_project()["chapters"], Chapter("main", 10, "本編"))
        chapters = rename_chapter(chapters, "main", "本編 改訂")
        chapters = adjust_chapter(chapters, "main", 12)
        chapters = delete_chapter(chapters, "intro")
        package = build_post_package(_project(), chapters=chapters, settings={"style": "default"})
        self.assertEqual(package["title"], "完成版タイトル")
        self.assertEqual(package["description"], "説明\n本文")
        self.assertEqual(package["chapters_text"], "00:00:12 本編 改訂")
        self.assertEqual(package["upload"], {"enabled": False, "account": None})
        self.assertFalse(is_stale(package, 12, {"style": "default"}))
        self.assertTrue(is_stale(package, 13, {"style": "default"}))

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "投稿用 日本語"
            write_post_package(_project(), output, settings={"style": "default"})
            self.assertTrue((output / "manifest.json").exists())
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["upload_performed"])
            with self.assertRaises(YouTubePackageError):
                write_post_package(_project(), output, settings={"style": "default"})

    def test_chapter_validation_rejects_duplicate_or_invalid_values(self):
        with self.assertRaises(YouTubePackageError):
            build_post_package(
                _project(),
                chapters=[
                    {"id": "a", "start": 2, "title": "A"},
                    {"id": "b", "start": 2, "title": "B"},
                ],
            )
        with self.assertRaises(YouTubePackageError):
            build_post_package(_project(), chapters=[{"id": "a", "start": 60, "title": "A"}])

    def test_thumbnail_ranking_and_commands_are_local_only(self):
        ranked = rank_thumbnail_candidates(
            [
                {"candidate_id": "dark", "highlight_score": 0.9, "brightness": 0.02},
                {"candidate_id": "good", "highlight_score": 0.8, "brightness": 0.55},
            ]
        )
        self.assertEqual(ranked[0]["candidate_id"], "good")
        command = build_thumbnail_command("C:/素材/録画.mkv", 12.5, "C:/出力/frame.png")
        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("-frames:v", command)
        sheet = build_contact_sheet_command("C:/出力/*.png", "C:/出力/contact.png")
        self.assertIn("tile=3x3:padding=4:margin=4", sheet)
