from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.transcribe import (
    _normalized_hotwords,
    build_extract_audio_command,
    build_whisperx_command,
    print_streams,
    probe_audio_streams,
    run_command_with_utf8_log,
    validate_hf_token,
)


class TranscribeTests(unittest.TestCase):
    def test_probe_audio_streams_parses_json(self) -> None:
        streams = [
            {
                "index": 0,
                "codec_name": "aac",
                "channels": 2,
                "tags": {"language": "jpn", "title": "Main"},
            }
        ]
        with mock.patch("src.transcribe.subprocess.run") as run:
            run.return_value = mock.MagicMock(stdout=json.dumps({"streams": streams}))
            result = probe_audio_streams("/tmp/video.mkv")
            self.assertEqual(result, streams)

    def test_probe_audio_streams_raises_on_subprocess_failure(self) -> None:
        with mock.patch("src.transcribe.subprocess.run") as run:
            run.side_effect = subprocess.CalledProcessError(1, ["ffprobe"])
            with self.assertRaises(subprocess.CalledProcessError):
                probe_audio_streams("/tmp/video.mkv")

    def test_build_extract_audio_command(self) -> None:
        command = build_extract_audio_command("in.mkv", "out.wav", "0:a:1")
        self.assertIn("ffmpeg", command)
        self.assertIn("0:a:1", command)
        self.assertIn("pcm_s16le", command)

    def test_normalized_hotwords_accepts_list_string_and_none(self) -> None:
        self.assertEqual(_normalized_hotwords(None), [])
        self.assertEqual(_normalized_hotwords("word"), ["word"])
        self.assertEqual(_normalized_hotwords(["  A ", "b", "A", ""]), ["A", "b"])

    def test_normalized_hotwords_removes_control_characters(self) -> None:
        self.assertEqual(_normalized_hotwords(["a\x01b"]), ["ab"])

    def test_validate_hf_token_does_nothing_without_diarize(self) -> None:
        with mock.patch.dict(os.environ, {"HF_TOKEN": ""}, clear=False):
            validate_hf_token(False)

    def test_validate_hf_token_raises_when_missing(self) -> None:
        with mock.patch.dict(os.environ, {"HF_TOKEN": ""}, clear=True):
            with self.assertRaises(SystemExit):
                validate_hf_token(True)

    def test_build_whisperx_command_includes_diarization_args(self) -> None:
        with mock.patch.dict(os.environ, {"HF_TOKEN": "token"}, clear=False):
            command = build_whisperx_command(
                "audio.wav",
                "/out",
                diarize=True,
                min_speakers=2,
                max_speakers=5,
            )
        self.assertIn("--diarize", command)
        self.assertIn("--min_speakers", command)
        self.assertIn("2", command)
        self.assertIn("--max_speakers", command)
        self.assertIn("5", command)

    def test_build_whisperx_command_omits_speaker_count_when_none(self) -> None:
        with mock.patch.dict(os.environ, {"HF_TOKEN": "token"}, clear=False):
            command = build_whisperx_command("audio.wav", "/out", diarize=True)
        self.assertIn("--diarize", command)
        self.assertNotIn("--min_speakers", command)
        self.assertNotIn("--max_speakers", command)

    def test_build_whisperx_command_includes_prompt_and_hotwords(self) -> None:
        command = build_whisperx_command(
            "audio.wav",
            "/out",
            initial_prompt="  prompt  ",
            hotwords=["word1", "word2"],
        )
        self.assertIn("--initial_prompt", command)
        self.assertIn("--hotwords", command)
        self.assertIn("word1, word2", command)

    def test_build_whisperx_command_omits_language_when_none(self) -> None:
        command = build_whisperx_command("audio.wav", "/out", language=None)
        self.assertNotIn("--language", command)

    def test_print_streams_empty(self) -> None:
        captured = io.StringIO()
        with mock.patch("sys.stdout", captured):
            print_streams([])
        self.assertIn("No audio streams found", captured.getvalue())

    def test_print_streams_outputs_stream_details(self) -> None:
        captured = io.StringIO()
        streams = [
            {"index": 1, "codec_name": "aac", "channels": 2, "tags": {"language": "jpn"}},
            {"index": 2, "codec_name": "flac", "channels": 1, "tags": {"title": "Voice"}},
        ]
        with mock.patch("sys.stdout", captured):
            print_streams(streams)
        output = captured.getvalue()
        self.assertIn("0:a:0", output)
        self.assertIn("0:a:1", output)
        self.assertIn("language=jpn", output)
        self.assertIn("title=Voice", output)

    def test_run_command_with_utf8_log_writes_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "log.txt"
            fake_process = mock.MagicMock()
            fake_process.stdout = io.StringIO("hello\n")
            fake_process.wait.return_value = 0
            with mock.patch("src.transcribe.subprocess.Popen", return_value=fake_process):
                run_command_with_utf8_log(["echo", "hello"], str(log_path))
            self.assertIn("hello", log_path.read_text(encoding="utf-8"))

    def test_run_command_with_utf8_log_raises_and_writes_on_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "log.txt"
            fake_process = mock.MagicMock()
            fake_process.stdout = io.StringIO("error\n")
            fake_process.wait.return_value = 1
            with mock.patch("src.transcribe.subprocess.Popen", return_value=fake_process):
                with self.assertRaises(subprocess.CalledProcessError):
                    run_command_with_utf8_log(["false"], str(log_path))
            self.assertIn("error", log_path.read_text(encoding="utf-8"))

    def test_run_command_with_utf8_log_handles_popen_oserror(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "log.txt"
            with mock.patch("src.transcribe.subprocess.Popen") as popen:
                popen.side_effect = OSError("no such file")
                with self.assertRaises(OSError):
                    run_command_with_utf8_log(["missing"], str(log_path))
            self.assertTrue(log_path.exists())


if __name__ == "__main__":
    unittest.main()
