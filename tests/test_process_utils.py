from __future__ import annotations

import io
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from src.craig_pipeline import decode_audio_samples
from src.media_probe import probe_media_duration, probe_media_stream_types
from src.process_utils import hidden_subprocess_kwargs
from src.subtitle_workflow_transcription import _extract_video_audio_track
from src.transcribe import probe_audio_streams, run_command_with_utf8_log


class HiddenSubprocessOptionsTests(unittest.TestCase):
    def test_non_windows_returns_no_platform_specific_options(self) -> None:
        with mock.patch("src.process_utils.os.name", "posix"):
            self.assertEqual(hidden_subprocess_kwargs(), {})

    def test_windows_returns_create_no_window(self) -> None:
        with (
            mock.patch("src.process_utils.os.name", "nt"),
            mock.patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
        ):
            self.assertEqual(hidden_subprocess_kwargs(), {"creationflags": 0x08000000})

    def test_media_probe_commands_receive_hidden_console_option(self) -> None:
        with (
            mock.patch("src.media_probe.subprocess.run") as run,
            mock.patch("src.media_probe.hidden_subprocess_kwargs", return_value={"creationflags": 1}),
        ):
            run.return_value = mock.MagicMock(stdout="1.0")
            probe_media_duration("video.mkv")
            self.assertEqual(run.call_args.kwargs["creationflags"], 1)

            run.reset_mock()
            run.return_value = mock.MagicMock(stdout='{"streams": []}')
            probe_media_stream_types("video.mkv")
            self.assertEqual(run.call_args.kwargs["creationflags"], 1)

    def test_transcription_commands_receive_hidden_console_option(self) -> None:
        with (
            mock.patch("src.transcribe.subprocess.run") as run,
            mock.patch("src.transcribe.hidden_subprocess_kwargs", return_value={"creationflags": 2}),
        ):
            run.return_value = mock.MagicMock(stdout='{"streams": []}')
            probe_audio_streams("video.mkv")
            self.assertEqual(run.call_args.kwargs["creationflags"], 2)

        with TemporaryDirectory() as temp_dir:
            process = mock.MagicMock()
            process.stdout = io.StringIO()
            process.wait.return_value = 0
            with (
                mock.patch("src.transcribe.subprocess.Popen", return_value=process) as popen,
                mock.patch("src.transcribe.hidden_subprocess_kwargs", return_value={"creationflags": 2}),
            ):
                run_command_with_utf8_log(["whisperx"], str(Path(temp_dir) / "run.log"))
            self.assertEqual(popen.call_args.kwargs["creationflags"], 2)

    def test_audio_analysis_commands_receive_hidden_console_option(self) -> None:
        with (
            mock.patch("src.craig_pipeline.subprocess.run") as run,
            mock.patch("src.craig_pipeline.hidden_subprocess_kwargs", return_value={"creationflags": 3}),
        ):
            run.return_value = mock.MagicMock(stdout=b"\x00\x00\x00\x00")
            decode_audio_samples("audio.aac")
            self.assertEqual(run.call_args.kwargs["creationflags"], 3)

        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "transcripts"

            def create_output(command: list[str], **_kwargs: object) -> mock.MagicMock:
                Path(command[-1]).touch()
                return mock.MagicMock(returncode=0)

            with (
                mock.patch("src.subtitle_workflow_transcription.subprocess.run", side_effect=create_output) as run,
                mock.patch(
                    "src.subtitle_workflow_transcription.hidden_subprocess_kwargs",
                    return_value={"creationflags": 4},
                ),
            ):
                _extract_video_audio_track("video.mkv", "0:a:0", output_dir)
            self.assertEqual(run.call_args.kwargs["creationflags"], 4)


if __name__ == "__main__":
    unittest.main()
