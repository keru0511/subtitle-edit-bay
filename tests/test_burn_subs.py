from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from src.burn_subs import (
    build_ass_filter,
    build_ass_filter_path_with_cleanup,
    build_ffmpeg_command,
    run_ffmpeg_burn,
    temporary_ass_path,
)
from src.ffmpeg_execution import run_ffmpeg_command


def _fake_process(
    stdout_lines: list[str] | tuple[str, ...] = (),
    *,
    return_code: int = 0,
) -> mock.MagicMock:
    process = mock.MagicMock()
    process.stdout = list(stdout_lines)
    process.wait.return_value = return_code
    return process


class BurnSubsTests(unittest.TestCase):
    def test_build_ass_filter_escapes_path(self) -> None:
        self.assertEqual(build_ass_filter("C:\\Users\\sub.ass"), r"ass='C\:/Users/sub.ass'")

    def test_build_ass_filter_path_with_cleanup_copies_when_quoted_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_path = Path(temp_dir) / "quote's.ass"
            raw_path.write_text("ASS", encoding="utf-8")

            filter_text, cleanup = build_ass_filter_path_with_cleanup(str(raw_path))
            self.assertTrue(filter_text.startswith("ass='") and filter_text.endswith("'"))
            self.assertIsNotNone(cleanup)
            safe_filter = filter_text.split("'")[1]
            self.assertNotIn("'", safe_filter)
            self.assertNotIn("quote's.ass", safe_filter)
            self.assertNotEqual(Path(safe_filter).name, raw_path.name)
            self.assertNotEqual(cleanup, str(raw_path))
            Path(cleanup).unlink(missing_ok=True)

    def test_build_ffmpeg_command_basic(self) -> None:
        command = build_ffmpeg_command("video.mp4", "sub.ass", "out.mp4", video_codec="libx264")
        self.assertIn("ffmpeg", command)
        self.assertIn("-vf", command)
        self.assertIn("ass='sub.ass'", command[command.index("-vf") + 1])

    def test_build_ffmpeg_command_with_audio_filter(self) -> None:
        command = build_ffmpeg_command(
            "video.mp4",
            "sub.ass",
            "out.mp4",
            audio_filter="loudnorm=I=-16",
            audio_codec="aac",
        )
        self.assertIn("-af", command)
        self.assertIn("aac", command)

    def test_build_ffmpeg_command_with_audio_mix(self) -> None:
        audio_mix = {
            "channels": [
                {"kind": "video", "selector": "0:a:0", "enabled": True, "volume_percent": 50},
                {"kind": "external", "path": "voice.flac", "enabled": True, "volume_percent": 100},
            ]
        }
        command = build_ffmpeg_command(
            "video.mp4",
            "sub.ass",
            "out.mp4",
            audio_mix=audio_mix,
            audio_codec="copy",
        )
        self.assertIn("-filter_complex", command)
        self.assertIn("voice.flac", command)
        self.assertIn("-shortest", command)
        self.assertIn("aac", command)
        self.assertNotIn("copy", command)

    def test_run_ffmpeg_command_without_callback(self) -> None:
        command = ["ffmpeg", "-y", "-i", "in.mp4", "out.mp4"]
        with (
            mock.patch(
                "src.ffmpeg_execution.subprocess.Popen",
                return_value=_fake_process(["line1\n", "line2\n"]),
            ) as popen,
            mock.patch("src.ffmpeg_execution._emit_ffmpeg_output") as emit,
        ):
            run_ffmpeg_command(command)
            popen.assert_called_once_with(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            self.assertEqual(emit.call_args_list, [mock.call("line1"), mock.call("line2")])

    def test_run_ffmpeg_command_without_callback_captures_stderr(self) -> None:
        command = [
            sys.executable,
            "-c",
            "import sys; print('could not find encoder h264_nvenc', file=sys.stderr); sys.exit(1)",
        ]
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                run_ffmpeg_command(command)

        self.assertIn("could not find encoder h264_nvenc", raised.exception.output)

    def test_run_ffmpeg_command_without_callback_bounds_failure_tail(self) -> None:
        command = ["ffmpeg", "-i", "in.mp4", "out.mp4"]
        lines = [f"line-{index}\n" for index in range(120)]
        with (
            mock.patch(
                "src.ffmpeg_execution.subprocess.Popen",
                return_value=_fake_process(lines, return_code=1),
            ),
            mock.patch("src.ffmpeg_execution._emit_ffmpeg_output") as emit,
        ):
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                run_ffmpeg_command(command)

        self.assertEqual(
            raised.exception.output.splitlines(),
            [f"line-{index}" for index in range(40, 120)],
        )
        self.assertEqual(emit.call_count, 120)

    def test_run_ffmpeg_command_with_callback(self) -> None:
        lines: list[str] = []
        fake_process = mock.MagicMock()
        fake_process.stdout = ["line1\n", "line2\n"]
        fake_process.wait.return_value = 0
        with mock.patch("src.ffmpeg_execution.subprocess.Popen", return_value=fake_process) as popen:
            run_ffmpeg_command(["ffmpeg", "-i", "in.mp4", "out.mp4"], progress_callback=lines.append)
            self.assertEqual(lines, ["line1", "line2"])
            popen.assert_called_once()

    def test_run_ffmpeg_command_with_callback_raises_on_nonzero(self) -> None:
        fake_process = mock.MagicMock()
        fake_process.stdout = ["error\n"]
        fake_process.wait.return_value = 1
        with mock.patch("src.ffmpeg_execution.subprocess.Popen", return_value=fake_process):
            with self.assertRaises(subprocess.CalledProcessError):
                run_ffmpeg_command(["ffmpeg", "-i", "in.mp4", "out.mp4"], progress_callback=print)

    def test_run_ffmpeg_burn_creates_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            subtitle = root / "sub.ass"
            output = root / "out.mp4"
            video.write_text("x", encoding="utf-8")
            subtitle.write_text("[Script Info]\n", encoding="utf-8")

            def create_output(command: list[str], **_kwargs: object) -> mock.MagicMock:
                Path(command[-1]).write_text("mp4", encoding="utf-8")
                return _fake_process()

            with mock.patch("src.ffmpeg_execution.subprocess.Popen", side_effect=create_output):
                returned = run_ffmpeg_burn(str(video), str(subtitle), str(output))
            self.assertEqual(returned, output)
            self.assertTrue(output.exists())

    def test_run_ffmpeg_burn_raises_when_output_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            subtitle = root / "sub.ass"
            output = root / "out.mp4"
            video.write_text("x", encoding="utf-8")
            subtitle.write_text("[Script Info]\n", encoding="utf-8")

            with mock.patch(
                "src.ffmpeg_execution.subprocess.Popen",
                return_value=_fake_process(),
            ):
                with self.assertRaises(RuntimeError):
                    run_ffmpeg_burn(str(video), str(subtitle), str(output))

    def test_run_ffmpeg_burn_uses_temporary_copy_for_apostrophe_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            subtitle = root / "O'Brien" / "caption.ass"
            subtitle.parent.mkdir(parents=True, exist_ok=True)
            subtitle.write_text("dummy", encoding="utf-8")
            output = root / "output.mp4"
            video.write_bytes(b"")

            calls: list[list[str]] = []

            def fake_popen(command: list[str], **_kwargs: object) -> mock.MagicMock:
                calls.append(command)
                Path(command[-1]).write_bytes(b"ok")
                return _fake_process()

            with mock.patch("src.ffmpeg_execution.subprocess.Popen", side_effect=fake_popen):
                result = run_ffmpeg_burn(str(video), str(subtitle), str(output))

            self.assertEqual(result, output)
            self.assertTrue(output.exists())
            command_str = " ".join(calls[0])
            self.assertNotIn("O'Brien", command_str)
            self.assertIn("ass='", command_str)
            self.assertNotIn("O\\'Brien", command_str)

    def test_run_ffmpeg_burn_falls_back_from_nvenc_to_x264(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            subtitle = root / "caption.ass"
            output = root / "output.mp4"
            video.write_bytes(b"video")
            subtitle.write_text("dummy", encoding="utf-8")
            calls: list[list[str]] = []

            def fake_popen(command: list[str], **_kwargs: object) -> mock.MagicMock:
                calls.append(command)
                codec = command[command.index("-c:v") + 1]
                if codec == "h264_nvenc":
                    return _fake_process(
                        ["could not find encoder h264_nvenc\n"],
                        return_code=1,
                    )
                Path(command[-1]).write_bytes(b"x264 output")
                return _fake_process()

            with mock.patch("src.ffmpeg_execution.subprocess.Popen", side_effect=fake_popen):
                run_ffmpeg_burn(
                    str(video),
                    str(subtitle),
                    str(output),
                    video_codec="h264_nvenc",
                    audio_codec="aac",
                )

            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0][calls[0].index("-c:v") + 1], "h264_nvenc")
            self.assertEqual(calls[1][calls[1].index("-c:v") + 1], "libx264")
            self.assertEqual(output.read_bytes(), b"x264 output")

    def test_run_ffmpeg_burn_rethrows_non_nvenc_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            subtitle = root / "caption.ass"
            output = root / "output.mp4"
            video.write_bytes(b"video")
            subtitle.write_text("dummy", encoding="utf-8")

            def fake_popen(command: list[str], **_kwargs: object) -> mock.MagicMock:
                return _fake_process(
                    ["unexpected media error\n"],
                    return_code=1,
                )

            with mock.patch("src.ffmpeg_execution.subprocess.Popen", side_effect=fake_popen):
                with self.assertRaises(subprocess.CalledProcessError):
                    run_ffmpeg_burn(
                        str(video),
                        str(subtitle),
                        str(output),
                        video_codec="libx264",
                        audio_codec="aac",
                    )

    def test_temporary_ass_path_copies_when_apostrophe_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            subtitle = Path(temp_dir) / "O'Brien" / "caption.ass"
            subtitle.parent.mkdir()
            subtitle.write_text("[Script Info]\n", encoding="utf-8")
            with temporary_ass_path(str(subtitle)) as safe_path:
                self.assertNotEqual(safe_path, str(subtitle))
                self.assertTrue(Path(safe_path).exists())


if __name__ == "__main__":
    unittest.main()
