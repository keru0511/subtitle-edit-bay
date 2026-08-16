from __future__ import annotations

import json
import subprocess
import unittest
from unittest import mock

from src.media_probe import probe_media_duration, probe_media_stream_types


class MediaProbeTests(unittest.TestCase):
    def test_probe_media_duration_parses_ffprobe_output(self) -> None:
        with mock.patch("src.media_probe.subprocess.run") as run:
            run.return_value = mock.MagicMock(stdout="  123.4567  \n")
            duration = probe_media_duration("/tmp/video.mkv")
            self.assertAlmostEqual(duration, 123.4567)
            run.assert_called_once()
            command = run.call_args[0][0]
            self.assertEqual(command[0], "ffprobe")
            self.assertIn("format=duration", command)

    def test_probe_media_duration_returns_zero_for_negative(self) -> None:
        with mock.patch("src.media_probe.subprocess.run") as run:
            run.return_value = mock.MagicMock(stdout="-5.0\n")
            self.assertEqual(probe_media_duration("/tmp/video.mkv"), 0.0)

    def test_probe_media_duration_raises_on_ffprobe_failure(self) -> None:
        with mock.patch("src.media_probe.subprocess.run") as run:
            run.side_effect = subprocess.CalledProcessError(1, ["ffprobe"])
            with self.assertRaises(subprocess.CalledProcessError):
                probe_media_duration("/tmp/video.mkv")

    def test_probe_media_stream_types_returns_audio_and_video(self) -> None:
        with mock.patch("src.media_probe.subprocess.run") as run:
            run.return_value = mock.MagicMock(
                stdout=json.dumps({"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]})
            )
            types = probe_media_stream_types("/tmp/video.mkv")
            self.assertEqual(types, {"audio", "video"})
            command = run.call_args[0][0]
            self.assertNotIn("-select_streams", command)

    def test_probe_media_stream_types_returns_empty_for_no_streams(self) -> None:
        with mock.patch("src.media_probe.subprocess.run") as run:
            run.return_value = mock.MagicMock(stdout=json.dumps({"streams": []}))
            self.assertEqual(probe_media_stream_types("/tmp/video.mkv"), set())

    def test_probe_media_stream_types_ignores_unknown_types(self) -> None:
        with mock.patch("src.media_probe.subprocess.run") as run:
            run.return_value = mock.MagicMock(
                stdout=json.dumps({"streams": [{"codec_type": "subtitle"}, {"codec_type": "AUDIO"}]})
            )
            self.assertEqual(probe_media_stream_types("/tmp/video.mkv"), {"audio"})

    def test_probe_media_stream_types_raises_on_ffprobe_failure(self) -> None:
        with mock.patch("src.media_probe.subprocess.run") as run:
            run.side_effect = subprocess.CalledProcessError(1, ["ffprobe"])
            with self.assertRaises(subprocess.CalledProcessError):
                probe_media_stream_types("/tmp/video.mkv")


if __name__ == "__main__":
    unittest.main()
