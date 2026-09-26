from __future__ import annotations

import unittest
from unittest import mock

from src import subtitle_packer
from src.subtitle_layout import wrapping


class SubtitleLayoutWrappingTests(unittest.TestCase):
    def test_width_fallback_keeps_existing_public_default(self) -> None:
        self.assertEqual(subtitle_packer.split_by_width("字幕ABC", max_width=4), ["字幕", "ABC"])
        self.assertEqual(subtitle_packer.split_by_width("字幕ABC"), ["字幕ABC"])
        self.assertEqual(wrapping.split_by_width("字幕ABC", 4), ["字幕", "ABC"])

    def test_patched_legacy_parser_still_controls_chunking(self) -> None:
        class FakeParser:
            def parse(self, text: str) -> list[str]:
                return [text[:2], text[2:4], text[4:]]

        with mock.patch("src.subtitle_packer.create_budoux_parser", return_value=FakeParser()):
            self.assertEqual(subtitle_packer.chunk_text("abcdef", 3), ["ab", "cd", "ef"])

    def test_patched_legacy_line_candidate_still_controls_normalization(self) -> None:
        with mock.patch("src.subtitle_packer.build_two_line_candidate", return_value=r"ABCD\NEFGH"):
            self.assertEqual(subtitle_packer.normalize_text("ABCDEFGH", max_width=4), r"ABCD\NEFGH")


if __name__ == "__main__":
    unittest.main()
