from __future__ import annotations

import unittest
from unittest import mock

from src.subtitle_packer import normalize_text


def jp(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


class SubtitleLayoutGoldenTests(unittest.TestCase):
    def test_short_reaction_stays_on_one_line(self) -> None:
        self.assertEqual(normalize_text(jp(r"\u3046\u3093"), max_width=12, max_lines=2), jp(r"\u3046\u3093"))

    def test_punctuation_case_keeps_existing_single_line_output(self) -> None:
        text = jp(r"\u3086\u304d\u3068\u3053\u308c\u3069\u3046\u306a\u3063\u3068\u3093\u306e?")

        self.assertEqual(normalize_text(text, max_width=24, max_lines=2), text)

    def test_budoux_boundary_case_keeps_current_two_line_output(self) -> None:
        class FakeParser:
            def parse(self, text: str) -> list[str]:
                return [
                    jp(r"\u3053\u3053\u3067\u6575\u304c\u6765\u308b\u304b\u3089"),
                    jp(r"\u4e00\u56de\u5f15\u3044\u305f\u307b\u3046\u304c\u3044\u3044\u304b\u3082\u3057\u308c\u306a\u3044"),
                ]

        text = jp(
            r"\u3053\u3053\u3067\u6575\u304c\u6765\u308b\u304b\u3089"
            r"\u4e00\u56de\u5f15\u3044\u305f\u307b\u3046\u304c\u3044\u3044\u304b\u3082\u3057\u308c\u306a\u3044"
        )

        with mock.patch("src.subtitle_packer.create_budoux_parser", return_value=FakeParser()):
            wrapped = normalize_text(text, max_width=24, max_lines=2)

        self.assertEqual(
            wrapped,
            jp(r"\u3053\u3053\u3067\u6575\u304c\u6765\u308b\u304b\u3089")
            + r"\N"
            + jp(r"\u4e00\u56de\u5f15\u3044\u305f\u307b\u3046\u304c\u3044\u3044\u304b\u3082\u3057\u308c\u306a\u3044"),
        )

    def test_short_duration_case_keeps_current_balanced_break_output(self) -> None:
        text = "ABCDEFGHIJKLMN"
        with (
            mock.patch("src.subtitle_packer.break_candidates", return_value=[6, 7]),
            mock.patch(
                "src.subtitle_packer.candidate_kind_bonus",
                side_effect=lambda _text, index: -50 if index == 6 else 0,
            ),
        ):
            long_duration = normalize_text(text, max_width=8, max_lines=2, display_duration=3.0)
            short_duration = normalize_text(text, max_width=8, max_lines=2, display_duration=0.4)

        self.assertEqual(long_duration, r"ABCDEF\NGHIJKLMN")
        self.assertEqual(short_duration, r"ABCDEFG\NHIJKLMN")


if __name__ == "__main__":
    unittest.main()
