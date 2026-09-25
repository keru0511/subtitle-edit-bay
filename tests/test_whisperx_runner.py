from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

from src.transcribe import build_whisperx_command, run_command_with_utf8_log
from src.whisperx_runner import build_parser, merge_chunks_with_gap, run


class WhisperxRunnerTests(unittest.TestCase):
    def backend(self) -> ModuleType:
        backend = ModuleType("whisperx")
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
            self.assertEqual(options["asr_options"]["repetition_penalty"], 1.0)
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

    def test_gap_split_preserves_short_pauses_absolute_times_and_all_speech(self):
        chunks = [{"start": 74.0, "end": 88.0, "segments": [(74.0, 77.4), (84.0, 85.0), (85.5, 88.0)]}]
        original = json.loads(json.dumps(chunks))
        merge = MagicMock(return_value=chunks)
        result = merge_chunks_with_gap(merge, 1.0, "scores", 15, onset=0.5, offset=0.363)
        self.assertEqual([(chunk["start"], chunk["end"]) for chunk in result], [(74.0, 77.4), (84.0, 88.0)])
        self.assertEqual([span for chunk in result for span in chunk["segments"]], chunks[0]["segments"])
        self.assertEqual(json.loads(json.dumps(chunks)), original)
        merge.assert_called_once_with("scores", 15, onset=0.5, offset=0.363)

    def test_gap_padding_preserves_weak_onsets_without_crossing_long_silence(self):
        chunk = {"start": 74.2, "end": 88.0, "segments": [(74.2, 77.4), (84.3, 88.0)]}
        result = merge_chunks_with_gap(lambda: [chunk], 1.0, padding=0.3)
        self.assertEqual([(item["start"], item["end"]) for item in result], [(74.2, 77.7), (84.0, 88.0)])
        self.assertLess(result[0]["end"], result[1]["start"])
        self.assertEqual(
            merge_chunks_with_gap(lambda: [{**chunk, "segments": [(74.2, 88.0)]}], 1.0, padding=0.3),
            [{**chunk, "segments": [(74.2, 88.0)]}],
        )
        with self.assertRaises(ValueError):
            merge_chunks_with_gap(lambda: [chunk], 1.0, padding=0.6)

    def test_gap_split_handles_silence_overlaps_and_exact_boundary(self):
        self.assertEqual(merge_chunks_with_gap(lambda: [], 1.0), [])
        chunk = {"start": 0.0, "end": 5.0, "segments": [(0.0, 3.0), (1.0, 2.0), (4.0, 5.0)]}
        self.assertEqual(merge_chunks_with_gap(lambda: [chunk], 1.0), [chunk])
        with self.assertRaises(ValueError):
            merge_chunks_with_gap(lambda: [], float("nan"))

    def test_vad_gap_policy_is_installed_before_single_transcription(self):
        backend = self.backend()
        vad = backend.load_model.return_value.vad_model
        original_merge = vad.merge_chunks
        original_merge.return_value = [{"start": 2.0, "end": 17.0, "segments": [(2.0, 5.0), (14.0, 17.0)]}]

        def transcribe(*args, **kwargs):
            chunks = vad.merge_chunks("scores", 15, onset=0.5, offset=0.363)
            self.assertEqual(len(chunks), 2)
            return {"language": "ja", "segments": []}

        backend.load_model.return_value.transcribe.side_effect = transcribe
        with tempfile.TemporaryDirectory() as directory, patch.dict("sys.modules", {"whisperx": backend}):
            command = build_whisperx_command("voice.wav", directory)
            self.assertIn("--max_speech_gap", command)
            run(build_parser().parse_args(command[3:]))
        backend.load_model.return_value.transcribe.assert_called_once()
        original_merge.assert_called_once()

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
                "class Vad:\n"
                "    def merge_chunks(self, *args, **kwargs): return []\n"
                "class Model:\n"
                "    def __init__(self): self.vad_model = Vad()\n"
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
