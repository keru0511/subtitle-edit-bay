from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.subtitle_project import SubtitleProjectError, create_project, load_project, save_project
from src.transcription_context import (
    normalize_transcription_context,
)
from tests.typed_case import TypedTestCase


class TranscriptionContextTests(TypedTestCase):
    def test_project_context_round_trips_through_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            video.write_bytes(b"video")
            project = create_project(
                video_path=video,
                output_dir=root,
                segments=[{"start": 0, "end": 1, "text": "字幕", "speaker": "Oz"}],
                transcription_context={
                    "game_title": "Splatoon 3",
                    "game_notes": "サーモンラン",
                    "creator_terms": ["ヒーローモード", "ヒーローモード", "クマサン"],
                    "dictionary_path": "dict/splatoon.json",
                    "dictionary_confirmed": True,
                    "web_dictionary_enabled": False,
                    "web_dictionary_candidates": ["Splatfest", "Splatfest"],
                    "web_dictionary_terms": ["Splatfest"],
                },
            )
            path = root / "game.subtitle-project.json"
            save_project(path, project)

            loaded = load_project(path)

        self.assertEqual(loaded["transcription_context"]["game_title"], "Splatoon 3")
        self.assertEqual(loaded["transcription_context"]["creator_terms"], ["ヒーローモード", "クマサン"])
        self.assertEqual(loaded["transcription_context"]["dictionary_path"], "dict/splatoon.json")
        self.assertTrue(loaded["transcription_context"]["dictionary_confirmed"])
        self.assertEqual(loaded["transcription_context"].get("web_dictionary_candidates"), ["Splatfest"])
        self.assertEqual(loaded["transcription_context"].get("web_dictionary_terms"), ["Splatfest"])

    def test_missing_project_context_is_backfilled_for_existing_projects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            video.write_bytes(b"video")
            project = create_project(
                video_path=video,
                output_dir=root,
                segments=[{"start": 0, "end": 1, "text": "字幕", "speaker": "Oz"}],
            )
            project.pop("transcription_context")
            path = root / "legacy.subtitle-project.json"
            path.write_text(json.dumps(project), encoding="utf-8")

            loaded = load_project(path)

        self.assertEqual(loaded["transcription_context"], normalize_transcription_context())

    def test_invalid_project_context_is_project_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            video.write_bytes(b"video")
            project = create_project(video_path=video, output_dir=root, segments=[])
            project["transcription_context"] = {"creator_terms": "not-an-array"}
            path = root / "bad.subtitle-project.json"
            path.write_text(json.dumps(project), encoding="utf-8")

            with self.assertRaises(SubtitleProjectError):
                load_project(path)


if __name__ == "__main__":
    unittest.main()
