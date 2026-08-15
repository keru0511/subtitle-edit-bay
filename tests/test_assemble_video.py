from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.assemble_video import (
    build_concat_command,
    build_loudnorm_filter,
    build_normalize_command,
    format_filter_number,
    optional_clip,
    probe_has_audio,
    probe_video_frame_rate,
    write_concat_manifest,
)


class AssembleVideoTests(unittest.TestCase):
    def test_format_filter_number(self) -> None:
        self.assertEqual(format_filter_number(-16.0), "-16")
        self.assertEqual(format_filter_number(-1.5), "-1.5")

    def test_build_loudnorm_filter(self) -> None:
        self.assertEqual(build_loudnorm_filter(), "loudnorm=I=-16:LRA=11:TP=-1.5")

    def test_optional_clip_existing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            clip = Path(temp_dir) / "op.mp4"
            clip.write_text("x", encoding="utf-8")
            self.assertEqual(optional_clip(str(clip)), clip)

    def test_optional_clip_missing(self) -> None:
        self.assertIsNone(optional_clip(""))
        self.assertIsNone(optional_clip("/nonexistent/op.mp4"))

    def test_probe_video_frame_rate_from_avg_frame_rate(self) -> None:
        with mock.patch("src.assemble_video.subprocess.run") as run:
            run.return_value = mock.MagicMock(
                stdout=json.dumps({"streams": [{"avg_frame_rate": "60000/1001", "r_frame_rate": "0/0"}]})
            )
            self.assertEqual(probe_video_frame_rate("/tmp/video.mkv"), "60000/1001")

    def test_probe_video_frame_rate_falls_back_to_r_frame_rate(self) -> None:
        with mock.patch("src.assemble_video.subprocess.run") as run:
            run.return_value = mock.MagicMock(
                stdout=json.dumps({"streams": [{"avg_frame_rate": "0/0", "r_frame_rate": "30000/1001"}]})
            )
            self.assertEqual(probe_video_frame_rate("/tmp/video.mkv"), "30000/1001")

    def test_probe_video_frame_rate_raises_without_video_stream(self) -> None:
        with mock.patch("src.assemble_video.subprocess.run") as run:
            run.return_value = mock.MagicMock(stdout=json.dumps({"streams": []}))
            with self.assertRaises(ValueError):
                probe_video_frame_rate("/tmp/video.mkv")

    def test_probe_video_frame_rate_raises_for_invalid_fractions(self) -> None:
        with mock.patch("src.assemble_video.subprocess.run") as run:
            run.return_value = mock.MagicMock(
                stdout=json.dumps({"streams": [{"avg_frame_rate": "0/0", "r_frame_rate": "0/0"}]})
            )
            with self.assertRaises(ValueError):
                probe_video_frame_rate("/tmp/video.mkv")

    def test_probe_video_frame_rate_raises_on_subprocess_error(self) -> None:
        with mock.patch("src.assemble_video.subprocess.run") as run:
            run.side_effect = subprocess.CalledProcessError(1, ["ffprobe"])
            with self.assertRaises(subprocess.CalledProcessError):
                probe_video_frame_rate("/tmp/video.mkv")

    def test_probe_has_audio_true(self) -> None:
        with mock.patch("src.assemble_video.subprocess.run") as run:
            run.return_value = mock.MagicMock(stdout="1\n")
            self.assertTrue(probe_has_audio("/tmp/video.mkv"))

    def test_probe_has_audio_false(self) -> None:
        with mock.patch("src.assemble_video.subprocess.run") as run:
            run.return_value = mock.MagicMock(stdout="\n")
            self.assertFalse(probe_has_audio("/tmp/video.mkv"))

    def test_probe_has_audio_raises_on_subprocess_error(self) -> None:
        with mock.patch("src.assemble_video.subprocess.run") as run:
            run.side_effect = subprocess.CalledProcessError(1, ["ffprobe"])
            with self.assertRaises(subprocess.CalledProcessError):
                probe_has_audio("/tmp/video.mkv")

    def test_build_normalize_command(self) -> None:
        command = build_normalize_command("input.mp4", "out.mp4", 1920, 1080)
        self.assertIn("ffmpeg", command)
        self.assertIn("loudnorm=I=-16:LRA=11:TP=-1.5", command[command.index("-af") + 1])

    def test_build_normalize_command_adds_silent_audio(self) -> None:
        command = build_normalize_command("input.mp4", "out.mp4", 1920, 1080, has_audio=False)
        self.assertIn("lavfi", command)
        self.assertTrue(any("anullsrc" in part for part in command))

    def test_build_concat_command(self) -> None:
        command = build_concat_command("manifest.txt", "final.mp4")
        self.assertEqual(command[:2], ["ffmpeg", "-y"])
        self.assertIn("concat", command)

    def test_write_concat_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "a.mp4"
            second = Path(temp_dir) / "b.mp4"
            first.write_text("x", encoding="utf-8")
            second.write_text("x", encoding="utf-8")
            manifest = write_concat_manifest([str(first), str(second)], str(Path(temp_dir) / "concat.txt"))
            text = manifest.read_text(encoding="utf-8")
            self.assertIn(first.resolve().as_posix(), text)
            self.assertIn(second.resolve().as_posix(), text)


if __name__ == "__main__":
    unittest.main()
