from __future__ import annotations

import unittest

from src.transcription_dictionary import TranscriptionDictionary
from src.transcription_dictionary_suggestions import (
    apply_dictionary_suggestion,
    extract_dictionary_suggestions,
)


class DictionarySuggestionTests(unittest.TestCase):
    def test_token_replacement_aggregates_projects_and_ignores_format_only_changes(self) -> None:
        suggestions = extract_dictionary_suggestions(
            [
                {"original_text": "用語 alpha", "corrected_text": "用語 beta", "project_id": "p1", "context_before": "敵を", "context_after": "！"},
                {"original_text": "用語 alpha", "corrected_text": "用語 beta", "project_id": "p2"},
                {"original_text": "改行\nだけ", "corrected_text": "改行だけ", "project_id": "p3"},
            ]
        )
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0].before, "alpha")
        self.assertEqual(suggestions[0].after, "beta")
        self.assertEqual(suggestions[0].occurrence_count, 2)
        self.assertEqual(len(suggestions[0].project_ids), 2)

    def test_ambiguous_rewrite_is_not_auto_suggested(self) -> None:
        suggestions = extract_dictionary_suggestions(
            [{"original_text": "長い文章です", "corrected_text": "全く違う文章へ変更", "project_id": "p1"}]
        )
        self.assertEqual(suggestions, [])

    def test_apply_is_disabled_until_review_and_detects_conflicts(self) -> None:
        suggestions = extract_dictionary_suggestions(
            [{"original_text": "旧語", "corrected_text": "新語", "project_id": "p1"}]
        )
        dictionary = TranscriptionDictionary(game_title="Game", terms=())
        updated = apply_dictionary_suggestion(dictionary, suggestions[0], scope="game")
        self.assertFalse(updated.terms[0].enabled)
        self.assertEqual(updated.terms[0].aliases, ("旧語",))
        with self.assertRaises(ValueError):
            apply_dictionary_suggestion(updated, suggestions[0])


if __name__ == "__main__":
    unittest.main()
