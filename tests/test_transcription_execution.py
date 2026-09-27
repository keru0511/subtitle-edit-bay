from __future__ import annotations

import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypedDict
from unittest.mock import patch

from typing_extensions import Unpack

from src.data_boundary import decode_json, is_string_object_mapping
from src.transcript_cache import transcript_cache_metadata_path, write_transcript_cache_metadata
from src.transcription_execution import TranscriptionExecutionResult, transcribe_audio_with_cache
from tests.typed_case import TypedTestCase


class TranscriptionOptions(TypedDict, total=False):
    model: str
    device: str
    compute_type: str
    language: str | None
    vad_onset: float | None
    vad_offset: float | None
    initial_prompt: str | None
    hotwords: Sequence[str] | str | None
    skip_existing: bool
    cache_fingerprint: str | None
    cache_settings: Mapping[str, object] | None


def _mapping(value: object) -> Mapping[str, object]:
    assert is_string_object_mapping(value)
    return value


class TranscriptionExecutionTests(TypedTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.audio = self.output / "voice.wav"
        self.audio.write_bytes(b"test audio")
        self.transcript = self.output / "voice.json"
        self.runner_calls: list[list[str]] = []
        self.runner_error: Exception | None = None
        self.create_output = True
        runner_patch = patch("src.transcription_execution.run_command_with_utf8_log", side_effect=self.write_result)
        runner_patch.start()
        self.addCleanup(runner_patch.stop)

    def write_result(self, command: list[str], log_path: str) -> None:
        self.runner_calls.append(command)
        if self.runner_error is not None:
            raise self.runner_error
        if self.create_output:
            self.transcript.write_text('{"segments": []}', encoding="utf-8")

    def transcribe(self, **kwargs: Unpack[TranscriptionOptions]) -> TranscriptionExecutionResult:
        return transcribe_audio_with_cache(str(self.audio), str(self.output), **kwargs)

    def test_legacy_transcript_is_regenerated_once_then_reused(self) -> None:
        self.transcript.write_text('{"segments": [{"text": "いいいいいい"}]}')
        first = self.transcribe()
        second = self.transcribe()
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(len(self.runner_calls), 1)
        self.assertIsNotNone(first.cache_metadata_path)

    def test_context_metadata_is_not_enough_without_execution_settings(self) -> None:
        self.transcript.write_text('{"segments": []}', encoding="utf-8")
        write_transcript_cache_metadata(self.transcript, fingerprint="context-v1")
        self.assertFalse(self.transcribe(cache_fingerprint="context-v1").cache_hit)
        self.assertTrue(self.transcribe(cache_fingerprint="context-v1").cache_hit)

    def test_model_vad_and_hints_invalidate_cache(self) -> None:
        changes: list[TranscriptionOptions] = [
            {"model": "large-v2"},
            {"vad_onset": 0.6},
            {"vad_offset": 0.3},
            {"initial_prompt": "ゲーム実況"},
            {"hotwords": ["クラベス"]},
            {"cache_fingerprint": "new"},
        ]
        for changed in changes:
            with self.subTest(changed=changed):
                self.transcribe()
                self.assertFalse(self.transcribe(**changed).cache_hit)
                self.assertTrue(self.transcribe(**changed).cache_hit)

    def test_changed_input_invalidates_cache(self) -> None:
        self.transcribe()
        self.audio.write_bytes(b"different audio contents")
        self.assertFalse(self.transcribe().cache_hit)

    def test_changed_profile_or_runtime_invalidates_cache(self) -> None:
        self.transcribe()
        next_profile: dict[str, str | int | float] = {"version": "next"}
        with patch("src.transcription_execution.first_pass_profile", return_value=next_profile):
            self.assertFalse(self.transcribe().cache_hit)
        with patch("src.transcription_execution.version", return_value="next-runtime"):
            self.assertFalse(self.transcribe().cache_hit)

    def test_metadata_keeps_execution_and_caller_settings(self) -> None:
        result = self.transcribe(cache_settings={"model": "large-v3"})
        assert result.cache_metadata_path is not None
        metadata = _mapping(decode_json(result.cache_metadata_path.read_text()))
        settings = _mapping(metadata["settings"])
        execution = _mapping(settings["execution"])
        profile = _mapping(execution["profile"])
        self.assertEqual(settings["model"], "large-v3")
        self.assertEqual(profile["repetition_penalty"], 1.1)

    def test_failure_invalidates_previous_metadata(self) -> None:
        self.transcribe()
        self.runner_error = RuntimeError("failed")
        with self.assertRaises(RuntimeError):
            self.transcribe(skip_existing=False)
        self.assertFalse(transcript_cache_metadata_path(self.transcript).exists())
        self.runner_error = None
        self.assertFalse(self.transcribe().cache_hit)

    def test_missing_output_never_creates_valid_metadata(self) -> None:
        self.create_output = False
        with self.assertRaises(FileNotFoundError):
            self.transcribe()
        self.assertFalse(transcript_cache_metadata_path(self.transcript).exists())

    def test_hints_reach_first_pass_command(self) -> None:
        self.transcribe(initial_prompt="ゲーム実況", hotwords=["クラベス", "クラベス"], skip_existing=False)
        command = self.runner_calls[-1]
        self.assertIn("ゲーム実況", command)
        self.assertEqual(command[command.index("--hotwords") + 1], "クラベス")
