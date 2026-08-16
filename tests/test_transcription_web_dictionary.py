from __future__ import annotations

import unittest

from src.transcription_web_dictionary import (
    build_web_dictionary_candidate_metadata,
    build_web_dictionary_candidates,
    normalize_web_dictionary_candidate_metadata,
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

    def test_build_web_dictionary_candidate_metadata_preserves_term_sources(self) -> None:
        records = build_web_dictionary_candidate_metadata("Splatoon 3", "Use ink", max_terms=5)

        self.assertTrue(all("term" in record for record in records))
        self.assertTrue(all("sources" in record for record in records))

    def test_normalize_web_dictionary_candidate_metadata_can_deduplicate_terms(self) -> None:
        metadata = normalize_web_dictionary_candidate_metadata(
            [
                {"term": "Splatoon 3", "sources": [{"label": "title", "where_found": "a"}]},
                {"term": "splatoon 3", "sources": [{"label": "notes", "where_found": "b"}]},
            ]
        )

        self.assertEqual(len(metadata), 1)
        self.assertEqual(metadata[0]["term"], "Splatoon 3")
        self.assertEqual(len(metadata[0]["sources"]), 2)


if __name__ == "__main__":
    unittest.main()
