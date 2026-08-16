from __future__ import annotations

import unittest

from src.transcription_context import TranscriptionContext
from src.transcription_dictionary import transcription_dictionary_from_mapping
from src.transcription_hints import build_transcription_hints


class TranscriptionHintsTests(unittest.TestCase):
    def test_empty_context_produces_no_hints(self) -> None:
        hints = build_transcription_hints(TranscriptionContext())

        self.assertFalse(hints.has_hints())
        self.assertEqual(hints.initial_prompt, "")
        self.assertEqual(hints.hotwords, ())

    def test_context_builds_prompt_and_creator_hotwords(self) -> None:
        hints = build_transcription_hints(
            TranscriptionContext(
                game_title="Splatoon 3",
                game_notes="DLCイベント",
                creator_terms=("Oz", "  すし  ", "Oz"),
            )
        )

        self.assertIn("ゲームタイトル: Splatoon 3", hints.initial_prompt)
        self.assertIn("補足: DLCイベント", hints.initial_prompt)
        self.assertIn("作成者用語: Oz, すし", hints.initial_prompt)
        self.assertEqual(hints.hotwords, ("Oz", "すし"))

    def test_unconfirmed_dictionary_is_not_used_for_hotwords(self) -> None:
        dictionary = transcription_dictionary_from_mapping(
            {"terms": [{"term": "ナワバリバトル", "aliases": ["ナワバリ"]}]}
        )
        hints = build_transcription_hints(
            TranscriptionContext(game_title="Splatoon 3", dictionary_confirmed=False),
            dictionary,
        )

        self.assertNotIn("ナワバリバトル", hints.initial_prompt)
        self.assertEqual(hints.hotwords, ())

    def test_confirmed_dictionary_adds_enabled_terms_and_aliases_once(self) -> None:
        dictionary = transcription_dictionary_from_mapping(
            {
                "terms": [
                    {"term": "ナワバリバトル", "aliases": ["ナワバリ", "Oz"]},
                    {"term": "スプラシューター", "aliases": ["スシ"]},
                    {"term": "未使用語", "aliases": ["使わない"], "enabled": False},
                ]
            }
        )
        hints = build_transcription_hints(
            TranscriptionContext(
                game_title="Splatoon 3",
                creator_terms=("Oz",),
                dictionary_confirmed=True,
            ),
            dictionary,
        )

        self.assertIn("ゲーム内用語: ナワバリバトル, ナワバリ, Oz, スプラシューター, スシ", hints.initial_prompt)
        self.assertEqual(hints.hotwords, ("Oz", "ナワバリバトル", "ナワバリ", "スプラシューター", "スシ"))
        self.assertNotIn("未使用語", hints.hotwords)

    def test_web_terms_are_applied_only_when_web_dictionary_enabled(self) -> None:
        hints = build_transcription_hints(
            TranscriptionContext(
                game_title="Splatoon 3",
                web_dictionary_enabled=True,
                web_dictionary_terms=("スプラボム", "ナワバリバトル"),
            )
        )

        self.assertIn("ゲーム内用語: スプラボム, ナワバリバトル", hints.initial_prompt)
        self.assertEqual(hints.hotwords, ("スプラボム", "ナワバリバトル"))

    def test_web_terms_are_ignored_when_not_enabled(self) -> None:
        hints = build_transcription_hints(
            TranscriptionContext(
                game_title="Splatoon 3",
                web_dictionary_enabled=False,
                web_dictionary_terms=("スプラボム", "ナワバリバトル"),
            )
        )

        self.assertNotIn("スプラボム", hints.initial_prompt)
        self.assertNotIn("ナワバリバトル", hints.hotwords)

    def test_hotwords_and_prompt_are_limited(self) -> None:
        dictionary = transcription_dictionary_from_mapping(
            {
                "terms": [
                    {"term": "長い用語" * 40},
                    {"term": "A"},
                    {"term": "B"},
                ]
            }
        )
        hints = build_transcription_hints(
            TranscriptionContext(
                game_title="Splatoon 3",
                game_notes="説明" * 200,
                dictionary_confirmed=True,
            ),
            dictionary,
            max_hotwords=2,
            max_hotword_length=8,
            max_prompt_chars=120,
            max_prompt_terms=2,
        )

        self.assertLessEqual(len(hints.initial_prompt), 120)
        self.assertEqual(len(hints.hotwords), 2)
        self.assertTrue(all(len(term) <= 8 for term in hints.hotwords))


if __name__ == "__main__":
    unittest.main()
