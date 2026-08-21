from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.short_video_ass import build_short_video_ass, remap_short_video_segments
from src.short_video import filter_complex_script_option
from src.short_video_schema import (
    ShortVideo,
    ShortVideoBgm,
    ShortVideoClip,
    ShortVideoOutput,
    ShortVideoTransition,
)
from src.short_video_timeline import build_short_video_timeline
from src.subtitle_project import create_project, load_project, save_project
from src.subtitle_workflow import render_project_short_video


class ShortVideoTimelineTests(unittest.TestCase):
    def test_filter_script_option_supports_ffmpeg_6_through_9(self) -> None:
        self.assertEqual(
            filter_complex_script_option("ffmpeg version 6.0-full_build"),
            "-filter_complex_script",
        )
        self.assertEqual(
            filter_complex_script_option("ffmpeg version 7.1.3-full_build"),
            "-/filter_complex",
        )
        self.assertEqual(
            filter_complex_script_option("ffmpeg version 9.0.1-essentials_build"),
            "-/filter_complex",
        )
        self.assertEqual(
            filter_complex_script_option("ffmpeg version N-123456-g89abcdef"),
            "-/filter_complex",
        )

    def test_filter_script_option_reports_actionable_unsupported_version(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "FFmpeg 6 以上へ更新"):
            filter_complex_script_option("ffmpeg version 5.1.6")

        with self.assertRaisesRegex(RuntimeError, "バージョンを判定できません"):
            filter_complex_script_option("unexpected version output")

    def test_crossfade_timeline_is_shared_with_subtitle_remapping(self) -> None:
        short_video = ShortVideo(
            enabled=True,
            transition=ShortVideoTransition(type="crossfade", duration=0.5),
            clips=[
                ShortVideoClip(segment_id="first", start=1.0, end=3.0),
                ShortVideoClip(segment_id="second", start=5.0, end=7.0),
            ],
        )
        timeline = build_short_video_timeline(short_video)
        mapped = remap_short_video_segments(
            [
                {
                    "id": "a",
                    "start": 1.25,
                    "end": 2.0,
                    "text": "first",
                    "words": [{"word": "first", "start": 1.5, "end": 1.75}],
                },
                {"id": "b", "start": 5.25, "end": 6.0, "text": "second", "words": []},
            ],
            short_video,
        )

        self.assertEqual(timeline.clips[1].output_start, 1.5)
        self.assertEqual(timeline.total_duration, 3.5)
        self.assertEqual([(item["start"], item["end"]) for item in mapped], [(0.25, 1.0), (1.75, 2.5)])
        self.assertEqual((mapped[0]["words"][0]["start"], mapped[0]["words"][0]["end"]), (0.5, 0.75))

    def test_build_ass_uses_vertical_resolution_and_scaled_font(self) -> None:
        short_video = ShortVideo(
            enabled=True,
            output=ShortVideoOutput(width=180, height=320, fps=15),
            subtitle_scale_percent=150,
            transition=ShortVideoTransition(type="cut", duration=0.0),
            clips=[ShortVideoClip(segment_id="visible", start=1.0, end=2.0)],
        )
        project = {
            "segments": [
                {
                    "id": "visible",
                    "start": 1.2,
                    "end": 1.8,
                    "text": "VISIBLE",
                    "speaker": "Oz",
                    "words": [],
                }
            ],
            "speakers": [],
            "subtitle_settings": {"font_size": 40},
            "short_video": short_video.to_json(),
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir) / "game.subtitle-project.json"
            output = build_short_video_ass(project_path, _project=project)
            ass_text = output.read_text(encoding="utf-8")

        self.assertIn("PlayResX: 180", ass_text)
        self.assertIn("PlayResY: 320", ass_text)
        self.assertIn(",60,", ass_text)
        dialogue = next(line for line in ass_text.splitlines() if line.startswith("Dialogue:"))
        self.assertIn("0:00:00.20,0:00:00.80", dialogue)
        self.assertIn("VISIBLE", dialogue)

    def test_build_ass_uses_the_documented_scale_boundaries(self) -> None:
        for scale_percent, expected_font_size in ((0, 3), (1, 3), (150, 60), (900, 360)):
            with self.subTest(scale_percent=scale_percent):
                short_video = ShortVideo(
                    enabled=True,
                    output=ShortVideoOutput(width=180, height=320, fps=15),
                    subtitle_scale_percent=scale_percent,
                    transition=ShortVideoTransition(type="cut", duration=0.0),
                    clips=[ShortVideoClip(segment_id="visible", start=1.0, end=2.0)],
                )
                project = {
                    "segments": [
                        {
                            "id": "visible",
                            "start": 1.2,
                            "end": 1.8,
                            "text": "VISIBLE",
                            "speaker": "Oz",
                            "words": [],
                        }
                    ],
                    "speakers": [],
                    "subtitle_settings": {"font_size": 40},
                    "short_video": short_video.to_json(),
                }

                with tempfile.TemporaryDirectory() as temp_dir:
                    output = build_short_video_ass(
                        Path(temp_dir) / "game.subtitle-project.json",
                        _project=project,
                    )
                    ass_text = output.read_text(encoding="utf-8")

                self.assertIn(f",{expected_font_size},", ass_text)


