from __future__ import annotations

import json
import itertools
import math
import os
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

from src.transcribe import build_whisperx_command, run_command_with_utf8_log
from src.whisperx_runner import build_parser, run
from src.ctc_alignment import blank_aware_alignment, blank_aware_backtrack, ctc_path


class WhisperxRunnerTests(unittest.TestCase):
    def backend(self) -> ModuleType:
        backend = ModuleType("whisperx")
        backend.alignment = ModuleType("whisperx.alignment")
        backend.alignment.backtrack = MagicMock()
        backend.load_audio = MagicMock(return_value=object())
        backend.load_model = MagicMock()
        backend.load_model.return_value.transcribe.return_value = {
            "language": "ja",
            "segments": [{"start": 12.0, "end": 13.0, "text": "いいですね"}],
        }
        backend.load_align_model = MagicMock(return_value=(object(), {"language": "ja"}))
        backend.align = MagicMock(
            return_value={
                "segments": [
                    {
                        "start": 12.2,
                        "end": 12.9,
                        "text": "いいですね",
                        "words": [
                            {"word": "いいですね", "start": 12.2, "end": 12.9},
                        ],
                    }
                ],
            }
        )
        return backend

    def test_command_reaches_generation_and_alignment_with_original_timebase(self) -> None:
        backend = self.backend()
        with tempfile.TemporaryDirectory() as directory, patch.dict("sys.modules", {"whisperx": backend}):
            command = build_whisperx_command("voice.wav", directory, initial_prompt="ゲーム実況", hotwords=["クラベス"])
            self.assertEqual(command[2], "src.whisperx_runner")
            output = run(build_parser().parse_args(command[3:]))
            options = backend.load_model.call_args.kwargs
            self.assertEqual(options["asr_options"]["beam_size"], 10)
            self.assertEqual(options["asr_options"]["repetition_penalty"], 1.2)
            self.assertEqual(options["asr_options"]["no_repeat_ngram_size"], 0)
            self.assertFalse(options["asr_options"]["condition_on_previous_text"])
            self.assertEqual(options["asr_options"]["initial_prompt"], "ゲーム実況")
            self.assertEqual(options["asr_options"]["hotwords"], "クラベス")
            self.assertEqual(options["vad_options"], {"chunk_size": 15, "vad_onset": 0.5, "vad_offset": 0.363})
            backend.load_model.return_value.transcribe.assert_called_once_with(
                backend.load_audio.return_value,
                batch_size=8,
                chunk_size=15,
                print_progress=True,
            )
            self.assertIs(backend.align.call_args.args[3], backend.load_audio.return_value)
            self.assertEqual(backend.align.call_args.args[0][0]["start"], 12.0)
            data = json.loads(output.read_text())
            self.assertEqual(data["segments"][0]["start"], 12.2)
            self.assertEqual(data["segments"][0]["words"][0]["end"], 12.9)
            self.assertEqual(data["language"], "ja")

    def test_ctc_does_not_assign_long_blank_to_previous_character(self):
        import numpy as np

        labels = [0, 1, 1] + [0] * 80 + [2, 2, 0]
        probabilities = np.full((len(labels), 3), 0.001)
        probabilities[np.arange(len(labels)), labels] = 0.998
        path = ctc_path(np.log(probabilities), [1, 2])
        self.assertEqual([point.time_index for point in path if point.token_index == 0], [1, 2])
        self.assertEqual([point.time_index for point in path if point.token_index == 1], [83, 84])

    def test_ctc_preserves_repeated_characters(self):
        import numpy as np

        probabilities = np.array([[0.01, 0.99], [0.99, 0.01], [0.01, 0.99]])
        path = ctc_path(np.log(probabilities), [1, 1])
        self.assertEqual([(point.token_index, point.time_index) for point in path], [(0, 0), (1, 2)])
        self.assertIsNone(ctc_path(np.log(probabilities[:2]), [1, 1]))

    def test_ctc_matches_exhaustive_paths(self):
        import numpy as np

        randomizer = np.random.default_rng(456)
        for target in [[1], [2], [1, 1], [1, 2], [2, 1], [1, 2, 1]]:
            with self.subTest(target=target):
                probabilities = randomizer.random((5, 3))
                emission = np.log(probabilities / probabilities.sum(axis=1, keepdims=True))
                best = -math.inf
                for labels in itertools.product(range(3), repeat=5):
                    collapsed = [label for label, _ in itertools.groupby(labels) if label != 0]
                    if collapsed == target:
                        best = max(best, sum(emission[index, label] for index, label in enumerate(labels)))
                path = ctc_path(emission, target)
                actual = [0] * 5
                for point in path:
                    actual[point.time_index] = target[point.token_index]
                score = sum(emission[index, label] for index, label in enumerate(actual))
                self.assertAlmostEqual(score, best)

    def test_ctc_adapter_is_restored_after_failure(self):
        backend = self.backend()
        original = backend.alignment.backtrack
        with self.assertRaisesRegex(RuntimeError, "failed"):
            with blank_aware_alignment(backend.alignment):
                self.assertIs(backend.alignment.backtrack, blank_aware_backtrack)
                raise RuntimeError("failed")
        self.assertIs(backend.alignment.backtrack, original)

    def test_silent_audio_produces_empty_result_without_alignment(self) -> None:
        backend = self.backend()
        backend.load_model.return_value.transcribe.return_value = {"language": "ja", "segments": []}
        with tempfile.TemporaryDirectory() as directory, patch.dict("sys.modules", {"whisperx": backend}):
            output = run(build_parser().parse_args(["silent.wav", "--output_dir", directory]))
            self.assertEqual(json.loads(output.read_text())["segments"], [])
            backend.load_align_model.assert_not_called()

    def test_explicit_vad_values_are_preserved(self) -> None:
        backend = self.backend()
        with tempfile.TemporaryDirectory() as directory, patch.dict("sys.modules", {"whisperx": backend}):
            command = build_whisperx_command("voice.wav", directory, vad_onset=0.4, vad_offset=0.25)
            run(build_parser().parse_args(command[3:]))
            self.assertEqual(backend.load_model.call_args.kwargs["vad_options"]["vad_onset"], 0.4)
            self.assertEqual(backend.load_model.call_args.kwargs["vad_options"]["vad_offset"], 0.25)

    def test_alignment_failure_does_not_overwrite_previous_result(self) -> None:
        backend = self.backend()
        backend.align.side_effect = RuntimeError("alignment failed")
        with tempfile.TemporaryDirectory() as directory, patch.dict("sys.modules", {"whisperx": backend}):
            output = Path(directory) / "voice.json"
            output.write_text("previous")
            with self.assertRaises(RuntimeError):
                run(build_parser().parse_args(["voice.wav", "--output_dir", directory]))
            self.assertEqual(output.read_text(), "previous")

    def test_diarization_uses_token_without_command_line_exposure(self) -> None:
        backend = self.backend()
        diarization = ModuleType("whisperx.diarize")
        diarization.DiarizationPipeline = MagicMock()
        diarization.assign_word_speakers = MagicMock(side_effect=lambda speakers, result: result)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"HF_TOKEN": "test-token"}),
            patch.dict(
                "sys.modules",
                {"whisperx": backend, "whisperx.diarize": diarization},
            ),
        ):
            command = build_whisperx_command("voice.wav", directory, diarize=True, min_speakers=2, max_speakers=3)
            self.assertNotIn("test-token", command)
            run(build_parser().parse_args(command[3:]))
            diarization.DiarizationPipeline.assert_called_once_with(token="test-token", device="cpu")
            diarization.DiarizationPipeline.return_value.assert_called_once_with(
                backend.load_audio.return_value,
                min_speakers=2,
                max_speakers=3,
            )

    def test_runner_subprocess_is_available_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "whisperx.py").write_text(
                "def load_audio(path): return []\n"
                "class Model:\n"
                "    def transcribe(self, audio, **kwargs): return {'language': 'ja', 'segments': []}\n"
                "def load_model(*args, **kwargs): return Model()\n"
            )
            previous = Path.cwd()
            try:
                os.chdir(root)
                with patch.dict("os.environ", {"PYTHONPATH": str(root)}):
                    run_command_with_utf8_log(build_whisperx_command("voice.wav", directory), str(root / "run.log"))
            finally:
                os.chdir(previous)
            self.assertEqual(json.loads((root / "voice.json").read_text())["segments"], [])

    def test_invalid_vad_rejected_before_model_loading(self) -> None:
        backend = self.backend()
        with patch.dict("sys.modules", {"whisperx": backend}), self.assertRaises(ValueError):
            run(
                build_parser().parse_args(
                    ["voice.wav", "--output_dir", ".", "--vad_onset", "0.1", "--vad_offset", "0.4"]
                )
            )
        backend.load_model.assert_not_called()
