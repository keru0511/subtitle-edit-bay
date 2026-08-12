from __future__ import annotations

import unittest
from unittest import mock

from src import render_ass
from src import subtitle_line_count
from src import subtitle_packer
from src.subtitle_layout import packer as layout_packer
from src.subtitle_layout import rules, scoring, tokenize


class SubtitleLayoutPackerBridgeTests(unittest.TestCase):
    def test_bridge_binds_legacy_rules_and_tokenizer_factories(self) -> None:
        self.assertIs(subtitle_packer.ELLIPSIS, rules.ELLIPSIS)
        self.assertIs(subtitle_packer.STRONG_BREAK_CHARS, rules.STRONG_BREAK_CHARS)
        self.assertIs(subtitle_packer.SOFT_BREAK_CHARS, rules.SOFT_BREAK_CHARS)
        self.assertIs(subtitle_packer.LEADING_AVOID_CHARS, rules.LEADING_AVOID_CHARS)
        self.assertIs(subtitle_packer.RIGHT_BOUNDARY_AVOID_WORDS, rules.RIGHT_BOUNDARY_AVOID_WORDS)
        self.assertIs(subtitle_packer.create_budoux_parser, tokenize.create_budoux_parser)
        self.assertIs(subtitle_packer.create_janome_tokenizer, tokenize.create_janome_tokenizer)

    def test_bridge_binds_patch_safe_scoring_helpers(self) -> None:
        self.assertIs(subtitle_packer.display_width, scoring.display_width)
        self.assertIs(subtitle_packer.text_width, scoring.text_width)
        self.assertIs(subtitle_packer.duration_pressure, scoring.duration_pressure)
        self.assertIs(subtitle_packer.timing_balance_penalty, scoring.timing_balance_penalty)
        self.assertIs(subtitle_packer.char_bucket, scoring.char_bucket)
        self.assertIs(subtitle_packer.connected_char_penalty, scoring.connected_char_penalty)
        self.assertIs(subtitle_packer.is_protected_inline_split, scoring.is_protected_inline_split)
        self.assertIs(subtitle_packer.chunk_boundaries, scoring.chunk_boundaries)
        self.assertIs(subtitle_packer.clause_break_bonus, scoring.clause_break_bonus)
        self.assertIs(subtitle_packer.leading_boundary_penalty, scoring.leading_boundary_penalty)

    def test_dependency_guard_remains_patchable_through_legacy_names(self) -> None:
        with mock.patch("src.subtitle_packer.create_budoux_parser", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "pip install -r requirements.txt"):
                layout_packer.require_japanese_layout_tools()

    def test_score_break_remains_patchable_through_legacy_names(self) -> None:
        with mock.patch("src.subtitle_packer.candidate_kind_bonus", return_value=-123):
            score = layout_packer.score_break("ABCDEFGHIJKLMN", 7, 8, display_duration=3.0)

        self.assertEqual(score[3], -123)

    def test_line_count_and_render_ass_use_bridge_exports(self) -> None:
        self.assertIs(subtitle_line_count.normalize_text, layout_packer.normalize_text)
        self.assertIs(subtitle_line_count.pack_segment_pages, layout_packer.pack_segment_pages)
        self.assertIs(render_ass.normalize_text, layout_packer.normalize_text)
        self.assertIs(render_ass.MAX_LINES, layout_packer.MAX_LINES)


if __name__ == "__main__":
    unittest.main()
