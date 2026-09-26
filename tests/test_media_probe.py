from __future__ import annotations

import json
import subprocess
import unittest
from dataclasses import dataclass
from typing import cast
from unittest import mock

from src.data_boundary import is_object_sequence
from src.media_probe import probe_media_duration, probe_media_stream_types, probe_video_stream
from tests.typed_case import TypedTestCase


@dataclass
class _FakeProbeResult:
    stdout: str


def _streams_result(streams: list[dict[str, object]]) -> _FakeProbeResult:
    payload: dict[str, object] = {"streams": streams}
    return _FakeProbeResult(json.dumps(payload))


def _called_command(run: mock.MagicMock) -> list[str]:
    raw_call = cast(object, run.call_args)
    if not is_object_sequence(raw_call) or not raw_call:
        raise AssertionError("ffprobe must have been called")
    arguments = raw_call[0]
    if not is_object_sequence(arguments) or not arguments:
        raise AssertionError("ffprobe command is missing")
    raw_command = arguments[0]
    if not is_object_sequence(raw_command) or not all(isinstance(item, str) for item in raw_command):
        raise AssertionError("ffprobe command must be a sequence of strings")
    return [item for item in raw_command if isinstance(item, str)]


class MediaProbeTests(TypedTestCase):
    def test_probe_media_duration_parses_ffprobe_output(self) -> None:
        with mock.patch("src.media_probe.subprocess.run", return_value=_FakeProbeResult("  123.4567  \n")) as run:
            duration = probe_media_duration("/tmp/video.mkv")
            self.assertAlmostEqual(duration, 123.4567)
            run.assert_called_once()
            command = _called_command(run)
            self.assertEqual(command[0], "ffprobe")
            self.assertIn("format=duration", command)

    def test_probe_media_duration_returns_zero_for_negative(self) -> None:
        with mock.patch("src.media_probe.subprocess.run", return_value=_FakeProbeResult("-5.0\n")):
            self.assertEqual(probe_media_duration("/tmp/video.mkv"), 0.0)

    def test_probe_media_duration_raises_on_ffprobe_failure(self) -> None:
        with mock.patch("src.media_probe.subprocess.run") as run:
            run.side_effect = subprocess.CalledProcessError(1, ["ffprobe"])
            with self.assertRaises(subprocess.CalledProcessError):
                probe_media_duration("/tmp/video.mkv")

    def test_probe_media_stream_types_returns_audio_and_video(self) -> None:
        with mock.patch(
            "src.media_probe.subprocess.run",
            return_value=_streams_result([{"codec_type": "video"}, {"codec_type": "audio"}]),
        ) as run:
            types = probe_media_stream_types("/tmp/video.mkv")
            self.assertEqual(types, {"audio", "video"})
            command = _called_command(run)
            self.assertNotIn("-select_streams", command)

    def test_probe_media_stream_types_returns_empty_for_no_streams(self) -> None:
        with mock.patch("src.media_probe.subprocess.run", return_value=_streams_result([])):
            self.assertEqual(probe_media_stream_types("/tmp/video.mkv"), set())

    def test_probe_media_stream_types_ignores_unknown_types(self) -> None:
        with mock.patch(
            "src.media_probe.subprocess.run",
            return_value=_streams_result([{"codec_type": "subtitle"}, {"codec_type": "AUDIO"}]),
        ):
            self.assertEqual(probe_media_stream_types("/tmp/video.mkv"), {"audio"})

    def test_probe_media_stream_types_rejects_malformed_streams(self) -> None:
        with mock.patch("src.media_probe.subprocess.run", return_value=_FakeProbeResult('{"streams": {}}')):
            with self.assertRaises(ValueError):
                probe_media_stream_types("/tmp/video.mkv")

    def test_probe_media_stream_types_raises_on_ffprobe_failure(self) -> None:
        with mock.patch("src.media_probe.subprocess.run") as run:
            run.side_effect = subprocess.CalledProcessError(1, ["ffprobe"])
            with self.assertRaises(subprocess.CalledProcessError):
                probe_media_stream_types("/tmp/video.mkv")

    def test_probe_video_stream_returns_dimensions_and_sample_aspect_ratio(self) -> None:
        stream: dict[str, object] = {"width": 1920, "height": 1080, "sample_aspect_ratio": "1:1"}
        with mock.patch("src.media_probe.subprocess.run", return_value=_streams_result([stream])) as run:
            result = probe_video_stream("/tmp/video.mkv")
            self.assertEqual(result, stream)
            command = _called_command(run)
            self.assertIn("v:0", command)
            self.assertIn("stream=width,height,sample_aspect_ratio", command)

    def test_probe_video_stream_raises_without_a_video_stream(self) -> None:
        with mock.patch("src.media_probe.subprocess.run", return_value=_streams_result([])):
            with self.assertRaisesRegex(ValueError, "No video stream found"):
                probe_video_stream("/tmp/video.mkv")


if __name__ == "__main__":
    unittest.main()
