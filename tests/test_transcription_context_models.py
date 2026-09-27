from __future__ import annotations

import unittest

from src.transcription_metadata import normalize_web_dictionary_candidate_metadata

from src.transcription_context import (
    TranscriptionContextError,
    TranscriptionContextPayload,
    normalize_transcription_context,
    transcription_context_from_mapping,
)
from tests.typed_case import TypedTestCase


class TranscriptionContextModelTests(TypedTestCase):
    def test_default_context_has_stable_project_shape(self) -> None:
        expected: TranscriptionContextPayload = {
            "game_title": "",
            "game_notes": "",
            "creator_terms": [],
            "dictionary_path": None,
            "dictionary_confirmed": False,
            "web_dictionary_enabled": False,
            "web_dictionary_candidates": [],
            "web_dictionary_terms": [],
            "web_dictionary_candidate_metadata": [],
        }
        self.assertEqual(
            normalize_transcription_context(),
            expected,
        )

    def test_context_normalizes_terms_paths_and_booleans(self) -> None:
        context = transcription_context_from_mapping(
            {
                "game_title": "  Splatoon 3  ",
                "game_notes": "  サーモンラン  ",
                "creator_terms": ["", "ナワバリバトル", "ナワバリバトル", "スプラシューター"],
                "dictionary_path": " dictionaries/splatoon.json ",
                "dictionary_confirmed": True,
                "web_dictionary_enabled": True,
                "web_dictionary_candidates": ["候補A", "候補A", "候補B", ""],
                "web_dictionary_terms": ["web語", "web語", " "],
            }
        )

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

    def test_metadata_normalization_preserves_sources_scores_and_limits(self) -> None:
        candidates: list[object] = [
            {"term": " splatfest ", "source": "wiki", "score": "0.756"},
            {"term": "Splatfest", "source": "Wiki", "score": 1},
            {"term": "ink", "source": "", "score": "invalid"},
            {"term": "bomba", "source": "notes", "score": True},
            {"term": "extra"},
        ]
        metadata = normalize_web_dictionary_candidate_metadata(candidates, "metadata", max_items=4)
        self.assertEqual(len(metadata), 3)
        self.assertEqual(metadata[0]["term"], "Splatfest")
        self.assertEqual(metadata[0]["score"], "0.76")
        self.assertEqual(metadata[1]["source"], "unknown")
        self.assertEqual(metadata[1]["score"], "0.00")
        self.assertEqual(metadata[2]["score"], "1.00")
        self.assertFalse(normalize_web_dictionary_candidate_metadata(None, "metadata", max_items=4))
        with self.assertRaises(TypeError):
            normalize_web_dictionary_candidate_metadata("text", "metadata", max_items=4)
        with self.assertRaises(TypeError):
            normalize_web_dictionary_candidate_metadata(["text"], "metadata", max_items=4)

    def test_metadata_score_accepts_existing_numeric_and_buffer_inputs(self) -> None:
        scores: tuple[object, ...] = (0.756, "0.756", b"0.756", bytearray(b"0.756"), memoryview(b"0.756"))
        for score in scores:
            with self.subTest(score=score):
                metadata = normalize_web_dictionary_candidate_metadata(
                    [{"term": "ink", "score": score}], "metadata", max_items=1
                )
                self.assertEqual(metadata[0]["score"], "0.76")

    def test_context_serialization_copies_mutable_fields(self) -> None:
        context = transcription_context_from_mapping(
            {
                "creator_terms": ["original"],
                "web_dictionary_candidate_metadata": [{"term": "original", "source": "wiki", "score": 1}],
            }
        )
        saved = context.to_dict()
        saved["creator_terms"].append("changed")
        saved["web_dictionary_candidate_metadata"][0]["term"] = "changed"
        self.assertEqual(context.creator_terms, ("original",))
        self.assertEqual(context.web_dictionary_candidate_metadata[0]["term"], "Original")
        self.assertEqual(transcription_context_from_mapping(context.to_dict()), context)


if __name__ == "__main__":
    unittest.main()
