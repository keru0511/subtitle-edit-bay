from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.burn_subs import (
    build_ffmpeg_command,
    build_ass_filter,
    run_ffmpeg_command,
    run_ffmpeg_with_nvenc_fallback,
    run_ffmpeg_burn,
    temporary_ass_path,
)


class BurnSubsTests(unittest.TestCase):
    def test_build_ass_filter_escapes_path(self) -> None:
        self.assertEqual(build_ass_filter("C:\\Users\\sub.ass"), r"ass='C\:/Users/sub.ass'")

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
        with mock.patch("src.burn_subs.subprocess.run") as run:
            run_ffmpeg_command(["ffmpeg", "-y", "-i", "in.mp4", "out.mp4"])
            run.assert_called_once_with(["ffmpeg", "-y", "-i", "in.mp4", "out.mp4"], check=True)

    def test_run_ffmpeg_command_with_callback(self) -> None:
        lines: list[str] = []
        fake_process = mock.MagicMock()
        fake_process.stdout = ["line1\n", "line2\n"]
        fake_process.wait.return_value = 0
        with mock.patch("src.burn_subs.subprocess.Popen", return_value=fake_process) as popen:
            run_ffmpeg_command(["ffmpeg", "-i", "in.mp4", "out.mp4"], progress_callback=lines.append)
            self.assertEqual(lines, ["line1", "line2"])
            popen.assert_called_once()

    def test_run_ffmpeg_command_with_callback_raises_on_nonzero(self) -> None:
        fake_process = mock.MagicMock()
        fake_process.stdout = ["error\n"]
        fake_process.wait.return_value = 1
        with mock.patch("src.burn_subs.subprocess.Popen", return_value=fake_process):
            with self.assertRaises(subprocess.CalledProcessError):
                run_ffmpeg_command(["ffmpeg", "-i", "in.mp4", "out.mp4"], progress_callback=print)

    def test_run_ffmpeg_with_nvenc_fallback_retries_on_failure(self) -> None:
        calls: list[str] = []

        def command_factory(codec: str) -> list[str]:
            calls.append(codec)
            return ["ffmpeg", "-c:v", codec]

        with mock.patch("src.burn_subs.run_ffmpeg_command") as run:
            run.side_effect = [subprocess.CalledProcessError(1, ["ffmpeg"]), None]
            run_ffmpeg_with_nvenc_fallback(command_factory, "h264_nvenc")
            self.assertEqual(calls, ["h264_nvenc", "libx264"])

    def test_run_ffmpeg_with_nvenc_fallback_reraises_for_non_nvenc(self) -> None:
        def command_factory(codec: str) -> list[str]:
            return ["ffmpeg", "-c:v", codec]

        with mock.patch("src.burn_subs.run_ffmpeg_command") as run:
            run.side_effect = subprocess.CalledProcessError(1, ["ffmpeg"])
            with self.assertRaises(subprocess.CalledProcessError):
                run_ffmpeg_with_nvenc_fallback(command_factory, "libx264")

    def test_run_ffmpeg_burn_creates_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            subtitle = root / "sub.ass"
            output = root / "out.mp4"
            video.write_text("x", encoding="utf-8")
            subtitle.write_text("[Script Info]\n", encoding="utf-8")

            def create_output(command: list[str], **kwargs: object) -> None:
                Path(command[-1]).write_text("mp4", encoding="utf-8")

            with mock.patch("src.burn_subs.run_ffmpeg_command", side_effect=create_output):
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

            with mock.patch("src.burn_subs.run_ffmpeg_command"):
                with self.assertRaises(RuntimeError):
                    run_ffmpeg_burn(str(video), str(subtitle), str(output))

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
