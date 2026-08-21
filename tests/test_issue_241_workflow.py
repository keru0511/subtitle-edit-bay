from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.burn_subs import build_ffmpeg_command
from src.subtitle_project import create_project, load_project, save_project
from src.subtitle_workflow import render_project_video


class Issue241WorkflowTests(unittest.TestCase):
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
            self.assertEqual(loaded["audio_sources"][0]["path"], str(audio))

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

            self.assertIsNone(burn.call_args.args[1])

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

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"),
        "ffmpeg and ffprobe required",
    )
    def test_video_only_project_renders_without_audio_stream(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video-only.mkv"
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15:duration=1",
                    "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
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
                    "ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                    "-of", "json", str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                [stream["codec_type"] for stream in json.loads(probe.stdout)["streams"]],
                ["video"],
            )

    def test_workflow_qml_exposes_independent_empty_project_actions(self) -> None:
        qml_path = Path(__file__).resolve().parents[1] / "src" / "ui" / "screens" / "MainWorkflowScreen.qml"
        qml = qml_path.read_text(encoding="utf-8")

        self.assertIn('objectName: "createEmptyProjectButton"', qml)
        self.assertIn("root.appBackend.createEmptyProject()", qml)
        self.assertIn('visible: root.appBackend.projectLoaded', qml)
        self.assertIn('"動画を書き出す"', qml)
        self.assertIn('objectName: "editorEmptyState"', qml)
        self.assertIn('objectName: "transcriptionMergeDialog"', qml)
        self.assertIn('root.appBackend.transcribeProject(root.currentSettings(), "merge")', qml)
        self.assertIn('root.appBackend.transcribeProject(root.currentSettings(), "replace")', qml)


if __name__ == "__main__":
    unittest.main()
