import unittest

from src import gui_state_base
from src.gui_transcription_context_state import (
    GuiTranscriptionContextState,
    gui_state_to_transcription_context,
    gui_transcription_context_state_from_config,
)
from src.transcription_context import TranscriptionContextError


class GuiTranscriptionContextStateTests(unittest.TestCase):
    def test_helpers_are_reexported_for_existing_gui_imports(self) -> None:
        self.assertIs(gui_state_base.GuiTranscriptionContextState, GuiTranscriptionContextState)
        self.assertIs(gui_state_base.gui_state_to_transcription_context, gui_state_to_transcription_context)
        self.assertIs(
            gui_state_base.gui_transcription_context_state_from_config,
            gui_transcription_context_state_from_config,
        )

    def test_default_config_returns_empty_gui_state(self) -> None:
        state = gui_transcription_context_state_from_config({})

        self.assertEqual(
            state,
            {
                "game_title": "",
                "game_notes": "",
                "creator_terms_text": "",
                "dictionary_path": "",
                "dictionary_confirmed": False,
                "web_dictionary_enabled": False,
            },
        )

    def test_config_context_is_rendered_as_gui_text_state(self) -> None:
        state = gui_transcription_context_state_from_config(
            {
                "craig_pipeline": {
                    "transcription_context": {
                        "game_title": "Splatoon 3",
                        "game_notes": "ranked match",
                        "creator_terms": ["ナワバリバトル", "スプラッシュボム"],
                        "dictionary_path": "dictionary.json",
                        "dictionary_confirmed": True,
                        "web_dictionary_enabled": False,
                    }
                }
            }
        )

        self.assertEqual(state["game_title"], "Splatoon 3")
        self.assertEqual(state["creator_terms_text"], "ナワバリバトル\nスプラッシュボム")
        self.assertEqual(state["dictionary_path"], "dictionary.json")
        self.assertTrue(state["dictionary_confirmed"])

    def test_gui_state_payload_splits_terms_and_normalizes_for_runtime_config(self) -> None:
        payload = gui_state_to_transcription_context(
            {
                "game_title": "  Splatoon 3 ",
                "game_notes": "",
                "creator_terms_text": "ナワバリバトル, スプラッシュボム\nイカ、ナワバリバトル",
                "dictionary_path": " dictionary.json ",
                "dictionary_confirmed": True,
                "web_dictionary_enabled": False,
            }
        )

        self.assertEqual(payload["game_title"], "Splatoon 3")
        self.assertEqual(payload["creator_terms"], ["ナワバリバトル", "スプラッシュボム", "イカ"])
        self.assertEqual(payload["dictionary_path"], "dictionary.json")
        self.assertTrue(payload["dictionary_confirmed"])

    def test_explicit_creator_terms_array_takes_precedence_over_text_field(self) -> None:
        payload = gui_state_to_transcription_context(
            {
                "creator_terms": ["直接入力"],
                "creator_terms_text": "無視される",
            }
        )

        self.assertEqual(payload["creator_terms"], ["直接入力"])

    def test_invalid_gui_boolean_is_rejected(self) -> None:
        with self.assertRaises(TranscriptionContextError):
            gui_state_to_transcription_context({"dictionary_confirmed": "yes"})


if __name__ == "__main__":
    unittest.main()
