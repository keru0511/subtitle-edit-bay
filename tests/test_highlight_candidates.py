from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.highlight_candidates import (
    HighlightCancelled,
    HighlightCandidate,
    HighlightSettings,
    _select_diverse,
    generate_highlight_candidates,
    highlight_cache_key,
)
from src.highlight_signals import build_speech_signals


SEGMENTS = [
    {"id": "s1", "start": 10.0, "end": 11.0, "text": "えっ、本当に？", "speaker": "A"},
    {"id": "s2", "start": 11.3, "end": 12.5, "text": "まさかの展開！", "speaker": "B"},
    {"id": "s3", "start": 40.0, "end": 41.0, "text": "次の話題です", "speaker": "A"},
    {"id": "s4", "start": 41.2, "end": 42.0, "text": "説明します", "speaker": "A"},
]


class HighlightCandidateTests(unittest.TestCase):
    def test_signals_normalize_audio_and_fallback_to_subtitles(self) -> None:
        signals = build_speech_signals(
            SEGMENTS,
            [{"start": 10.0, "end": 12.5, "level": 4.0}],
        )
        self.assertEqual(len(signals), 4)
        self.assertGreater(signals[0].level, signals[2].level)
        self.assertEqual(signals[2].source, "subtitle")

    def test_generation_is_deterministic_diverse_and_does_not_mutate_input(self) -> None:
        settings = HighlightSettings(minimum_duration=2.0, top_k=3, diversity_buckets=3)
        original = [dict(item) for item in SEGMENTS]
        first = generate_highlight_candidates(
            SEGMENTS,
            duration_seconds=60,
            settings=settings,
            audio_levels=[{"start": 10, "end": 13, "level": 5}],
        )
        second = generate_highlight_candidates(
            SEGMENTS,
            duration_seconds=60,
            settings=settings,
            audio_levels=[{"start": 10, "end": 13, "level": 5}],
        )

        self.assertEqual([item.to_json() for item in first], [item.to_json() for item in second])
        self.assertEqual(SEGMENTS, original)
        self.assertLessEqual(len(first), 3)
        self.assertTrue(all(item.start < item.end and item.duration <= 45 for item in first))
        self.assertTrue(all(item.reason and item.score_breakdown for item in first))
        self.assertEqual(len({item.source_segment_ids[0] for item in first}), len(first))

    def test_cache_key_changes_with_settings_and_cache_roundtrip(self) -> None:
        settings = HighlightSettings(top_k=2)
        changed = HighlightSettings(top_k=3)
        self.assertNotEqual(highlight_cache_key(SEGMENTS, None, settings), highlight_cache_key(SEGMENTS, None, changed))
        with tempfile.TemporaryDirectory() as temp_dir:
            first = generate_highlight_candidates(SEGMENTS, settings=settings, cache_directory=temp_dir)
            second = generate_highlight_candidates(SEGMENTS, settings=settings, cache_directory=temp_dir)
            self.assertEqual([item.to_json() for item in first], [item.to_json() for item in second])

    def test_audio_level_generators_are_materialized_before_cache_and_scoring(self) -> None:
        audio_levels = [{"start": 10.0, "end": 12.5, "level": 5.0}]
        with tempfile.TemporaryDirectory() as temp_dir:
            from_generator = generate_highlight_candidates(
                SEGMENTS,
                audio_levels=(item for item in audio_levels),
                settings=HighlightSettings(top_k=4),
                cache_directory=temp_dir,
            )
            from_list = generate_highlight_candidates(
                SEGMENTS,
                audio_levels=audio_levels,
                settings=HighlightSettings(top_k=4),
            )
        self.assertEqual([item.to_json() for item in from_generator], [item.to_json() for item in from_list])

    def test_top_one_is_the_global_highest_score_not_the_first_bucket(self) -> None:
        candidates = [
            HighlightCandidate(
                id="early-low",
                start=1.0,
                end=2.0,
                score=0.1,
                category="conversation",
                reason="low",
                subtitle_excerpt="",
                source_segment_ids=("a",),
                score_breakdown={},
            ),
            HighlightCandidate(
                id="late-high",
                start=90.0,
                end=91.0,
                score=0.9,
                category="emphasis",
                reason="high",
                subtitle_excerpt="",
                source_segment_ids=("b",),
                score_breakdown={},
            ),
        ]
        selected = _select_diverse(candidates, HighlightSettings(top_k=1, diversity_buckets=5))
        self.assertEqual([item.id for item in selected], ["late-high"])

    def test_corrupt_cache_is_discarded_and_regenerated(self) -> None:
        settings = HighlightSettings(top_k=2)
        with tempfile.TemporaryDirectory() as temp_dir:
            first = generate_highlight_candidates(SEGMENTS, settings=settings, cache_directory=temp_dir)
            cache_path = next(Path(temp_dir).glob("*.json"))
            cache_path.write_text("{broken", encoding="utf-8")
            regenerated = generate_highlight_candidates(
                SEGMENTS, settings=settings, cache_directory=temp_dir
            )
        self.assertEqual([item.to_json() for item in first], [item.to_json() for item in regenerated])

    def test_cancellation_is_reported(self) -> None:
        with self.assertRaises(HighlightCancelled):
            generate_highlight_candidates(SEGMENTS, cancel_check=lambda: True)


if __name__ == "__main__":
    unittest.main()
