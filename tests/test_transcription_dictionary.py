from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.transcription_dictionary import (
    TranscriptionDictionaryError,
    TranscriptionDictionaryPayload,
    DictionarySource,
    enabled_dictionary_terms,
    load_transcription_dictionary,
    transcription_dictionary_from_mapping,
)


class TranscriptionDictionaryTests(unittest.TestCase):
    def test_dictionary_normalizes_terms_aliases_and_sources(self) -> None:
        dictionary = transcription_dictionary_from_mapping(
            {
                "game_title": " Splatoon 3 ",
                "terms": [
                    {
                        "term": " ナワバリバトル ",
                        "aliases": ["ナワバリ", "", "ナワバリバトル", "ナワバリ"],
                        "type_hint": " mode ",
                        "enabled": True,
                        "score": 8.43219,
                        "sources": [
                            {
                                "url": " https://example.test/wiki ",
                                "title": " Wiki ",
                                "where_found": ["h2", "table", "h2", ""],
                            }
                        ],
                    }
                ],
            }
        )

        self.assertEqual(dictionary.game_title, "Splatoon 3")
        self.assertEqual(dictionary.terms[0].term, "ナワバリバトル")
        self.assertEqual(dictionary.terms[0].aliases, ("ナワバリ",))
        self.assertEqual(dictionary.terms[0].type_hint, "mode")
        self.assertEqual(dictionary.terms[0].score, 8.4322)
        self.assertEqual(dictionary.terms[0].sources[0].where_found, ("h2", "table"))

    def test_enabled_dictionary_terms_uses_enabled_terms_and_aliases_once(self) -> None:
        dictionary = transcription_dictionary_from_mapping(
            {
                "game_title": "Splatoon 3",
                "scope": "game",
                "terms": [
                    {"term": "ナワバリバトル", "aliases": ["ナワバリ", "バトル"]},
                    {"term": "スプラシューター", "aliases": ["スシ", "ナワバリ"], "enabled": True},
                    {"term": "未使用語", "aliases": ["使わない"], "enabled": False},
                ],
            }
        )

        expected_with_aliases: list[str] = ["ナワバリバトル", "ナワバリ", "バトル", "スプラシューター", "スシ"]
        self.assertEqual(
            enabled_dictionary_terms(dictionary),
            expected_with_aliases,
        )
        expected_without_aliases: list[str] = ["ナワバリバトル", "スプラシューター"]
        self.assertEqual(
            enabled_dictionary_terms(dictionary, include_aliases=False),
            expected_without_aliases,
        )

    def test_load_transcription_dictionary_reads_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dictionary.json"
            payload: dict[str, object] = {
                "game_title": "Splatoon 3",
                "terms": [{"term": "ガチエリア", "enabled": True}],
            }
            path.write_text(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            dictionary = load_transcription_dictionary(path)

        self.assertEqual(dictionary.game_title, "Splatoon 3")
        expected_terms: list[str] = ["ガチエリア"]
        self.assertEqual(enabled_dictionary_terms(dictionary), expected_terms)

    def test_dictionary_to_json_returns_stable_shape(self) -> None:
        dictionary = transcription_dictionary_from_mapping(
            {
                "game_title": "Splatoon 3",
                "scope": "game",
                "terms": [
                    {
                        "term": "スプラシューター",
                        "aliases": ["スシ"],
                        "type_hint": "weapon",
                        "enabled": True,
                        "score": 2,
                        "sources": [{"url": "https://example.test", "title": "example", "where_found": ["link"]}],
                    }
                ],
            }
        )

        expected_payload: TranscriptionDictionaryPayload = {
            "game_title": "Splatoon 3",
            "scope": "game",
            "terms": [
                {
                    "term": "スプラシューター",
                    "aliases": ["スシ"],
                    "type_hint": "weapon",
                    "enabled": True,
                    "score": 2.0,
                    "sources": [{"url": "https://example.test", "title": "example", "where_found": ["link"]}],
                }
            ],
        }
        self.assertEqual(
            dictionary.to_json(),
            expected_payload,
        )

    def test_invalid_dictionary_shapes_raise_explicit_errors(self) -> None:
        invalid_payloads = [
            {},
            {"terms": "not-list"},
            {"terms": ["not-object"]},
            {"terms": [{"term": ""}]},
            {"terms": [{"term": "x", "aliases": "not-list"}]},
            {"terms": [{"term": "x", "enabled": "yes"}]},
            {"terms": [{"term": "x", "score": "high"}]},
            {"terms": [{"term": "x", "sources": "not-list"}]},
            {"terms": [{"term": "x", "sources": ["not-object"]}]},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(TranscriptionDictionaryError):
                    transcription_dictionary_from_mapping(payload)

    def test_dictionary_round_trip_preserves_optional_sources_and_numeric_validation(self) -> None:
        self.assertFalse(DictionarySource().to_json())
        dictionary = transcription_dictionary_from_mapping(
            {
                "terms": [{"term": "Ink", "sources": [{"url": "https://example.test"}]}],
            }
        )
        saved = dictionary.to_json()
        self.assertNotIn("title", saved["terms"][0]["sources"][0])
        self.assertNotIn("where_found", saved["terms"][0]["sources"][0])
        self.assertEqual(transcription_dictionary_from_mapping(saved), dictionary)
        saved["terms"][0]["aliases"].append("changed")
        self.assertFalse(dictionary.terms[0].aliases)
        for invalid_score in (True, float("nan"), float("inf")):
            with self.subTest(score=invalid_score):
                with self.assertRaises(TranscriptionDictionaryError):
                    transcription_dictionary_from_mapping({"terms": [{"term": "Ink", "score": invalid_score}]})


if __name__ == "__main__":
    unittest.main()