class ShortVideoRenderE2ETests(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"),
        "ffmpeg and ffprobe required",
    )
    def test_project_renders_vertical_video_audio_and_remapped_subtitles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "source.mkv"
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", "color=c=black:size=320x180:rate=15:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(video),
                ],
                check=True,
                capture_output=True,
            )
            segments = [
                {"id": "first", "start": 0.2, "end": 1.0, "text": "FIRST SHORT", "speaker": "Oz", "words": []},
                {"id": "second", "start": 1.5, "end": 2.3, "text": "SECOND SHORT", "speaker": "Oz", "words": []},
            ]
            project = create_project(
                video_path=video,
                output_dir=root,
                segments=segments,
                duration_seconds=3.0,
                subtitle_settings={"font_size": 40},
            )
            project["short_video"] = ShortVideo(
                enabled=True,
                output=ShortVideoOutput(width=180, height=320, fps=15),
                global_fit="contain",
                subtitle_scale_percent=100,
                transition=ShortVideoTransition(type="cut", duration=0.0),
                clips=[
                    ShortVideoClip(segment_id="first", start=0.2, end=1.0, fit="contain"),
                    ShortVideoClip(segment_id="second", start=1.5, end=2.3, fit="contain"),
                ],
            ).to_json()
            project_path = save_project(root / "source.subtitle-project.json", project)

            output = render_project_short_video(
                project_path,
                video_codec="libx264",
                audio_codec="aac",
                x264_crf=30,
            )

            probe = subprocess.run(
                [
                    "ffprobe", "-v", "error", "-show_entries",
                    "format=duration:stream=codec_type,width,height,pix_fmt",
                    "-of", "json", str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            media = json.loads(probe.stdout)
            video_stream = next(item for item in media["streams"] if item["codec_type"] == "video")
            self.assertEqual((video_stream["width"], video_stream["height"]), (180, 320))
            self.assertEqual(video_stream["pix_fmt"], "yuv420p")
            self.assertTrue(any(item["codec_type"] == "audio" for item in media["streams"]))
            self.assertAlmostEqual(float(media["format"]["duration"]), 1.6, delta=0.25)

            ass_path = Path(project["render_settings"].get("short_last_ass", root / "source.short.ass"))
            if not ass_path.is_file():
                ass_path = root / "source.short.ass"
            self.assertTrue(ass_path.is_file())
            dialogue_lines = [
                line for line in ass_path.read_text(encoding="utf-8").splitlines()
                if line.startswith("Dialogue:")
            ]
            self.assertEqual(len(dialogue_lines), 2)

            for timestamp in (0.4, 1.2):
                frame = subprocess.run(
                    [
                        "ffmpeg", "-v", "error", "-ss", str(timestamp), "-i", str(output),
                        "-frames:v", "1", "-vf", "format=gray", "-f", "rawvideo", "-",
                    ],
                    check=True,
                    capture_output=True,
                ).stdout
                self.assertTrue(frame)
                self.assertGreater(max(frame), 100)

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"),
        "ffmpeg and ffprobe required",
    )
    def test_project_renders_direct_range_without_subtitles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "source.mkv"
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", "color=c=black:size=320x180:rate=15:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(video),
                ],
                check=True,
                capture_output=True,
            )
            project = create_project(
                video_path=video,
                output_dir=root,
                segments=[],
                duration_seconds=2.0,
            )
            project["short_video"] = ShortVideo(
                enabled=True,
                output=ShortVideoOutput(width=180, height=320, fps=15),
                transition=ShortVideoTransition(type="cut", duration=0.0),
                clips=[ShortVideoClip(segment_id="", start=0.25, end=1.25)],
            ).to_json()
            project_path = save_project(root / "source.subtitle-project.json", project)

            output = render_project_short_video(
                project_path,
                video_codec="libx264",
                audio_codec="aac",
                x264_crf=30,
            )

            self.assertTrue(output.is_file())
            saved = load_project(project_path)
            self.assertNotIn("short_last_ass", saved["render_settings"])
            probe = subprocess.run(
                [
                    "ffprobe", "-v", "error", "-show_entries",
                    "stream=codec_type,width,height,pix_fmt", "-of", "json", str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            streams = json.loads(probe.stdout)["streams"]
            video_stream = next(item for item in streams if item["codec_type"] == "video")
            self.assertEqual((video_stream["width"], video_stream["height"]), (180, 320))
            self.assertEqual(video_stream["pix_fmt"], "yuv420p")
            self.assertTrue(any(item["codec_type"] == "audio" for item in streams))

    @unittest.skipUnless(
        os.environ.get("RUN_FFMPEG_SMOKE") == "1"
        and shutil.which("ffmpeg")
        and shutil.which("ffprobe"),
        "RUN_FFMPEG_SMOKE=1 with ffmpeg and ffprobe required",
    )
    def test_project_renders_all_fits_crossfade_bgm_and_faststart_in_unicode_workspace(
        self,
    ) -> None:
        """Exercise the Windows FFmpeg filter-script path with real media."""
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "日本語ワークスペース"
            workspace.mkdir()
            video = workspace / "入力動画.mkv"
            bgm = workspace / "背景音.wav"
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", "color=c=red:size=320x180:rate=15:duration=2",
                    "-f", "lavfi", "-i", "color=c=blue:size=320x180:rate=15:duration=2",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
                    "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", str(video),
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "ffmpeg", "-y", "-f", "lavfi",
                    "-i", "sine=frequency=660:sample_rate=48000:duration=1.5",
                    "-c:a", "pcm_s16le", str(bgm),
                ],
                check=True,
                capture_output=True,
            )

            segments = [
                {"id": "first", "start": 0.2, "end": 1.6, "text": "FIRST", "speaker": "A", "words": []},
                {"id": "second", "start": 1.4, "end": 2.8, "text": "SECOND", "speaker": "B", "words": []},
            ]
            for fit in ("cover", "contain", "blur"):
                project = create_project(
                    video_path=video,
                    output_dir=workspace,
                    segments=segments,
                    duration_seconds=4.0,
                    subtitle_settings={"font_size": 40},
                )
                project["short_video"] = ShortVideo(
                    enabled=True,
                    output=ShortVideoOutput(width=180, height=320, fps=15),
                    global_fit=fit,
                    global_background_color="112233",
                    subtitle_scale_percent=80,
                    transition=ShortVideoTransition(type="crossfade", duration=0.2),
                    bgm=ShortVideoBgm(
                        path=str(bgm),
                        in_point=0.2,
                        out_point=0.9,
                        start=0.1,
                        volume=0.4,
                    ),
                    clips=[
                        ShortVideoClip(segment_id="first", start=0.2, end=1.6),
                        ShortVideoClip(segment_id="second", start=2.0, end=3.4),
                    ],
                ).to_json()
                project_path = save_project(
                    workspace / f"{fit}.subtitle-project.json", project
                )
                output = render_project_short_video(
                    project_path,
                    workspace / f"{fit}.mp4",
                    video_codec="libx264",
                    audio_codec="aac",
                    x264_crf=30,
                )

                probe = subprocess.run(
                    [
                        "ffprobe", "-v", "error", "-show_entries",
                        "format=duration,format_name:stream=codec_type,width,height,r_frame_rate,pix_fmt",
                        "-of", "json", str(output),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                media = json.loads(probe.stdout)
                video_stream = next(
                    item for item in media["streams"] if item["codec_type"] == "video"
                )
                self.assertEqual(
                    (video_stream["width"], video_stream["height"]), (180, 320)
                )
                self.assertEqual(video_stream["r_frame_rate"], "15/1")
                self.assertEqual(video_stream["pix_fmt"], "yuv420p")
                self.assertTrue(
                    any(item["codec_type"] == "audio" for item in media["streams"])
                )
                self.assertAlmostEqual(float(media["format"]["duration"]), 2.6, delta=0.3)
                self.assertIn("mp4", media["format"]["format_name"])

                audio_bytes = subprocess.run(
                    [
                        "ffmpeg", "-v", "error", "-i", str(output), "-vn",
                        "-t", "0.5", "-ac", "1", "-ar", "8000", "-f", "s16le", "-",
                    ],
                    check=True,
                    capture_output=True,
                ).stdout
                samples = struct.unpack(
                    f"<{len(audio_bytes) // 2}h", audio_bytes[: len(audio_bytes) // 2 * 2]
                )
                self.assertTrue(samples)
                self.assertGreater(max(abs(sample) for sample in samples), 100)

                media_bytes = output.read_bytes()
                self.assertLess(media_bytes.find(b"moov"), media_bytes.find(b"mdat"))
                saved_project = json.loads(project_path.read_text(encoding="utf-8"))
                ass_path = Path(saved_project["render_settings"]["short_last_ass"])
                dialogue_lines = [
                    line for line in ass_path.read_text(encoding="utf-8").splitlines()
                    if line.startswith("Dialogue:")
                ]
                self.assertGreaterEqual(len(dialogue_lines), 2)
                self.assertTrue(any("FIRST" in line for line in dialogue_lines))

                frame = subprocess.run(
                    [
                        "ffmpeg", "-v", "error", "-ss", "0.7", "-i", str(output),
                        "-frames:v", "1", "-vf", "format=gray", "-f", "rawvideo", "-",
                    ],
                    check=True,
                    capture_output=True,
                ).stdout
                self.assertTrue(frame)
                self.assertGreater(max(frame), 0)

                transition_pixel = subprocess.run(
                    [
                        "ffmpeg", "-v", "error", "-ss", "1.3", "-i", str(output),
                        "-frames:v", "1", "-vf", "crop=1:1:90:160,format=rgb24",
                        "-f", "rawvideo", "-",
                    ],
                    check=True,
                    capture_output=True,
                ).stdout
                self.assertEqual(len(transition_pixel), 3)
                self.assertGreater(transition_pixel[0], 40)
                self.assertGreater(transition_pixel[2], 40)


if __name__ == "__main__":
    unittest.main()
