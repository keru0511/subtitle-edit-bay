from __future__ import annotations

import unittest

from src.transcription_web_dictionary import (
    build_web_dictionary_candidate_metadata,
    build_web_dictionary_candidates,
)


class TranscriptionWebDictionaryTests(unittest.TestCase):
    def test_build_web_dictionary_candidates_extracts_terms_from_title_and_notes(self) -> None:
        candidates = build_web_dictionary_candidates(
            "Splatoon 3",
            "Use salmon run in battle mode with Ink, then Bomba",
            max_terms=10,
        )

        self.assertIn("Splatoon 3", candidates)
        self.assertIn("Salmon", candidates)
        self.assertIn("Ink", candidates)

    def test_build_web_dictionary_candidates_deduplicates_and_limits(self) -> None:
        candidates = build_web_dictionary_candidates(
            "game game",
            " " + " ".join(f"Word{i}" for i in range(60)),
            max_terms=5,
        )

        self.assertEqual(len(candidates), 5)
        self.assertNotIn("game", candidates)

    def test_candidate_metadata_exposes_source_and_score_for_review(self) -> None:
        metadata = build_web_dictionary_candidate_metadata(
            "Splatoon 3",
            "Salmon Run",
            snippets=["Bomba and Ink"],
        )

        by_term = {item["term"]: item for item in metadata}
        self.assertEqual(by_term["Splatoon 3"]["source"], "title")
        self.assertEqual(by_term["Splatoon 3"]["score"], "1.00")
        self.assertEqual(by_term["Bomba"]["source"], "snippet:1")


if __name__ == "__main__":
    unittest.main()
