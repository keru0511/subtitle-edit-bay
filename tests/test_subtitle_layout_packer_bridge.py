from __future__ import annotations

import unittest
from unittest import mock

from src import render_ass
from src import subtitle_line_count
from src import subtitle_packer
from src.subtitle_layout import packer as layout_packer
from src.subtitle_layout import rules, tokenize


class SubtitleLayoutPackerBridgeTests(unittest.TestCase):
    def test_bridge_binds_legacy_rules_and_tokenizer_factories(self) -> None:
        self.assertIs(subtitle_packer.ELLIPSIS, rules.ELLIPSIS)
        self.assertIs(subtitle_packer.STRONG_BREAK_CHARS, rules.STRONG_BREAK_CHARS)
        self.assertIs(subtitle_packer.SOFT_BREAK_CHARS, rules.SOFT_BREAK_CHARS)
        self.assertIs(subtitle_packer.LEADING_AVOID_CHARS, rules.LEADING_AVOID_CHARS)
        self.assertIs(subtitle_packer.RIGHT_BOUNDARY_AVOID_WORDS, rules.RIGHT_BOUNDARY_AVOID_WORDS)
        self.assertIs(subtitle_packer.create_budoux_parser, tokenize.create_budoux_parser)
        self.assertIs(subtitle_packer.create_janome_tokenizer, tokenize.create_janome_tokenizer)

    def test_dependency_guard_remains_patchable_through_legacy_names(self) -> None:
        with mock.patch("src.subtitle_packer.create_budoux_parser", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "pip install -r requirements.txt"):
                layout_packer.require_japanese_layout_tools()

    def test_line_count_and_render_ass_use_bridge_exports(self) -> None:
        self.assertIs(subtitle_line_count.normalize_text, layout_packer.normalize_text)
        self.assertIs(subtitle_line_count.pack_segment_pages, layout_packer.pack_segment_pages)
        self.assertIs(render_ass.normalize_text, layout_packer.normalize_text)
        self.assertIs(render_ass.MAX_LINES, layout_packer.MAX_LINES)


if __name__ == "__main__":
    unittest.main()
