from __future__ import annotations

import unittest
from unittest import mock

from src.subtitle_layout import scoring


def jp(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


class SubtitleLayoutCandidateExplanationTests(unittest.TestCase):
    def setUp(self) -> None:
        scoring.text_width.cache_clear()
        scoring.budoux_boundaries.cache_clear()
        scoring.morpheme_boundaries.cache_clear()

    def test_explanation_exposes_score_break_components(self) -> None:
        text = "ABCDEFGHIJKLMN"

        explanation = scoring.explain_split_candidate(
            text,
            7,
            max_width=8,
            display_duration=0.4,
        )

        assert explanation is not None
        self.assertEqual(explanation.left, "ABCDEFG")
        self.assertEqual(explanation.right, "HIJKLMN")
        self.assertEqual(explanation.left_width, 7)
        self.assertEqual(explanation.right_width, 7)
        self.assertEqual(
            explanation.score,
            scoring.score_break(text, 7, 8, display_duration=0.4),
        )
        self.assertEqual(explanation.overflow_penalty, explanation.score[0])
        self.assertEqual(explanation.timing_penalty, explanation.score[1])
        self.assertEqual(explanation.tiny_line_penalty, explanation.score[2])
        self.assertEqual(explanation.candidate_bonus, explanation.score[3])
        self.assertEqual(explanation.boundary_penalty, explanation.score[4])
        self.assertEqual(explanation.width_balance_penalty, explanation.score[5])
        self.assertEqual(explanation.natural_midpoint_penalty, explanation.score[6])

    def test_explanations_are_sorted_by_score_and_skip_invalid_indices(self) -> None:
        explanations = scoring.explain_split_candidates(
            "ABCDEFGHIJKLMN",
            [0, 6, 7, 14],
            max_width=8,
            display_duration=0.4,
        )

        self.assertEqual([item.break_index for item in explanations], [7, 6])
        self.assertLess(explanations[0].score, explanations[1].score)

    def test_explanation_marks_natural_boundaries(self) -> None:
        class FakeParser:
            def parse(self, text: str) -> list[str]:
                return ["ab", "cd", "ef"] if text == "abcdef" else [text]

        with (
            mock.patch("src.subtitle_layout.tokenize.create_budoux_parser", return_value=FakeParser()),
            mock.patch("src.subtitle_layout.tokenize.create_janome_tokenizer", return_value=None),
        ):
            scoring.budoux_boundaries.cache_clear()
            scoring.morpheme_boundaries.cache_clear()
            explanation = scoring.explain_split_candidate("abcdef", 2, max_width=4)

        assert explanation is not None
        self.assertTrue(explanation.is_budoux_boundary)
        self.assertFalse(explanation.is_morpheme_boundary)
        self.assertLess(explanation.candidate_bonus, 0)

    def test_empty_side_candidate_is_not_explained(self) -> None:
        text = jp(r"\u3053\u3053\u3067")

        self.assertIsNone(scoring.explain_split_candidate(text, 0, max_width=8))
        self.assertIsNone(scoring.explain_split_candidate(text, len(text), max_width=8))


if __name__ == "__main__":
    unittest.main()
