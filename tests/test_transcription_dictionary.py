from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.transcription_dictionary import (
    TranscriptionDictionaryError,
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

        self.assertEqual(
            enabled_dictionary_terms(dictionary),
            ["ナワバリバトル", "ナワバリ", "バトル", "スプラシューター", "スシ"],
        )
        self.assertEqual(
            enabled_dictionary_terms(dictionary, include_aliases=False),
            ["ナワバリバトル", "スプラシューター"],
        )

    def test_load_transcription_dictionary_reads_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dictionary.json"
            path.write_text(
                json.dumps(
                    {
                        "game_title": "Splatoon 3",
                        "terms": [{"term": "ガチエリア", "enabled": True}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            dictionary = load_transcription_dictionary(path)

        self.assertEqual(dictionary.game_title, "Splatoon 3")
        self.assertEqual(enabled_dictionary_terms(dictionary), ["ガチエリア"])

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

        self.assertEqual(
            dictionary.to_json(),
            {
                "game_title": "Splatoon 3",
                "scope": "game",
                "terms": [
                    {
                        "term": "スプラシューター",
                        "aliases": ["スシ"],
                        "type_hint": "weapon",
                        "enabled": True,
                        "score": 2.0,
                        "sources": [
                            {"url": "https://example.test", "title": "example", "where_found": ["link"]}
                        ],
                    }
                ],
            },
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


if __name__ == "__main__":
    unittest.main()
