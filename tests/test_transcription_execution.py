from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.transcript_cache import write_transcript_cache_metadata
from src.transcription_execution import transcribe_audio_with_cache


class TranscriptionExecutionTests(unittest.TestCase):
    def test_reuses_legacy_transcript_when_no_fingerprint_is_expected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            transcript = output / "voice.json"
            transcript.write_text('{"segments": []}', encoding="utf-8")

            with patch("src.transcription_execution.run_command_with_utf8_log") as run:
                result = transcribe_audio_with_cache("voice.wav", str(output))

            self.assertTrue(result.cache_hit)
            self.assertEqual(result.transcript_path, transcript)
            run.assert_not_called()

    def test_missing_metadata_is_cache_miss_when_fingerprint_is_expected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            transcript = output / "voice.json"
            transcript.write_text('{"segments": []}', encoding="utf-8")

            with patch("src.transcription_execution.run_command_with_utf8_log") as run:
                result = transcribe_audio_with_cache(
                    "voice.wav",
                    str(output),
                    cache_fingerprint="fingerprint-v1",
                    cache_settings={"model": "large-v3"},
                )

            self.assertFalse(result.cache_hit)
            run.assert_called_once()
            self.assertIsNotNone(result.cache_metadata_path)
            metadata = json.loads(result.cache_metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["fingerprint"], "fingerprint-v1")
            self.assertEqual(metadata["settings"]["model"], "large-v3")

    def test_reuses_transcript_when_metadata_matches_expected_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            transcript = output / "voice.json"
            transcript.write_text('{"segments": []}', encoding="utf-8")
            write_transcript_cache_metadata(transcript, fingerprint="fingerprint-v1")

            with patch("src.transcription_execution.run_command_with_utf8_log") as run:
                result = transcribe_audio_with_cache(
                    "voice.wav",
                    str(output),
                    cache_fingerprint="fingerprint-v1",
                )

            self.assertTrue(result.cache_hit)
            run.assert_not_called()

    def test_mismatched_metadata_forces_retranscription(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            transcript = output / "voice.json"
            transcript.write_text('{"segments": []}', encoding="utf-8")
            write_transcript_cache_metadata(transcript, fingerprint="old")

            with patch("src.transcription_execution.run_command_with_utf8_log") as run:
                result = transcribe_audio_with_cache(
                    "voice.wav",
                    str(output),
                    cache_fingerprint="new",
                )

            self.assertFalse(result.cache_hit)
            run.assert_called_once()
            self.assertEqual(
                json.loads((output / "voice.json.cache.json").read_text(encoding="utf-8"))["fingerprint"],
                "new",
            )

    def test_command_receives_transcription_hints_on_cache_miss(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("src.transcription_execution.run_command_with_utf8_log") as run:
                result = transcribe_audio_with_cache(
                    "voice.wav",
                    temp_dir,
                    initial_prompt="Game context",
                    hotwords=["ナワバリバトル", "ナワバリバトル", "スプラシューター"],
                    skip_existing=False,
                )

            self.assertFalse(result.cache_hit)
            command = run.call_args.args[0]
            self.assertIn("--initial_prompt", command)
            self.assertIn("Game context", command)
            self.assertIn("--hotwords", command)
            self.assertIn("ナワバリバトル, スプラシューター", command)


if __name__ == "__main__":
    unittest.main()
