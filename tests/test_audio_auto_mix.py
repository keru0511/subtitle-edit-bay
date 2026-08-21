from __future__ import annotations

import math
import unittest

from src.audio_auto_mix import (
    build_bgm_ducking_filter,
    build_ducking_envelope,
    estimate_level_db,
    predict_limiter_reduction,
    suggest_channel_gains,
)


class AudioAutoMixTests(unittest.TestCase):
    def test_estimate_level_trims_outlier_and_rejects_short_input(self) -> None:
        self.assertIsNone(estimate_level_db([0.1] * 3))
        level = estimate_level_db([0.1] * 100 + [1.0] * 2)
        self.assertIsNotNone(level)
        self.assertLess(level, -15)

    def test_gain_suggestions_are_bounded_and_respect_exclusion(self) -> None:
        suggestions = suggest_channel_gains(
            {"quiet": [0.05] * 40, "loud": [0.2] * 40, "manual": [0.1] * 40},
            minimum_gain_db=-3,
            maximum_gain_db=3,
            excluded_channels={"manual"},
        )
        by_id = {item.channel_id: item for item in suggestions}
        self.assertTrue(-3 <= by_id["quiet"].gain_db <= 3)
        self.assertTrue(by_id["manual"].excluded)
        self.assertEqual(by_id["manual"].gain_db, 0.0)

    def test_ducking_envelope_and_filter_have_attack_release_and_no_bgm_policy(self) -> None:
        envelope = build_ducking_envelope([(1.0, 2.0)], total_duration=3.0, duck_amount_db=-12)
        self.assertLessEqual(min(item.gain_db for item in envelope), -12)
        filter_graph = build_bgm_ducking_filter(duck_amount_db=-12)
        self.assertIn("sidechaincompress", filter_graph)
        self.assertIn("attack=", filter_graph)
        self.assertIn("release=", filter_graph)

    def test_ducking_envelope_merges_overlapping_and_adjacent_speech(self) -> None:
        envelope = build_ducking_envelope(
            [(1.0, 2.0), (1.5, 3.0), (3.0, 4.0)],
            total_duration=5.0,
            duck_amount_db=-12.0,
            attack_seconds=0.05,
            hold_seconds=0.0,
            release_seconds=0.25,
        )
        self.assertTrue(
            all(
                point.gain_db <= -12.0
                for point in envelope
                if 1.0 <= point.timestamp < 4.0
            )
        )

    def test_ducking_envelope_merges_release_overlap_with_next_attack(self) -> None:
        envelope = build_ducking_envelope(
            [(1.0, 2.0), (2.1, 3.0)],
            total_duration=5.0,
            duck_amount_db=-12.0,
            attack_seconds=0.05,
            hold_seconds=0.1,
            release_seconds=0.25,
        )

        self.assertTrue(
            all(
                point.gain_db <= -12.0
                for point in envelope
                if 1.0 <= point.timestamp < 3.0
            )
        )

    def test_limiter_prediction_and_synthetic_multiple_channels(self) -> None:
        reduction = predict_limiter_reduction({"a": -2.0, "b": -10.0}, {"a": 3.0, "b": 0.0})
        expected_peak_db = 20.0 * math.log10(10.0 ** (1.0 / 20.0) + 10.0 ** (-10.0 / 20.0))
        self.assertAlmostEqual(reduction, expected_peak_db + 1.0)
        suggestions = suggest_channel_gains({"a": [0.1] * 50, "b": [0.11] * 50})
        self.assertEqual(len(suggestions), 2)


if __name__ == "__main__":
    unittest.main()
