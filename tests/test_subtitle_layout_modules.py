from __future__ import annotations

import unittest

from src import subtitle_packer
from src.subtitle_layout import rules, tokenize


class SubtitleLayoutModuleTests(unittest.TestCase):
    def test_extracted_rules_match_legacy_packer_constants(self) -> None:
        self.assertEqual(rules.MAX_LINES, subtitle_packer.MAX_LINES)
        self.assertEqual(rules.ELLIPSIS, subtitle_packer.ELLIPSIS)
        self.assertEqual(rules.STRONG_BREAK_CHARS, subtitle_packer.STRONG_BREAK_CHARS)
        self.assertEqual(rules.SOFT_BREAK_CHARS, subtitle_packer.SOFT_BREAK_CHARS)
        self.assertEqual(rules.LEADING_AVOID_CHARS, subtitle_packer.LEADING_AVOID_CHARS)
        self.assertEqual(rules.TRAILING_AVOID_CHARS, subtitle_packer.TRAILING_AVOID_CHARS)
        self.assertEqual(rules.RIGHT_BOUNDARY_AVOID_WORDS, subtitle_packer.RIGHT_BOUNDARY_AVOID_WORDS)
        self.assertEqual(rules.LEFT_BOUNDARY_AVOID_WORDS, subtitle_packer.LEFT_BOUNDARY_AVOID_WORDS)
        self.assertEqual(rules.CLAUSE_BREAK_TOKENS, subtitle_packer.CLAUSE_BREAK_TOKENS)
        self.assertEqual(rules.LEADING_BOUNDARY_PENALTIES, subtitle_packer.LEADING_BOUNDARY_PENALTIES)

    def test_budoux_tokenizer_helper_preserves_legacy_chunk_contract(self) -> None:
        class FakeParser:
            def parse(self, text: str) -> list[str]:
                return ["ab", "", "cd"] if text == "abcd" else []

        self.assertEqual(tokenize.parse_budoux_chunks("abcd", parser=FakeParser()), ["ab", "cd"])
        self.assertEqual(tokenize.parse_budoux_chunks("", parser=FakeParser()), [])
        self.assertEqual(tokenize.parse_budoux_chunks("x", parser=None), subtitle_packer.parse_budoux_chunks("x"))

    def test_morpheme_tokenizer_helper_preserves_legacy_chunk_contract(self) -> None:
        class Token:
            def __init__(self, surface: str) -> None:
                self.surface = surface

        class FakeTokenizer:
            def tokenize(self, text: str) -> list[Token]:
                return [Token("敵"), Token(""), Token("来る")] if text else []

        self.assertEqual(tokenize.parse_morpheme_chunks("敵来る", tokenizer=FakeTokenizer()), ["敵", "来る"])
        self.assertEqual(tokenize.parse_morpheme_chunks("", tokenizer=FakeTokenizer()), [])
        self.assertEqual(tokenize.parse_morpheme_chunks("x", tokenizer=None), subtitle_packer.parse_morpheme_chunks("x"))


if __name__ == "__main__":
    unittest.main()
