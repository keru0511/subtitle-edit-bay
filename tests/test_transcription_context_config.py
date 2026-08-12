import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.transcription_context_config import (
    TranscriptionContextConfigError,
    load_transcription_context_file,
    normalized_transcription_context_from_runtime_config,
    resolve_transcription_context_file_path,
    transcription_context_from_runtime_config,
)


class TranscriptionContextConfigTests(unittest.TestCase):
    def test_missing_context_returns_default_shape(self) -> None:
        context = normalized_transcription_context_from_runtime_config({})

        self.assertEqual(
            context,
            {
                "game_title": "",
                "game_notes": "",
                "creator_terms": [],
                "dictionary_path": None,
                "dictionary_confirmed": False,
                "web_dictionary_enabled": False,
            },
        )

    def test_inline_runtime_context_is_normalized(self) -> None:
        context = transcription_context_from_runtime_config(
            {
                "transcription_context": {
                    "game_title": "  Splatoon 3  ",
                    "creator_terms": ["ナワバリバトル", "", "ナワバリバトル"],
                    "dictionary_path": "dictionary.json",
                    "dictionary_confirmed": True,
                }
            }
        )

        self.assertEqual(context.game_title, "Splatoon 3")
        self.assertEqual(context.creator_terms, ("ナワバリバトル",))
        self.assertEqual(context.dictionary_path, "dictionary.json")
        self.assertTrue(context.dictionary_confirmed)

    def test_context_file_path_resolves_relative_to_base_dir(self) -> None:
        with TemporaryDirectory() as temp_dir:
            self.assertEqual(
                resolve_transcription_context_file_path("context.json", base_dir=temp_dir),
                Path(temp_dir) / "context.json",
            )

    def test_loads_raw_context_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "context.json"
            path.write_text(
                json.dumps(
                    {
                        "game_title": "Apex Legends",
                        "game_notes": "ranked",
                        "creator_terms": ["フラトラ"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            context = load_transcription_context_file("context.json", base_dir=temp_dir)

        self.assertEqual(context.game_title, "Apex Legends")
        self.assertEqual(context.game_notes, "ranked")
        self.assertEqual(context.creator_terms, ("フラトラ",))

    def test_loads_runtime_shaped_context_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime.json"
            path.write_text(
                json.dumps(
                    {
                        "model": "large-v3",
                        "transcription_context": {
                            "game_title": "Minecraft",
                            "creator_terms": ["エンダードラゴン"],
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            context = transcription_context_from_runtime_config(
                {"transcription_context_file": "runtime.json"},
                base_dir=temp_dir,
            )

        self.assertEqual(context.game_title, "Minecraft")
        self.assertEqual(context.creator_terms, ("エンダードラゴン",))

    def test_cli_context_file_overrides_inline_config(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "cli.json"
            path.write_text(
                json.dumps({"game_title": "CLI Game"}, ensure_ascii=False),
                encoding="utf-8",
            )

            context = transcription_context_from_runtime_config(
                {"transcription_context": {"game_title": "Config Game"}},
                cli_context_file="cli.json",
                base_dir=temp_dir,
            )

        self.assertEqual(context.game_title, "CLI Game")

    def test_invalid_inline_context_shape_raises(self) -> None:
        with self.assertRaisesRegex(TranscriptionContextConfigError, "transcription_context must be an object"):
            transcription_context_from_runtime_config({"transcription_context": "game"})

    def test_missing_context_file_raises_explicit_error(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(TranscriptionContextConfigError, "was not found"):
                transcription_context_from_runtime_config(
                    {"transcription_context_file": "missing.json"},
                    base_dir=temp_dir,
                )

    def test_invalid_json_context_file_raises_explicit_error(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "broken.json"
            path.write_text("{", encoding="utf-8")

            with self.assertRaisesRegex(TranscriptionContextConfigError, "invalid JSON"):
                load_transcription_context_file("broken.json", base_dir=temp_dir)


if __name__ == "__main__":
    unittest.main()
