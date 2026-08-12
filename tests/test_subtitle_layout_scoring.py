from __future__ import annotations

import unittest
from unittest import mock

from src.subtitle_layout import packer as layout_packer
from src.subtitle_layout import scoring


def jp(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


class SubtitleLayoutScoringTests(unittest.TestCase):
    def setUp(self) -> None:
        layout_packer.text_width.cache_clear()
        layout_packer.budoux_boundaries.cache_clear()
        layout_packer.morpheme_boundaries.cache_clear()
        scoring.text_width.cache_clear()
        scoring.budoux_boundaries.cache_clear()
        scoring.morpheme_boundaries.cache_clear()

    def test_width_helpers_match_legacy_packer(self) -> None:
        for text in (
            "ASCII",
            jp(r"\u3053\u3053\u3067OBS\u3092\u4f7f\u3046"),
            jp(r"\u30b9\u30d7\u30e9\u30c8\u30a5\u30fc\u30f33"),
        ):
            self.assertEqual(scoring.text_width(text), layout_packer.text_width(text))

    def test_connected_char_penalties_match_legacy_packer(self) -> None:
        for previous_char, next_char in (
            ("A", "B"),
            ("1", "2"),
            (jp(r"\u304b"), jp(r"\u306a")),
            (jp(r"\u6f22"), jp(r"\u5b57")),
            (jp(r"\u6f22"), jp(r"\u3060")),
            (jp(r"\u304b"), jp(r"\u6f22")),
            (jp(r"\u3093"), jp(r"\u3060")),
        ):
            self.assertEqual(
                scoring.connected_char_penalty(previous_char, next_char),
                layout_packer.connected_char_penalty(previous_char, next_char),
            )

    def test_boundary_helpers_match_legacy_packer(self) -> None:
        class FakeParser:
            def parse(self, text: str) -> list[str]:
                return ["ab", "cd", "ef"] if text == "abcdef" else [text]

        with (
            mock.patch("src.subtitle_layout.tokenize.create_budoux_parser", return_value=FakeParser()),
            mock.patch("src.subtitle_packer.create_budoux_parser", return_value=FakeParser()),
        ):
            scoring.budoux_boundaries.cache_clear()
            layout_packer.budoux_boundaries.cache_clear()
            self.assertEqual(scoring.budoux_boundaries("abcdef"), {2, 4})
            self.assertEqual(
                scoring.budoux_boundaries("abcdef"),
                layout_packer.budoux_boundaries("abcdef"),
            )

    def test_candidate_bonus_and_leading_penalty_match_legacy_packer(self) -> None:
        text = jp(
            r"\u3053\u3053\u3067\u6575\u304c\u6765\u308b\u304b\u3089"
            r"\u4e00\u56de\u5f15\u3044\u305f\u307b\u3046\u304c\u3044\u3044"
        )
        break_index = len(jp(r"\u3053\u3053\u3067\u6575\u304c\u6765\u308b\u304b\u3089"))

        self.assertEqual(
            scoring.candidate_kind_bonus(text, break_index),
            layout_packer.candidate_kind_bonus(text, break_index),
        )
        self.assertEqual(
            scoring.leading_boundary_penalty(text, break_index),
            layout_packer.leading_boundary_penalty(text, break_index),
        )

    def test_score_break_matches_legacy_packer(self) -> None:
        cases = (
            ("ABCDEFGHIJKLMN", 7, 8, 0.4),
            (jp(r"\u3053\u3053\u3067\u6575\u304c\u6765\u308b\u304b\u3089\u4e00\u56de\u5f15\u304f"), 12, 18, 2.0),
            (jp(r"\u305d\u308c\u306f\u4eca\u3084\u308b\u306e\u306f\u5371\u306a\u3044"), 8, 16, None),
        )

        for text, break_index, max_width, display_duration in cases:
            self.assertEqual(
                scoring.score_break(
                    text,
                    break_index,
                    max_width,
                    display_duration=display_duration,
                ),
                layout_packer.score_break(
                    text,
                    break_index,
                    max_width,
                    display_duration=display_duration,
                ),
            )
            self.assertEqual(
                scoring.score_truncated_break(
                    text,
                    break_index,
                    max_width,
                    display_duration=display_duration,
                ),
                layout_packer.score_truncated_break(
                    text,
                    break_index,
                    max_width,
                    display_duration=display_duration,
                ),
            )


if __name__ == "__main__":
    unittest.main()
