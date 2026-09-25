from __future__ import annotations

import copy
import hashlib
import math
import io
from contextlib import redirect_stdout
from array import array
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.benchmark_transcription import (
    align_characters,
    apply_effect,
    merge_reports,
    main,
    normalize_text,
    prepare_audio,
    quality_failures,
    score_transcript,
    score_recording,
    score_word_boundaries,
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

    def paired_reports(self):
        common = {
            "schema_version": 1,
            "manifest": self.manifest,
            "audio_sha256": "same-audio",
            "versions": {"whisperx": "3.8.6"},
            "model_snapshots": ["model/revision"],
            "python": "3.10",
            "platform": "Windows",
            "failures": [],
        }
        score = score_transcript(self.payload, self.manifest)
        return (
            {**copy.deepcopy(common), "baseline": score},
            {**copy.deepcopy(common), "candidate": copy.deepcopy(score)},
        )

    def test_parallel_results_reject_different_inputs_and_models(self):
        for key in ["schema_version", "manifest", "audio_sha256", "versions", "model_snapshots"]:
            with self.subTest(key=key):
                baseline, candidate = self.paired_reports()
                candidate[key] = "different"
                with self.assertRaisesRegex(ValueError, key):
                    merge_reports(baseline, candidate)

    def test_parallel_results_require_both_successful_recognitions(self):
        for name in ["baseline", "candidate"]:
            for failure in [False, True]:
                with self.subTest(name=name, failure=failure):
                    baseline, candidate = self.paired_reports()
                    report = baseline if name == "baseline" else candidate
                    if failure:
                        report["failures"] = ["推論に失敗"]
                    else:
                        del report[name]
                    with self.assertRaises(ValueError):
                        merge_reports(baseline, candidate)

    def test_parallel_comparison_keeps_quality_gate(self):
        baseline, candidate = self.paired_reports()
        self.assertEqual(merge_reports(baseline, candidate)["failures"], [])
        candidate["candidate"] = score_transcript({"segments": []}, self.manifest)
        self.assertTrue(merge_reports(baseline, candidate)["failures"])

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

    def test_equivalent_spelling_is_not_a_spoken_deletion(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["clips"] = [{"text": "あとから進みます", "start": 2, "duration": 3}]
        manifest["equivalent_spellings"] = {"後から": "あとから"}
        payload = {
            "segments": [
                {
                    "text": "後から進みます",
                    "start": 2,
                    "end": 4,
                    "words": [{"word": "後から進みます", "start": 2, "end": 4}],
                }
            ]
        }
        result = score_transcript(payload, manifest)
        self.assertGreater(result["raw_cer"], 0)
        self.assertEqual(result["cer"], 0)
        self.assertEqual(result["untimed_characters"], 0)
        payload["segments"][0]["text"] = "後からます"
        payload["segments"][0]["words"][0]["word"] = "後からます"
        missing = score_transcript(payload, manifest)
        self.assertEqual(missing["deletions"], 2)
        self.assertTrue(quality_failures(result, missing, manifest["limits"]))

    def add_word_annotations(self):
        # 評価器の合成テスト。実録音に人手ラベルを付けたという意味ではない。
        self.manifest["limits"]["word_boundary_tolerance_seconds"] = 0.2
        self.manifest["clips"][0]["word_annotation"] = {
            "method": "human",
            "source": "単体テスト用の人工的な正解時刻",
            "words": [{"text": "はいはい", "start": 0.1, "end": 1.5}],
        }

    def test_unannotated_boundaries_are_unknown_not_zero(self):
        result = score_word_boundaries(self.payload, self.manifest)
        self.assertEqual(result["status"], "not_annotated")
        self.assertEqual(result["reference_words"], 0)
        self.assertIsNone(result["max_boundary_error_seconds"])
        self.assertIsNone(result["boundary_errors"])

    def test_word_boundaries_detect_shift_inside_speech_window(self):
        self.add_word_annotations()
        baseline = score_recording(self.payload, self.manifest)
        self.payload["segments"][0]["words"][0].update(start=2.5, end=3.9)
        candidate = score_recording(self.payload, self.manifest)
        self.assertEqual(candidate["cer"], 0)
        self.assertEqual(candidate["timing_window_errors"], 0)
        self.assertEqual(candidate["word_boundaries"]["boundary_errors"], 1)
        self.assertAlmostEqual(candidate["word_boundaries"]["mean_start_error_seconds"], 0.4)
        self.assertTrue(quality_failures(baseline, candidate, self.manifest["limits"]))

    def test_word_boundaries_detect_shortened_ending(self):
        self.add_word_annotations()
        baseline = score_recording(self.payload, self.manifest)
        self.payload["segments"][0]["words"][0]["end"] = 2.5
        candidate = score_recording(self.payload, self.manifest)
        self.assertEqual(candidate["timing_window_errors"], 0)
        self.assertEqual(candidate["word_boundaries"]["shortened_endings"], 1)
        self.assertTrue(quality_failures(baseline, candidate, self.manifest["limits"]))

    def test_word_boundaries_aggregate_character_times_and_report_coverage(self):
        self.add_word_annotations()
        self.payload["segments"][0]["words"] = [
            {"word": "はい", "start": 2.1, "end": 2.6},
            {"word": "はい", "start": 2.8, "end": 3.5},
        ]
        score = score_recording(self.payload, self.manifest)
        boundary = score["word_boundaries"]
        self.assertEqual((boundary["annotated_clips"], boundary["total_clips"]), (1, 2))
        self.assertEqual(boundary["matched_words"], 1)
        self.assertEqual(boundary["max_boundary_error_seconds"], 0)
        self.assertEqual(quality_failures(score, score, self.manifest["limits"]), [])

    def test_word_boundaries_do_not_hide_unmatched_or_untimed_words(self):
        self.add_word_annotations()
        baseline = score_recording(self.payload, self.manifest)
        for changed in [
            {"text": "はい", "words": [{"word": "はい", "start": 2.1, "end": 3.5}]},
            {"text": "はいあはい", "words": [{"word": "はいあはい", "start": 2.1, "end": 3.5}]},
            {"text": "はいはい", "words": []},
        ]:
            with self.subTest(changed=changed):
                payload = copy.deepcopy(self.payload)
                payload["segments"][0].update(changed)
                candidate = score_recording(payload, self.manifest)
                self.assertEqual(candidate["word_boundaries"]["unmatched_words"], 1)
                self.assertIsNone(candidate["word_boundaries"]["max_boundary_error_seconds"])
                self.assertTrue(quality_failures(baseline, candidate, self.manifest["limits"]))

    def test_word_annotations_reject_bad_provenance_text_and_times(self):
        self.add_word_annotations()
        changes = [
            {"method": "forced_alignment"},
            {"source": ""},
            {"words": []},
            {"words": [{"text": "はい", "start": 0.1, "end": 1.5}]},
            {"words": [{"text": "はいはい", "start": 0.1, "end": float("nan")}]},
            {"words": [{"text": "はいはい", "start": 0.1, "end": 2.001}]},
            {"words": [{"text": "はい", "start": 0.1, "end": 1.1}, {"text": "はい", "start": 1.0, "end": 1.5}]},
        ]
        for changed in changes:
            with self.subTest(changed=changed):
                manifest = copy.deepcopy(self.manifest)
                manifest["clips"][0]["word_annotation"].update(changed)
                with self.assertRaises(ValueError):
                    score_word_boundaries(self.payload, manifest)

    def test_word_boundary_equivalent_spelling_preserves_interval(self):
        self.add_word_annotations()
        clip = self.manifest["clips"][0]
        clip["text"] = "あとから"
        clip["word_annotation"]["words"][0]["text"] = "あとから"
        self.manifest["equivalent_spellings"] = {"後から": "あとから"}
        self.payload["segments"][0].update(
            text="後から",
            words=[
                {"word": "後", "start": 2.1, "end": 2.5},
                {"word": "から", "start": 2.5, "end": 3.5},
            ],
        )
        score = score_word_boundaries(self.payload, self.manifest)
        self.assertEqual(score["matched_words"], 1)
        self.assertEqual(score["max_boundary_error_seconds"], 0)

    def test_saved_transcript_scoring_needs_no_model_and_fails_bad_boundaries(self):
        self.add_word_annotations()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            transcript = root / "transcript.json"
            manifest.write_text(json.dumps(self.manifest), encoding="utf-8")
            for shortened in [False, True]:
                with self.subTest(shortened=shortened):
                    if shortened:
                        self.payload["segments"][0]["words"][0]["end"] = 2.5
                    transcript.write_text(json.dumps(self.payload), encoding="utf-8")
                    output = root / str(shortened)
                    arguments = [
                        "benchmark",
                        "--score-transcript",
                        str(transcript),
                        "--manifest",
                        str(manifest),
                        "--output",
                        str(output),
                    ]
                    with (
                        patch("sys.argv", arguments),
                        redirect_stdout(io.StringIO()),
                        patch("scripts.benchmark_transcription.run_revision") as run,
                        patch("scripts.benchmark_transcription.importlib.metadata.version") as version,
                    ):
                        self.assertEqual(main(), int(shortened))
                        run.assert_not_called()
                        version.assert_not_called()
                    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
                    self.assertEqual(report["mode"], "score_only")
                    self.assertEqual(report["transcript_sha256"], hashlib.sha256(transcript.read_bytes()).hexdigest())
                    self.assertIn("保存済みJSON", (output / "report.md").read_text(encoding="utf-8"))

    def test_audio_effects_preserve_length_and_are_deterministic(self):
        source = array("h", [1000, -1000] * 800)
        quiet = apply_effect(source, 16000, {"gain_db": -18})
        self.assertEqual(len(quiet), len(source))
        self.assertAlmostEqual(quiet[0] / source[0], 10 ** (-18 / 20), places=3)
        for effect in [{"noise_snr_db": 15, "seed": 447}, {"tone_snr_db": 15}]:
            first = apply_effect(source, 16000, effect)
            self.assertEqual(first, apply_effect(source, 16000, effect))
            self.assertEqual(len(first), len(source))
            signal = sum(value * value for value in source)
            noise = sum((actual - original) ** 2 for actual, original in zip(first, source))
            self.assertAlmostEqual(10 * math.log10(signal / noise), 15, places=1)
        with self.assertRaises(ValueError):
            apply_effect(source, 16000, {"noize_snr_db": 15})

    def test_effects_do_not_overflow_pcm(self):
        result = apply_effect(array("h", [32767, -32768] * 100), 16000, {"noise_snr_db": 0})
        self.assertTrue(all(-32768 <= value <= 32767 for value in result))

    def test_condition_regression_cannot_hide_in_overall_average(self):
        self.manifest["clips"][0]["condition"] = "clean"
        self.manifest["clips"][1]["condition"] = "low_volume"
        old = copy.deepcopy(self.payload)
        old["segments"][0]["text"] = "はいは"
        old["segments"][0]["words"][0]["word"] = "はいは"
        new = copy.deepcopy(self.payload)
        new["segments"][1]["text"] = "進ます"
        new["segments"][1]["words"][0]["word"] = "進ます"
        baseline = score_recording(old, self.manifest)
        candidate = score_recording(new, self.manifest)
        self.assertEqual(baseline["cer"], candidate["cer"])
        self.assertTrue(
            any("low_volume" in failure for failure in quality_failures(baseline, candidate, self.manifest["limits"]))
        )

    def test_word_stretched_across_silence_keeps_global_text_but_fails_timing(self):
        baseline = score_recording(self.payload, self.manifest)
        self.payload["segments"][0]["words"][0]["end"] = 12.2
        candidate = score_recording(self.payload, self.manifest)
        self.assertEqual(candidate["cer"], 0)
        self.assertGreater(candidate["conditions"]["clean"]["cer"], 0)
        self.assertGreater(candidate["timing_window_errors"], 0)
        self.assertTrue(quality_failures(baseline, candidate, self.manifest["limits"]))

    def test_condition_scoring_does_not_hide_untimed_or_silent_text(self):
        baseline = score_recording(self.payload, self.manifest)
        self.payload["segments"].append({"text": "いいいい", "start": 7, "end": 8, "words": []})
        candidate = score_recording(self.payload, self.manifest)
        self.assertEqual(candidate["untimed_characters"], 4)
        self.assertTrue(quality_failures(baseline, candidate, self.manifest["limits"]))

    def test_checked_in_audio_hashes_match(self) -> None:
        directory = Path(__file__).resolve().parents[1] / "assets/asr_benchmark"
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["clips"]), 8)
        self.assertEqual(
            {clip["condition"] for clip in manifest["clips"]},
            {"clean", "low_volume", "speech_noise", "tonal_background", "band_limited"},
        )
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
