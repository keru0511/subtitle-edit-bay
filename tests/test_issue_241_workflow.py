from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.burn_subs import build_ffmpeg_command
from src.data_boundary import is_object_list, is_string_object_mapping
from src.subtitle_project import create_project, load_project, save_project
from src.subtitle_workflow import render_project_video
from tests.typed_case import TypedTestCase


class Issue241WorkflowTests(TypedTestCase):
    def test_empty_project_is_a_valid_editable_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            audio = root / "speaker.wav"
            video.write_bytes(b"video")
            audio.write_bytes(b"audio")
            project_path = root / "game.subtitle-project.json"

            project = create_project(
                video_path=video,
                output_dir=root,
                audio_sources=[{"path": str(audio), "file_name": audio.name}],
                speakers=[{"name": "speaker", "style": "Speaker_speaker", "path": str(audio)}],
                segments=[],
            )
            save_project(project_path, project)

            loaded = load_project(project_path)
            self.assertEqual(loaded["segments"], [])
            audio_sources = loaded["audio_sources"]
            assert is_object_list(audio_sources)
            first_source = audio_sources[0]
            assert is_string_object_mapping(first_source)
            self.assertEqual(first_source["path"], str(audio))

    def test_empty_project_render_skips_ass_generation_and_filter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            video.write_bytes(b"video")
            project_path = root / "game.subtitle-project.json"
            save_project(
                project_path,
                create_project(video_path=video, output_dir=root, segments=[]),
            )

            with (
                patch("src.subtitle_workflow.build_project_ass", side_effect=AssertionError("ASS must be skipped")),
                patch("src.subtitle_workflow.run_ffmpeg_burn") as burn,
            ):
                render_project_video(project_path, audio_normalize=False)

            call_arguments: object = burn.call_args.args
            assert isinstance(call_arguments, tuple)
            self.assertIsNone(call_arguments[1])

    def test_ffmpeg_command_without_subtitle_has_no_video_filter(self) -> None:
        command = build_ffmpeg_command(
            "video.mkv",
            None,
            "output.mp4",
            audio_codec="aac",
        )

        self.assertNotIn("-vf", command)
        self.assertIn("-map", command)

    def test_ffmpeg_command_without_audio_stream_omits_audio_mapping_and_codec(self) -> None:
        command = build_ffmpeg_command(
            "video-only.mkv",
            None,
            "output.mp4",
            audio_codec="aac",
            include_audio=False,
        )

        self.assertEqual(command.count("-map"), 1)
        self.assertIn("0:v:0", command)
        self.assertNotIn("0:a:0", command)
        self.assertNotIn("-c:a", command)

    def test_video_only_project_renders_without_audio_stream(self) -> None:
        if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
            self.skipTest("ffmpeg and ffprobe required")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video-only.mkv"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=320x180:rate=15:duration=1",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(video),
                ],
                check=True,
                capture_output=True,
            )
            project_path = save_project(
                root / "video-only.subtitle-project.json",
                create_project(video_path=video, output_dir=root, segments=[], duration_seconds=1.0),
            )

            output = render_project_video(
                project_path,
                root / "rendered.mp4",
                video_codec="libx264",
                audio_codec="aac",
                x264_crf=30,
            )

            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "stream=codec_type",
                    "-of",
                    "json",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload: object = json.loads(probe.stdout)
            assert is_string_object_mapping(payload)
            streams = payload["streams"]
            assert is_object_list(streams)
            codec_types: list[object] = []
            for stream in streams:
                assert is_string_object_mapping(stream)
                codec_types.append(stream["codec_type"])
            self.assertEqual(codec_types, ["video"])


if __name__ == "__main__":
    unittest.main()
