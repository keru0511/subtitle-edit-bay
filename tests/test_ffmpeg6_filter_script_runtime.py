from __future__ import annotations

from array import array
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.ffmpeg_filter_script import (
    LEGACY_FILTER_SCRIPT_OPTION,
    detect_filter_complex_script_option,
)
from src.short_video import render_short_video
from src.silence_cut import cut_media_ranges


@unittest.skipUnless(
    os.environ.get("RUN_FFMPEG6_COMPAT") == "1",
    "set RUN_FFMPEG6_COMPAT=1 to exercise the pinned FFmpeg 6 runtime",
)
class FFmpeg6FilterScriptRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise unittest.SkipTest("ffmpeg and ffprobe are required")
        version = cls._run(["ffmpeg", "-version"]).stdout.splitlines()[0]
        if not version.startswith("ffmpeg version 6."):
            raise AssertionError(f"Expected FFmpeg 6, got: {version}")
        detect_filter_complex_script_option.cache_clear()

    @staticmethod
    def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _make_media(self, path: Path, duration: float) -> Path:
        self._run([
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=320x180:rate=15:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ])
        return path

    def _make_pattern_media(self, path: Path, duration: float) -> Path:
        self._run([
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=320x180:rate=15:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ])
        return path

    def _make_bgm(self, path: Path, duration: float) -> Path:
        self._run([
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=660:sample_rate=48000:duration={duration}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ])
        return path

    def _make_video_only(self, path: Path, duration: float) -> Path:
        self._run([
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=320x180:rate=15:duration={duration}",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ])
        return path

    @staticmethod
    def _read_rgb_frame(output: Path, *, seek: float = 0.4) -> bytes:
        process = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                str(seek),
                "-i",
                str(output),
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ],
            check=True,
            capture_output=True,
        )
        return process.stdout

    @staticmethod
    def _pixel(frame: bytes, *, width: int, x: int, y: int) -> tuple[int, int, int]:
        offset = (y * width + x) * 3
        return tuple(frame[offset : offset + 3])  # type: ignore[return-value]

    @staticmethod
    def _mean_abs_difference(left: bytes, right: bytes) -> float:
        differences = [abs(first - second) for first, second in zip(left, right)]
        return sum(differences) / len(differences)

    @staticmethod
    def _audio_peak(output: Path, *, start: float, duration: float) -> int:
        process = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                str(start),
                "-i",
                str(output),
                "-t",
                str(duration),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "8000",
                "-f",
                "s16le",
                "-",
            ],
            check=True,
            capture_output=True,
        )
        samples = array("h")
        samples.frombytes(process.stdout)
        return max((abs(sample) for sample in samples), default=0)

    def _assert_video_audio_output(
        self,
        output: Path,
        *,
        width: int,
        height: int,
    ) -> None:
        self.assertTrue(output.is_file())
        self.assertGreater(output.stat().st_size, 0)
        probe = self._run([
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,width,height,pix_fmt",
            "-of",
            "json",
            str(output),
        ])
        streams = json.loads(probe.stdout)["streams"]
        video = next(stream for stream in streams if stream["codec_type"] == "video")
        self.assertEqual(video["width"], width)
        self.assertEqual(video["height"], height)
        self.assertEqual(video["pix_fmt"], "yuv420p")
        self.assertTrue(any(stream["codec_type"] == "audio" for stream in streams))

    def test_short_video_filter_script_renders_with_ffmpeg_6(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self._make_media(root / "input.mp4", 1.0)
            output = root / "short.mp4"
            option = detect_filter_complex_script_option()
            project = {
                "video": {"path": str(source)},
                "short_video": {
                    "enabled": True,
                    "output": {"width": 180, "height": 320, "fps": 15},
                    "transition": {"type": "cut", "duration": 0.0},
                    "clips": [
                        {
                            "segment_id": "ffmpeg6-short",
                            "start": 0.0,
                            "end": 0.8,
                            "fit": "cover",
                        }
                    ],
                },
            }

            self.assertEqual(option, LEGACY_FILTER_SCRIPT_OPTION)
            result = render_short_video(
                root / "ffmpeg6.subtitle-project.json",
                output,
                _project=project,
            )
            self.assertEqual(result, output)
            self._assert_video_audio_output(output, width=180, height=320)

    def test_fit_modes_change_real_media_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self._make_pattern_media(root / "pattern.mp4", 1.0)
            frames: dict[str, bytes] = {}
            for fit in ("cover", "contain", "blur"):
                output = root / f"{fit}.mp4"
                project = {
                    "video": {"path": str(source)},
                    "short_video": {
                        "enabled": True,
                        "output": {"width": 180, "height": 320, "fps": 15},
                        "global_fit": fit,
                        "global_background_color": "FF00FF",
                        "transition": {"type": "cut", "duration": 0.0},
                        "clips": [
                            {
                                "segment_id": f"fit-{fit}",
                                "start": 0.0,
                                "end": 0.8,
                            }
                        ],
                    },
                }
                result = render_short_video(
                    root / f"{fit}.subtitle-project.json",
                    output,
                    _project=project,
                )
                self.assertEqual(result, output)
                self._assert_video_audio_output(output, width=180, height=320)
                frames[fit] = self._read_rgb_frame(output)

            contain_corner = self._pixel(frames["contain"], width=180, x=0, y=0)
            self.assertLess(
                sum(abs(actual - expected) for actual, expected in zip(contain_corner, (255, 0, 255))),
                30,
            )
            for fit in ("cover", "blur"):
                corner = self._pixel(frames[fit], width=180, x=0, y=0)
                self.assertGreater(
                    sum(abs(actual - expected) for actual, expected in zip(corner, (255, 0, 255))),
                    60,
                )
            self.assertGreater(
                self._mean_abs_difference(frames["cover"], frames["blur"]),
                2.0,
            )

    def test_bgm_start_and_loop_produce_audio_after_silence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self._make_video_only(root / "silent.mp4", 1.0)
            bgm = self._make_bgm(root / "bgm.wav", 0.2)
            output = root / "bgm-short.mp4"
            project = {
                "video": {"path": str(source)},
                "short_video": {
                    "enabled": True,
                    "output": {"width": 180, "height": 320, "fps": 15},
                    "transition": {"type": "cut", "duration": 0.0},
                    "bgm": {
                        "path": str(bgm),
                        "in": 0.0,
                        "out": 0.2,
                        "start": 0.3,
                        "volume": 1.0,
                    },
                    "clips": [
                        {
                            "segment_id": "bgm",
                            "start": 0.0,
                            "end": 0.8,
                        }
                    ],
                },
            }

            result = render_short_video(
                root / "bgm.subtitle-project.json",
                output,
                _project=project,
            )
            self.assertEqual(result, output)
            self._assert_video_audio_output(output, width=180, height=320)

            before_start = self._audio_peak(output, start=0.05, duration=0.15)
            after_start = self._audio_peak(output, start=0.35, duration=0.15)
            after_loop = self._audio_peak(output, start=0.60, duration=0.15)
            self.assertGreater(after_start, 2_000)
            self.assertGreater(after_loop, 2_000)
            self.assertLess(before_start * 5, after_start)

    def test_long_silence_cut_filter_script_renders_with_ffmpeg_6(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self._make_media(root / "input.mp4", 6.4)
            output = root / "silence-cut.mp4"
            keep_ranges = [
                (index * 0.1, index * 0.1 + 0.08)
                for index in range(64)
            ]

            cut_media_ranges(str(source), str(output), keep_ranges)

            self._assert_video_audio_output(output, width=320, height=180)


if __name__ == "__main__":
    unittest.main()
