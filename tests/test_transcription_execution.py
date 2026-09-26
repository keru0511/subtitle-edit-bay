from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.transcript_cache import transcript_cache_metadata_path, write_transcript_cache_metadata
from src.transcription_execution import transcribe_audio_with_cache


class TranscriptionExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.audio = self.output / "voice.wav"
        self.audio.write_bytes(b"test audio")
        self.transcript = self.output / "voice.json"
        self.runner = patch(
            "src.transcription_execution.run_command_with_utf8_log", side_effect=self.write_result
        ).start()
        self.addCleanup(patch.stopall)

    def write_result(self, *args) -> None:
        self.transcript.write_text('{"segments": []}', encoding="utf-8")

    def transcribe(self, **kwargs):
        return transcribe_audio_with_cache(str(self.audio), str(self.output), **kwargs)

    def test_legacy_transcript_is_regenerated_once_then_reused(self) -> None:
        self.transcript.write_text('{"segments": [{"text": "いいいいいい"}]}')
        first = self.transcribe()
        second = self.transcribe()
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.runner.assert_called_once()
        self.assertIsNotNone(first.cache_metadata_path)

    def test_context_metadata_is_not_enough_without_execution_settings(self) -> None:
        self.write_result()
        write_transcript_cache_metadata(self.transcript, fingerprint="context-v1")
        self.assertFalse(self.transcribe(cache_fingerprint="context-v1").cache_hit)
        self.assertTrue(self.transcribe(cache_fingerprint="context-v1").cache_hit)

    def test_model_vad_and_hints_invalidate_cache(self) -> None:
        for changed in [
            {"model": "large-v2"},
            {"vad_onset": 0.6},
            {"vad_offset": 0.3},
            {"initial_prompt": "ゲーム実況"},
            {"hotwords": ["クラベス"]},
            {"cache_fingerprint": "new"},
        ]:
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
        with patch("src.transcription_execution.first_pass_profile", return_value={"version": "next"}):
            self.assertFalse(self.transcribe().cache_hit)
        with patch("src.transcription_execution.version", return_value="next-runtime"):
            self.assertFalse(self.transcribe().cache_hit)

    def test_metadata_keeps_execution_and_caller_settings(self) -> None:
        result = self.transcribe(cache_settings={"model": "large-v3"})
        metadata = json.loads(result.cache_metadata_path.read_text())
        self.assertEqual(metadata["settings"]["model"], "large-v3")
        self.assertEqual(metadata["settings"]["execution"]["profile"]["repetition_penalty"], 1.1)

    def test_failure_invalidates_previous_metadata(self) -> None:
        self.transcribe()
        self.runner.side_effect = RuntimeError("failed")
        with self.assertRaises(RuntimeError):
            self.transcribe(skip_existing=False)
        self.assertFalse(transcript_cache_metadata_path(self.transcript).exists())
        self.runner.side_effect = self.write_result
        self.assertFalse(self.transcribe().cache_hit)

    def test_missing_output_never_creates_valid_metadata(self) -> None:
        self.runner.side_effect = None
        with self.assertRaises(FileNotFoundError):
            self.transcribe()
        self.assertFalse(transcript_cache_metadata_path(self.transcript).exists())

    def test_hints_reach_first_pass_command(self) -> None:
        self.transcribe(initial_prompt="ゲーム実況", hotwords=["クラベス", "クラベス"], skip_existing=False)
        command = self.runner.call_args.args[0]
        self.assertIn("ゲーム実況", command)
        self.assertEqual(command[command.index("--hotwords") + 1], "クラベス")
