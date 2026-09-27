from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Mapping
from unittest.mock import patch

from src.data_boundary import decode_json, is_object_list, is_string_object_mapping
from src.transcribe import build_whisperx_command, run_command_with_utf8_log
from src.whisperx_runner import parse_args, run
from tests.typed_case import TypedTestCase


def _mapping(value: object) -> Mapping[str, object]:
    assert is_string_object_mapping(value)
    return value


def _list(value: object) -> list[object]:
    assert is_object_list(value)
    return value


class FakeModel:
    def __init__(self) -> None:
        self.result: dict[str, object] = {
            "language": "ja",
            "segments": [{"start": 12.0, "end": 13.0, "text": "いいですね"}],
        }
        self.calls: list[tuple[object, dict[str, object]]] = []

    def transcribe(self, audio: object, **kwargs: object) -> dict[str, object]:
        self.calls.append((audio, kwargs))
        return dict(self.result)


class FakeBackend(ModuleType):
    def __init__(self) -> None:
        super().__init__("whisperx")
        self.audio = object()
        self.model = FakeModel()
        self.load_model_options: list[dict[str, object]] = []
        self.align_calls: list[tuple[object, ...]] = []
        self.load_align_model_calls = 0
        self.align_failure: Exception | None = None
        self.align_result: dict[str, object] = {
            "segments": [
                {
                    "start": 12.2,
                    "end": 12.9,
                    "text": "いいですね",
                    "words": [{"word": "いいですね", "start": 12.2, "end": 12.9}],
                }
            ],
        }

    def load_audio(self, path: str) -> object:
        return self.audio

    def load_model(self, model: str, **kwargs: object) -> FakeModel:
        self.load_model_options.append({"model": model, **kwargs})
        return self.model

    def load_align_model(self, *, language_code: str, device: str) -> tuple[object, object]:
        self.load_align_model_calls += 1
        return object(), {"language": language_code}

    def align(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.align_calls.append(args)
        if self.align_failure is not None:
            raise self.align_failure
        return dict(self.align_result)


class FakeDiarizer:
    def __init__(self) -> None:
        self.calls: list[tuple[object, dict[str, object]]] = []

    def __call__(self, audio: object, **kwargs: object) -> object:
        self.calls.append((audio, kwargs))
        return object()


class FakeDiarization(ModuleType):
    def __init__(self) -> None:
        super().__init__("whisperx.diarize")
        self.pipeline_options: list[dict[str, object]] = []
        self.diarizer = FakeDiarizer()

    def DiarizationPipeline(self, *, token: str, device: str) -> FakeDiarizer:
        self.pipeline_options.append({"token": token, "device": device})
        return self.diarizer

    def assign_word_speakers(self, speakers: object, result: Mapping[str, object]) -> dict[str, object]:
        return dict(result)


class WhisperxRunnerTests(TypedTestCase):
    def test_command_reaches_generation_and_alignment_with_original_timebase(self) -> None:
        backend = FakeBackend()
        modules: dict[str, ModuleType] = {"whisperx": backend}
        with tempfile.TemporaryDirectory() as directory, patch.dict("sys.modules", modules):
            command = build_whisperx_command("voice.wav", directory, initial_prompt="ゲーム実況", hotwords=["クラベス"])
            self.assertEqual(command[2], "src.whisperx_runner")
            output = run(parse_args(command[3:]))
            options = backend.load_model_options[-1]
            asr_options = _mapping(options["asr_options"])
            self.assertEqual(asr_options["beam_size"], 10)
            self.assertEqual(asr_options["repetition_penalty"], 1.1)
            self.assertEqual(asr_options["no_repeat_ngram_size"], 0)
            self.assertFalse(asr_options["condition_on_previous_text"])
            self.assertEqual(asr_options["initial_prompt"], "ゲーム実況")
            self.assertEqual(asr_options["hotwords"], "クラベス")
            self.assertEqual(options["vad_options"], {"chunk_size": 15, "vad_onset": 0.5, "vad_offset": 0.363})
            self.assertEqual(
                backend.model.calls,
                [(backend.audio, {"batch_size": 8, "chunk_size": 15, "print_progress": True})],
            )
            self.assertIs(backend.align_calls[0][3], backend.audio)
            self.assertEqual(_mapping(_list(backend.align_calls[0][0])[0])["start"], 12.0)
            data = _mapping(decode_json(output.read_text()))
            segment = _mapping(_list(data["segments"])[0])
            self.assertEqual(segment["start"], 12.2)
            self.assertEqual(_mapping(_list(segment["words"])[0])["end"], 12.9)
            self.assertEqual(data["language"], "ja")

    def test_silent_audio_produces_empty_result_without_alignment(self) -> None:
        backend = FakeBackend()
        backend.model.result = {"language": "ja", "segments": []}
        modules: dict[str, ModuleType] = {"whisperx": backend}
        with tempfile.TemporaryDirectory() as directory, patch.dict("sys.modules", modules):
            output = run(parse_args(["silent.wav", "--output_dir", directory]))
            self.assertEqual(_mapping(decode_json(output.read_text()))["segments"], [])
            self.assertEqual(backend.load_align_model_calls, 0)

    def test_explicit_vad_values_are_preserved(self) -> None:
        backend = FakeBackend()
        modules: dict[str, ModuleType] = {"whisperx": backend}
        with tempfile.TemporaryDirectory() as directory, patch.dict("sys.modules", modules):
            command = build_whisperx_command("voice.wav", directory, vad_onset=0.4, vad_offset=0.25)
            run(parse_args(command[3:]))
            options = _mapping(backend.load_model_options[-1]["vad_options"])
            self.assertEqual(options["vad_onset"], 0.4)
            self.assertEqual(options["vad_offset"], 0.25)

    def test_alignment_failure_does_not_overwrite_previous_result(self) -> None:
        backend = FakeBackend()
        backend.align_failure = RuntimeError("alignment failed")
        modules: dict[str, ModuleType] = {"whisperx": backend}
        with tempfile.TemporaryDirectory() as directory, patch.dict("sys.modules", modules):
            output = Path(directory) / "voice.json"
            output.write_text("previous")
            with self.assertRaises(RuntimeError):
                run(parse_args(["voice.wav", "--output_dir", directory]))
            self.assertEqual(output.read_text(), "previous")

    def test_diarization_uses_token_without_command_line_exposure(self) -> None:
        backend = FakeBackend()
        diarization = FakeDiarization()
        environment: dict[str, str] = {"HF_TOKEN": "test-token"}
        modules: dict[str, ModuleType] = {"whisperx": backend, "whisperx.diarize": diarization}
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", environment),
            patch.dict("sys.modules", modules),
        ):
            command = build_whisperx_command("voice.wav", directory, diarize=True, min_speakers=2, max_speakers=3)
            self.assertNotIn("test-token", command)
            run(parse_args(command[3:]))
            self.assertEqual(diarization.pipeline_options, [{"token": "test-token", "device": "cpu"}])
            self.assertEqual(
                diarization.diarizer.calls,
                [(backend.audio, {"min_speakers": 2, "max_speakers": 3})],
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
                environment: dict[str, str] = {"PYTHONPATH": str(root)}
                with patch.dict("os.environ", environment):
                    run_command_with_utf8_log(build_whisperx_command("voice.wav", directory), str(root / "run.log"))
            finally:
                os.chdir(previous)
            self.assertEqual(_mapping(decode_json((root / "voice.json").read_text()))["segments"], [])

    def test_invalid_vad_rejected_before_model_loading(self) -> None:
        backend = FakeBackend()
        modules: dict[str, ModuleType] = {"whisperx": backend}
        with patch.dict("sys.modules", modules), self.assertRaises(ValueError):
            run(parse_args(["voice.wav", "--output_dir", ".", "--vad_onset", "0.1", "--vad_offset", "0.4"]))
        self.assertEqual(backend.load_model_options, [])
