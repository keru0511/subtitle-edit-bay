from __future__ import annotations

import io
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from src.craig_pipeline import decode_audio_samples
from src.media_probe import probe_media_duration, probe_media_stream_types
from src.process_utils import SubprocessOptions, hidden_subprocess_kwargs
from src.subtitle_workflow_transcription import _extract_video_audio_track
from src.transcribe import probe_audio_streams, run_command_with_utf8_log
from tests.typed_case import TypedTestCase


class HiddenSubprocessOptionsTests(TypedTestCase):
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
        calls: list[dict[str, object]] = []
        outputs = iter(("1.0", '{"streams": []}'))
        options: SubprocessOptions = {"creationflags": 1}

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(kwargs)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=next(outputs))

        with (
            mock.patch("src.media_probe.subprocess.run", side_effect=fake_run),
            mock.patch("src.media_probe.hidden_subprocess_kwargs", return_value=options),
        ):
            probe_media_duration("video.mkv")
            self.assertEqual(calls[-1]["creationflags"], 1)

            probe_media_stream_types("video.mkv")
            self.assertEqual(calls[-1]["creationflags"], 1)

    def test_transcription_commands_receive_hidden_console_option(self) -> None:
        calls: list[dict[str, object]] = []
        options: SubprocessOptions = {"creationflags": 2}

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(kwargs)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout='{"streams": []}')

        with (
            mock.patch("src.transcribe.subprocess.run", side_effect=fake_run),
            mock.patch("src.transcribe.hidden_subprocess_kwargs", return_value=options),
        ):
            probe_audio_streams("video.mkv")
            self.assertEqual(calls[-1]["creationflags"], 2)

        with TemporaryDirectory() as temp_dir:

            class FakeProcess:
                def __init__(self) -> None:
                    self.stdout = io.StringIO()

                def wait(self) -> int:
                    return 0

            popen_calls: list[dict[str, object]] = []

            def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
                popen_calls.append(kwargs)
                return FakeProcess()

            with (
                mock.patch("src.transcribe.subprocess.Popen", side_effect=fake_popen),
                mock.patch("src.transcribe.hidden_subprocess_kwargs", return_value=options),
            ):
                run_command_with_utf8_log(["whisperx"], str(Path(temp_dir) / "run.log"))
            self.assertEqual(popen_calls[-1]["creationflags"], 2)

    def test_audio_analysis_commands_receive_hidden_console_option(self) -> None:
        calls: list[dict[str, object]] = []
        options: SubprocessOptions = {"creationflags": 3}

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            calls.append(kwargs)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=b"\x00\x00\x00\x00")

        with (
            mock.patch("src.craig_pipeline.subprocess.run", side_effect=fake_run),
            mock.patch("src.craig_pipeline.hidden_subprocess_kwargs", return_value=options),
        ):
            decode_audio_samples("audio.aac")
            self.assertEqual(calls[-1]["creationflags"], 3)

        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "transcripts"
            extraction_options: SubprocessOptions = {"creationflags": 4}

            output_calls: list[dict[str, object]] = []

            def create_output(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                output_calls.append(kwargs)
                Path(command[-1]).touch()
                return subprocess.CompletedProcess(args=command, returncode=0)

            with (
                mock.patch("src.subtitle_workflow_transcription.subprocess.run", side_effect=create_output),
                mock.patch(
                    "src.subtitle_workflow_transcription.hidden_subprocess_kwargs",
                    return_value=extraction_options,
                ),
            ):
                _extract_video_audio_track("video.mkv", "0:a:0", output_dir)
            self.assertEqual(output_calls[-1]["creationflags"], 4)


class ProcessBoundaryTests(TypedTestCase):
    def test_windows_stop_targets_tree_and_force_is_explicit(self) -> None:
        from src.process_utils import stop_process

        class FakeProcess:
            terminate_calls = 0
            kill_calls = 0

            def terminate(self) -> None:
                self.terminate_calls += 1

            def kill(self) -> None:
                self.kill_calls += 1

        process = FakeProcess()
        commands: list[object] = []

        def fake_run(command: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(args=[], returncode=0)

        with (
            mock.patch("src.process_utils.os.name", "nt"),
            mock.patch("src.process_utils.subprocess.run", side_effect=fake_run),
        ):
            stop_process(process, 123)
            self.assertEqual(commands[-1], ["taskkill", "/PID", "123", "/T"])
            stop_process(process, 123, force=True)
            self.assertEqual(commands[-1], ["taskkill", "/PID", "123", "/T", "/F"])
        self.assertEqual(process.terminate_calls, 0)
        self.assertEqual(process.kill_calls, 0)

    def test_posix_stop_preserves_terminate_then_kill_contract(self) -> None:
        from src.process_utils import stop_process

        class FakeProcess:
            terminate_calls = 0
            kill_calls = 0

            def terminate(self) -> None:
                self.terminate_calls += 1

            def kill(self) -> None:
                self.kill_calls += 1

        process = FakeProcess()
        commands: list[object] = []

        def fake_run(command: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(args=[], returncode=0)

        with (
            mock.patch("src.process_utils.os.name", "posix"),
            mock.patch("src.process_utils.subprocess.run", side_effect=fake_run),
        ):
            stop_process(process, 123)
            self.assertEqual(process.terminate_calls, 1)
            self.assertEqual(process.kill_calls, 0)
            stop_process(process, 123, force=True)
            self.assertEqual(process.kill_calls, 1)
            self.assertEqual(commands, [])

    def test_detached_options_include_platform_console_policy(self) -> None:
        from src.process_utils import detached_subprocess_kwargs

        options: SubprocessOptions = {"creationflags": 8}
        with mock.patch("src.process_utils.hidden_subprocess_kwargs", return_value=options):
            self.assertEqual(detached_subprocess_kwargs(), {"start_new_session": True, "creationflags": 8})


if __name__ == "__main__":
    unittest.main()
