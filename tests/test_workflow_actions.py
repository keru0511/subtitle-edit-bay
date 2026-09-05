from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.runtime_dependencies import RuntimeDependencyStatus
from src.subtitle_project import create_project
from src.workflow_actions import (
    prepare_render_request, render_capability, render_output_path,
    transcription_capability, validate_render_output,
)


class WorkflowActionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.video = self.root / "source.mp4"
        self.video.write_bytes(b"fixture")
        self.path = str(self.root / "project.subtitle-project.json")
        self.project = create_project(
            video_path=self.video, output_dir=self.root, segments=[], duration_seconds=2,
        )
        self.project["short_video"] = {"enabled": True, "clips": [{"start": 0, "end": 1}]}
        self.dependencies = RuntimeDependencyStatus(True, True, False, cuda=False, nvenc=False)

    def transcription(self, dependencies, *, device="cpu", **kwargs):
        return transcription_capability(
            dependencies, device=device, has_video=True, has_audio=True,
            output_dir=str(self.root), **kwargs,
        )

    def test_whisperx_and_cuda_only_gate_the_corresponding_transcription(self) -> None:
        self.assertIn("whisperx", self.transcription(self.dependencies).reason)
        installed = replace(self.dependencies, whisperx=True)
        self.assertTrue(self.transcription(installed).enabled)
        self.assertFalse(self.transcription(installed, device="cuda").enabled)
        self.assertTrue(self.transcription(replace(installed, cuda=True), device="cuda").enabled)

    def test_normal_and_short_use_nvenc_independently_of_torch_and_whisperx(self) -> None:
        for short in (False, True):
            for cuda in (False, True):
                for nvenc, codec in ((False, "libx264"), (True, "h264_nvenc")):
                    with self.subTest(short=short, cuda=cuda, nvenc=nvenc):
                        request = prepare_render_request(
                            replace(self.dependencies, cuda=cuda, nvenc=nvenc),
                            self.project, self.path, self.root / "config.json", short=short,
                        )
                        self.assertEqual(request.video_codec, codec)
                        self.assertEqual(request.job, "render_short" if short else "render")
                        self.assertIn("render-short" if short else "render", request.command)
                        self.assertEqual(request.command[request.command.index("--output") + 1], str(request.output_path))

    def test_ffmpeg_and_ffprobe_are_required_for_both_artifacts(self) -> None:
        for short in (False, True):
            for missing in ("ffmpeg", "ffprobe"):
                with self.subTest(short=short, missing=missing):
                    with self.assertRaisesRegex(ValueError, missing):
                        prepare_render_request(
                            replace(self.dependencies, **{missing: False}),
                            self.project, self.path, "config.json", short=short,
                        )

    def test_short_requires_clips_but_normal_does_not(self) -> None:
        self.project["short_video"]["clips"] = []
        self.assertTrue(render_capability(self.dependencies, self.project, self.path).enabled)
        self.assertFalse(render_capability(self.dependencies, self.project, self.path, short=True).enabled)

    def test_output_validation_is_independent_for_each_artifact(self) -> None:
        output = render_output_path(self.path, short=True)
        output.mkdir()
        self.assertTrue(render_capability(self.dependencies, self.project, self.path).enabled)
        self.assertFalse(render_capability(self.dependencies, self.project, self.path, short=True).enabled)
        self.assertEqual(list(output.iterdir()), [])
        with self.assertRaisesRegex(ValueError, "入力素材"):
            validate_render_output(self.video, self.project, self.path)
        with patch("src.workflow_actions.os.access", return_value=False):
            self.assertFalse(render_capability(self.dependencies, self.project, self.path).enabled)

    def test_missing_project_video_and_busy_state_prevent_start(self) -> None:
        self.assertFalse(render_capability(self.dependencies, None, "").enabled)
        for short in (False, True):
            self.assertFalse(render_capability(self.dependencies, self.project, self.path, short=short, running=True).enabled)
        self.video.unlink()
        self.assertFalse(render_capability(self.dependencies, self.project, self.path).enabled)
        self.assertFalse(self.transcription(replace(self.dependencies, whisperx=True), running=True).enabled)
