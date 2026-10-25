from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from typing import TypedDict
from unittest.mock import patch

from src.data_boundary import is_object_list, is_string_object_mapping
from scripts.benchmark_transcription import (
    align_characters,
    main as benchmark_main,
    merge_reports,
    normalize_text,
    prepare_audio,
    quality_failures,
    score_transcript,
    write_report,
)
from tests.typed_case import TypedTestCase


class _Segment(TypedDict, total=False):
    text: str
    start: object
    end: object
    words: list[dict[str, object]]


class _Payload(TypedDict):
    segments: list[_Segment]


class _Clip(TypedDict, total=False):
    text: str
    start: float
    duration: float
    audio: str


class _SpeechWindow(TypedDict):
    text: str
    start: float
    end: float


class _Limits(TypedDict):
    max_cer: float
    max_cer_regression: float
    timing_tolerance_seconds: float
    max_outside_speech_characters: float
    max_untimed_characters: float
    max_timing_window_errors: float


class _Manifest(TypedDict, total=False):
    duration: float
    clips: list[_Clip]
    limits: _Limits
    equivalent_spellings: dict[str, str]
    speech_windows: list[_SpeechWindow]


class TranscriptionBenchmarkTests(TypedTestCase):
    def setUp(self) -> None:
        self.manifest: _Manifest = {
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
        self.payload: _Payload = {
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

    def paired_reports(self) -> tuple[dict[str, object], dict[str, object]]:
        common: dict[str, object] = {
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
        baseline_score: dict[str, object] = {**score, "commit": "baseline-ref", "elapsed_seconds": 1.0}
        candidate_score: dict[str, object] = {**score, "commit": "candidate-ref", "elapsed_seconds": 1.1}
        return (
            {**copy.deepcopy(common), "baseline": baseline_score},
            {**copy.deepcopy(common), "candidate": candidate_score},
        )

    def test_parallel_results_reject_different_inputs_and_models(self) -> None:
        for key in ["schema_version", "manifest", "audio_sha256", "versions", "model_snapshots"]:
            with self.subTest(key=key):
                baseline, candidate = self.paired_reports()
                candidate[key] = "different"
                with self.assertRaisesRegex(ValueError, key):
                    merge_reports(baseline, candidate)

        baseline, candidate = self.paired_reports()
        baseline["asr_runtime"] = {"model": "large-v3", "device": "cpu", "compute_type": "int8"}
        candidate["asr_runtime"] = {"model": "large-v3", "device": "cuda", "compute_type": "float16"}
        with self.assertRaisesRegex(ValueError, "asr_runtime"):
            merge_reports(baseline, candidate)

    def test_parallel_results_require_both_successful_recognitions(self) -> None:
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

    def test_parallel_comparison_keeps_quality_gate(self) -> None:
        baseline, candidate = self.paired_reports()
        self.assertEqual(merge_reports(baseline, candidate)["failures"], [])
        candidate["candidate"] = score_transcript({"segments": []}, self.manifest)
        self.assertTrue(merge_reports(baseline, candidate)["failures"])

    def test_merge_report_cli_writes_comparison(self) -> None:
        baseline, candidate = self.paired_reports()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline_path = root / "baseline.json"
            candidate_path = root / "candidate.json"
            output = root / "result"
            baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            arguments = [
                "benchmark_transcription.py",
                "--merge-reports",
                str(baseline_path),
                str(candidate_path),
                "--output",
                str(output),
            ]
            previous_summary = os.environ.get("GITHUB_STEP_SUMMARY")
            os.environ["GITHUB_STEP_SUMMARY"] = ""
            try:
                with patch("scripts.benchmark_transcription.sys.argv", arguments), redirect_stdout(io.StringIO()):
                    self.assertEqual(benchmark_main(), 0)
            finally:
                if previous_summary is None:
                    os.environ.pop("GITHUB_STEP_SUMMARY", None)
                else:
                    os.environ["GITHUB_STEP_SUMMARY"] = previous_summary
            raw_report: object = json.loads((output / "report.json").read_text(encoding="utf-8"))
            assert is_string_object_mapping(raw_report)
            self.assertEqual(raw_report["failures"], [])
            self.assertIn("比較対象: `baseline-ref`", (output / "report.md").read_text(encoding="utf-8"))

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

    def test_gameplay_speech_windows_leave_game_audio_as_negative_example(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["clips"] = [{"start": 0, "duration": 20, "audio": "gameplay.wav"}]
        manifest["speech_windows"] = [
            {"text": "はいはい", "start": 2, "end": 4},
            {"text": "進みます", "start": 12, "end": 15},
        ]
        baseline = score_transcript(self.payload, manifest)
        self.assertEqual(baseline["cer"], 0)
        changed = copy.deepcopy(self.payload)
        changed["segments"].insert(
            1,
            {"text": "いいいい", "start": 8, "end": 9, "words": [{"word": "いいいい", "start": 8, "end": 9}]},
        )
        result = score_transcript(changed, manifest)
        self.assertEqual(result["insertions"], 4)
        self.assertEqual(result["outside_speech_characters"], 4)
        self.assertTrue(quality_failures(baseline, result, manifest["limits"]))

    def test_gameplay_speech_windows_reject_overlap_and_out_of_range(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["speech_windows"] = [
            {"text": "はいはい", "start": 2, "end": 4},
            {"text": "進みます", "start": 3, "end": 5},
        ]
        with self.assertRaisesRegex(ValueError, "正解発話"):
            score_transcript(self.payload, manifest)
        manifest["speech_windows"][1]["start"] = 12
        manifest["speech_windows"][1]["end"] = 21
        with self.assertRaisesRegex(ValueError, "正解発話"):
            score_transcript(self.payload, manifest)

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
        updates: list[dict[str, object]] = [
            {"start": None},
            {"end": float("nan")},
            {"end": 999},
            {"start": 4, "end": 2},
        ]
        for update in updates:
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
        payload: _Payload = {
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

    def test_checked_in_audio_hashes_match(self) -> None:
        directory = Path(__file__).resolve().parents[1] / "assets/asr_benchmark"
        raw_manifest: object = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        assert is_string_object_mapping(raw_manifest)
        clips = raw_manifest["clips"]
        assert is_object_list(clips)
        self.assertEqual(len(clips), 4)
        for raw_clip in clips:
            assert is_string_object_mapping(raw_clip)
            audio = raw_clip["audio"]
            sha256 = raw_clip["sha256"]
            assert isinstance(audio, str) and isinstance(sha256, str)
            self.assertEqual(hashlib.sha256((directory / audio).read_bytes()).hexdigest(), sha256)

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

    def test_local_recording_can_select_track_and_extract_a_scene(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "gameplay.mp4"
            source.write_bytes(b"source")
            manifest = {
                "sample_rate": 16000,
                "duration": 1,
                "clips": [
                    {
                        "id": "scene",
                        "audio": source.name,
                        "sha256": hashlib.sha256(b"source").hexdigest(),
                        "start": 0,
                        "duration": 1,
                        "source_start_seconds": 30,
                        "audio_stream": "0:a:1",
                    }
                ],
            }
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            commands: list[list[str]] = []

            def decode(command: list[str]) -> bytes:
                commands.append(command)
                return b"\x00\x00" * 16000

            with patch("scripts.benchmark_transcription.subprocess.check_output", side_effect=decode):
                prepare_audio(path, root / "scene.wav")
            command = commands[0]
            self.assertLess(command.index("-ss"), command.index("-i"))
            self.assertEqual(command[command.index("-map") + 1], "0:a:1")
            self.assertEqual(command[command.index("-ss") + 1], "30.0")
            self.assertEqual(command[command.index("-t") + 1], "1")
            self.assertTrue((root / "scene.wav").exists())

    def test_existing_json_can_be_scored_without_loading_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "manifest.json"
            transcript_path = root / "transcript.json"
            manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
            transcript_path.write_text(json.dumps(self.payload), encoding="utf-8")
            argv = [
                "benchmark_transcription.py",
                "--manifest",
                str(manifest_path),
                "--score-json",
                str(transcript_path),
                "--output",
                str(root / "report"),
            ]
            summary_environment: dict[str, str] = {"GITHUB_STEP_SUMMARY": ""}
            with (
                patch("sys.argv", argv),
                patch("scripts.benchmark_transcription.prepare_audio") as prepare,
                patch("scripts.benchmark_transcription.importlib.metadata.version") as versions,
                patch.dict(os.environ, summary_environment),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(benchmark_main(), 0)
            prepare.assert_not_called()
            versions.assert_not_called()
            raw_report: object = json.loads((root / "report" / "report.json").read_text(encoding="utf-8"))
            assert is_string_object_mapping(raw_report)
            candidate = raw_report["candidate"]
            assert is_string_object_mapping(candidate)
            self.assertEqual(candidate["cer"], 0)
            self.assertTrue(raw_report["scored_existing_json"])
            markdown = (root / "report" / "report.md").read_text(encoding="utf-8")
            self.assertIn("| insertions | 0.0000 |", markdown)
            self.assertIn("実行条件は未検証", markdown)

    def test_gameplay_comparison_report_describes_its_actual_scope(self) -> None:
        baseline, candidate = self.paired_reports()
        manifest = copy.deepcopy(self.manifest)
        manifest["speech_windows"] = [
            {"text": "はいはい", "start": 2, "end": 4},
            {"text": "進みます", "start": 12, "end": 15},
        ]
        baseline["manifest"] = manifest
        candidate["manifest"] = manifest
        report = merge_reports(baseline, candidate)
        summary_environment: dict[str, str] = {"GITHUB_STEP_SUMMARY": ""}
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, summary_environment), redirect_stdout(io.StringIO()):
                write_report(Path(temporary), report)
            markdown = (Path(temporary) / "report.md").read_text(encoding="utf-8")
        self.assertIn("この実況素材の定義した基準で回帰なし", markdown)
        self.assertNotIn("実際のゲーム実況における改善を証明するものではありません", markdown)

    def test_existing_json_failure_writes_diagnostic_without_loading_model(self) -> None:
        invalid_manifests: list[object] = [[], {**self.manifest, "speech_windows": True}]
        for invalid_manifest in invalid_manifests:
            with self.subTest(manifest=invalid_manifest), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                manifest_path = root / "manifest.json"
                transcript_path = root / "transcript.json"
                manifest_path.write_text(json.dumps(invalid_manifest), encoding="utf-8")
                transcript_path.write_text(json.dumps(self.payload), encoding="utf-8")
                argv = [
                    "benchmark_transcription.py",
                    "--manifest",
                    str(manifest_path),
                    "--score-json",
                    str(transcript_path),
                    "--output",
                    str(root / "report"),
                ]
                summary_environment: dict[str, str] = {"GITHUB_STEP_SUMMARY": ""}
                with (
                    patch("sys.argv", argv),
                    patch("scripts.benchmark_transcription.prepare_audio") as prepare,
                    patch("scripts.benchmark_transcription.importlib.metadata.version") as versions,
                    patch.dict(os.environ, summary_environment),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(benchmark_main(), 1)
                prepare.assert_not_called()
                versions.assert_not_called()
                raw_report: object = json.loads((root / "report" / "report.json").read_text(encoding="utf-8"))
                assert is_string_object_mapping(raw_report)
                self.assertTrue(raw_report["scored_existing_json"])
                self.assertNotIn("candidate", raw_report)
                failures = raw_report["failures"]
                assert is_object_list(failures) and isinstance(failures[0], str)
                self.assertIn("既存JSONの採点が完了しませんでした", failures[0])
                markdown = (root / "report" / "report.md").read_text(encoding="utf-8")
                self.assertIn("# 既存の文字起こしJSONの採点", markdown)
                self.assertNotIn("片側の実認識を完了", markdown)

    def test_legacy_report_does_not_invent_runtime_conditions(self) -> None:
        baseline, candidate = self.paired_reports()
        report = merge_reports(baseline, candidate)
        summary_environment: dict[str, str] = {"GITHUB_STEP_SUMMARY": ""}
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, summary_environment), redirect_stdout(io.StringIO()):
                write_report(Path(temporary), report)
            markdown = (Path(temporary) / "report.md").read_text(encoding="utf-8")
        self.assertIn("認識モデル: 実行条件は未記録", markdown)
        self.assertNotIn("CPU int8", markdown)
        self.assertNotIn("既存JSONの実行条件", markdown)
