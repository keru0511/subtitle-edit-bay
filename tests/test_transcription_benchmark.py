from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.benchmark_transcription import (
    align_characters,
    normalize_text,
    prepare_audio,
    quality_failures,
    score_transcript,
)


class TranscriptionBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = {
            "duration": 20,
            "clips": [
                {"text": "はいはい", "start": 2, "duration": 2},
                {"text": "進みます", "start": 12, "duration": 3},
            ],
            "limits": {
                "max_cer": 0.25,
                "max_cer_regression": 0,
                "timing_tolerance_seconds": 0.4,
                "max_outside_speech_characters": 0,
                "max_untimed_characters": 0,
                "max_timing_window_errors": 0,
            },
        }
        self.payload = {
            "segments": [
                {
                    "text": "はいはい",
                    "start": 2.1,
                    "end": 3.5,
                    "words": [{"word": "はいはい", "start": 2.1, "end": 3.5}],
                },
                {
                    "text": "進みます",
                    "start": 12.2,
                    "end": 14,
                    "words": [{"word": "進みます", "start": 12.2, "end": 14}],
                },
            ]
        }

    def test_normalization_keeps_repetition(self) -> None:
        self.assertEqual(normalize_text("Ａ　はい、はい！いいいいいい"), "aはいはいいいいいいい")

    def test_edit_operations_distinguish_insertions_deletions_and_substitutions(self) -> None:
        cases = [("abc", "abxc", "insert"), ("abc", "ac", "delete"), ("abc", "axc", "substitute")]
        for reference, hypothesis, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    [operation for operation, _, _ in align_characters(reference, hypothesis) if operation != "equal"],
                    [expected],
                )

    def test_correct_text_and_timing_pass(self) -> None:
        result = score_transcript(self.payload, self.manifest)
        self.assertEqual(result["cer"], 0)
        self.assertEqual(quality_failures(result, result, self.manifest["limits"]), [])

    def test_silent_empty_output_cannot_pass_speech_test(self) -> None:
        baseline = score_transcript(self.payload, self.manifest)
        empty = score_transcript({"segments": []}, self.manifest)
        self.assertEqual(empty["cer"], 1)
        self.assertTrue(quality_failures(baseline, empty, self.manifest["limits"]))

    def test_hallucinated_text_in_silence_fails(self) -> None:
        baseline = score_transcript(self.payload, self.manifest)
        self.payload["segments"].insert(
            1, {"text": "いいいいいい", "start": 8, "end": 9, "words": [{"word": "いいいいいい", "start": 8, "end": 9}]}
        )
        result = score_transcript(self.payload, self.manifest)
        self.assertEqual(result["insertions"], 6)
        self.assertEqual(result["outside_speech_characters"], 6)
        self.assertTrue(quality_failures(baseline, result, self.manifest["limits"]))

    def test_correct_text_shifted_to_other_speech_window_fails(self) -> None:
        baseline = score_transcript(self.payload, self.manifest)
        self.payload["segments"][0]["words"][0].update(start=12.2, end=14)
        result = score_transcript(self.payload, self.manifest)
        self.assertEqual(result["cer"], 0)
        self.assertEqual(result["outside_speech_characters"], 0)
        self.assertEqual(result["timing_window_errors"], 4)
        self.assertTrue(quality_failures(baseline, result, self.manifest["limits"]))

    def test_missing_or_nonfinite_word_times_fail(self) -> None:
        baseline = score_transcript(self.payload, self.manifest)
        for update in [{"start": None}, {"end": float("nan")}, {"end": 999}, {"start": 4, "end": 2}]:
            with self.subTest(update=update):
                changed = copy.deepcopy(self.payload)
                changed["segments"][0]["words"][0].update(update)
                result = score_transcript(changed, self.manifest)
                self.assertEqual(result["untimed_characters"], 4)
                self.assertTrue(quality_failures(baseline, result, self.manifest["limits"]))

    def test_missing_words_do_not_hide_segment_text(self) -> None:
        del self.payload["segments"][0]["words"]
        result = score_transcript(self.payload, self.manifest)
        self.assertEqual(result["hypothesis"], "はいはい進みます")
        self.assertEqual(result["untimed_characters"], 4)

    def test_timing_tolerance_is_bounded(self) -> None:
        self.payload["segments"][0]["words"][0].update(start=1.7)
        self.assertEqual(score_transcript(self.payload, self.manifest)["timing_window_errors"], 0)
        self.payload["segments"][0]["words"][0].update(start=1.5)
        self.assertEqual(score_transcript(self.payload, self.manifest)["timing_window_errors"], 4)

    def test_checked_in_audio_hashes_match(self) -> None:
        directory = Path(__file__).resolve().parents[1] / "assets/asr_benchmark"
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["clips"]), 4)
        for clip in manifest["clips"]:
            self.assertEqual(hashlib.sha256((directory / clip["audio"]).read_bytes()).hexdigest(), clip["sha256"])

    def test_corrupted_fixture_fails_before_decoding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "voice.flac").write_bytes(b"corrupted")
            manifest = {
                "sample_rate": 16000,
                "duration": 1,
                "clips": [{"id": "voice", "audio": "voice.flac", "sha256": "bad"}],
            }
            (root / "manifest.json").write_text(json.dumps(manifest))
            with (
                patch("scripts.benchmark_transcription.subprocess.check_output") as decode,
                self.assertRaises(ValueError),
            ):
                prepare_audio(root / "manifest.json", root / "out.wav")
            decode.assert_not_called()
