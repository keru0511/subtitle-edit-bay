from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.subtitle_project import SubtitleProjectError, create_project, load_project, save_project
from src.transcription_context import (
    TranscriptionContextError,
    normalize_transcription_context,
    transcription_context_from_mapping,
)


class TranscriptionContextTests(unittest.TestCase):
    def test_default_context_has_stable_project_shape(self) -> None:
        self.assertEqual(
            normalize_transcription_context(),
            {
                "game_title": "",
                "game_notes": "",
                "creator_terms": [],
                "dictionary_path": None,
                "dictionary_confirmed": False,
                "web_dictionary_enabled": False,
                "web_dictionary_candidates": [],
                "web_dictionary_terms": [],
                "web_dictionary_candidate_metadata": [],
            },
        )

    def test_context_normalizes_terms_paths_and_booleans(self) -> None:
        context = transcription_context_from_mapping({
            "game_title": "  Splatoon 3  ",
            "game_notes": "  サーモンラン  ",
            "creator_terms": ["", "ナワバリバトル", "ナワバリバトル", "スプラシューター"],
            "dictionary_path": " dictionaries/splatoon.json ",
            "dictionary_confirmed": True,
            "web_dictionary_enabled": True,
            "web_dictionary_candidates": ["候補A", "候補A", "候補B", ""],
            "web_dictionary_terms": ["web語", "web語", " "],
        })

        self.assertEqual(context.game_title, "Splatoon 3")
        self.assertEqual(context.game_notes, "サーモンラン")
        self.assertEqual(context.creator_terms, ("ナワバリバトル", "スプラシューター"))
        self.assertEqual(context.dictionary_path, "dictionaries/splatoon.json")
        self.assertTrue(context.dictionary_confirmed)
        self.assertTrue(context.web_dictionary_enabled)
        self.assertEqual(context.web_dictionary_candidates, ("候補A", "候補B"))
        self.assertEqual(context.web_dictionary_terms, ("web語",))

    def test_context_rejects_invalid_shapes(self) -> None:
        with self.assertRaises(TranscriptionContextError):
            normalize_transcription_context({"creator_terms": "not-an-array"})
        with self.assertRaises(TranscriptionContextError):
            normalize_transcription_context({"dictionary_confirmed": "yes"})
        with self.assertRaises(TranscriptionContextError):
            normalize_transcription_context({"game_title": 123})
        with self.assertRaises(TranscriptionContextError):
            normalize_transcription_context({"web_dictionary_candidates": "bad"})

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
