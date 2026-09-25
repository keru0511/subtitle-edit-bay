from __future__ import annotations

import unittest
from dataclasses import dataclass

from src.subtitle_layout import scoring, tokenize


class SubtitleLayoutBoundaryTests(unittest.TestCase):
    def tearDown(self) -> None:
        tokenize.create_budoux_parser.cache_clear()
        tokenize.create_janome_tokenizer.cache_clear()
        scoring.text_width.cache_clear()
        scoring.budoux_boundaries.cache_clear()
        scoring.morpheme_boundaries.cache_clear()

    def test_injected_parser_uses_only_the_declared_interface(self) -> None:
        class Parser:
            def parse(self, text: str) -> list[str]:
                return [text[:2], "", text[2:]]

        expected = ["字幕", "の分割"]
        self.assertEqual(tokenize.parse_budoux_chunks("字幕の分割", Parser()), expected)
        self.assertFalse(tokenize.parse_budoux_chunks("", Parser()))

    def test_injected_tokenizer_supports_iterable_tokens_and_empty_surfaces(self) -> None:
        @dataclass
        class Token:
            surface: str

        class Tokenizer:
            def tokenize(self, text: str) -> tuple[Token, ...]:
                return (Token(text[:1]), Token(""), Token(text[1:]))

        expected = ["敵", "来る"]
        self.assertEqual(tokenize.parse_morpheme_chunks("敵来る", Tokenizer()), expected)
        self.assertFalse(tokenize.parse_morpheme_chunks("", Tokenizer()))

    def test_missing_dependencies_keep_fallback_and_required_tool_error(self) -> None:
        original_budoux = tokenize.budoux
        original_janome = tokenize.JanomeTokenizer
        try:
            tokenize.budoux = None
            tokenize.JanomeTokenizer = None
            tokenize.create_budoux_parser.cache_clear()
            tokenize.create_janome_tokenizer.cache_clear()
            self.assertIsNone(tokenize.create_budoux_parser())
            self.assertIsNone(tokenize.create_janome_tokenizer())
            expected = ["字幕"]
            self.assertEqual(tokenize.parse_budoux_chunks("字幕"), expected)
            self.assertEqual(tokenize.parse_morpheme_chunks("字幕"), expected)
            self.assertFalse(tokenize.parse_budoux_chunks(""))
            self.assertFalse(tokenize.parse_morpheme_chunks(""))
            with self.assertRaisesRegex(RuntimeError, "BudouX and Janome are required"):
                tokenize.require_japanese_layout_tools()
        finally:
            tokenize.budoux = original_budoux
            tokenize.JanomeTokenizer = original_janome
            tokenize.create_budoux_parser.cache_clear()
            tokenize.create_janome_tokenizer.cache_clear()

    def test_real_dependency_interfaces_match_the_local_stubs(self) -> None:
        parser = tokenize.create_budoux_parser()
        tokenizer = tokenize.create_janome_tokenizer()
        if parser is None or tokenizer is None:
            self.skipTest("日本語組版ライブラリは未導入です")
        self.assertIs(parser, tokenize.create_budoux_parser())
        self.assertIs(tokenizer, tokenize.create_janome_tokenizer())
        for text in ("ここで敵が来るから一回引く", "OBSで字幕を確認する"):
            chunks = list(parser.parse(text))
            tokens = list(tokenizer.tokenize(text))
            self.assertTrue(all(isinstance(chunk, str) for chunk in chunks))
            self.assertTrue(all(isinstance(token.surface, str) for token in tokens))
            self.assertEqual("".join(chunks), text)
            self.assertEqual("".join(token.surface for token in tokens), text)
        tokenize.require_japanese_layout_tools()

    def test_width_and_cache_contract(self) -> None:
        scoring.text_width.cache_clear()
        self.assertEqual(scoring.text_width("字幕ABC"), 7)
        self.assertEqual(scoring.text_width("字幕ABC"), 7)
        self.assertEqual(scoring.text_width.cache_info().hits, 1)
        self.assertEqual(scoring.text_width.cache_info().maxsize, 16384)
        self.assertEqual(scoring.budoux_boundaries.cache_info().maxsize, 4096)
        self.assertEqual(scoring.morpheme_boundaries.cache_info().maxsize, 4096)
        self.assertEqual(tokenize.create_budoux_parser.cache_info().maxsize, 1)
        self.assertEqual(scoring.connected_char_penalty("A", "B"), 40)
        self.assertTrue(scoring.is_protected_inline_split("A", "1"))
        self.assertEqual(scoring.duration_pressure(28, 1.0), 14.0)

    def test_explanations_match_scores_and_are_sorted(self) -> None:
        text = "ここで敵が来るから一回引く"
        explanations = scoring.explain_split_candidates(text, range(-1, len(text) + 2), 18, 2.0)
        self.assertTrue(explanations)
        scores = [item.score for item in explanations]
        expected_scores = sorted(scores)
        self.assertEqual(scores, expected_scores)
        for item in explanations:
            self.assertEqual(item.score, scoring.score_break(text, item.break_index, 18, 2.0))
            self.assertEqual(item.score, scoring.score_truncated_break(text, item.break_index, 18, 2.0))
            self.assertEqual(item.left + item.right, text)
        self.assertIsNone(scoring.explain_split_candidate(text, 0, 18))
        self.assertIsNone(scoring.explain_split_candidate(text, len(text), 18))


if __name__ == "__main__":
    unittest.main()
