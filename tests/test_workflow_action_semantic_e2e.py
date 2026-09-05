from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from src.runtime_dependencies import RuntimeDependencyStatus
from src.subtitle_project import create_project, derive_ass_path, load_project, save_project
from src.workflow_actions import prepare_render_request
from tests.media_test_utils import (
    FrameRegion, MediaSegment, compare_rgb_frames, create_lavfi_av_fixture,
    extract_rgb_frame, mean_rgb, measure_audio_level, media_duration_seconds,
    probe_media, require_media_tools, run_media_command, video_stream,
)


@unittest.skipUnless(os.environ.get("RUN_FFMPEG_SMOKE") == "1", "set RUN_FFMPEG_SMOKE=1")
class WorkflowActionSemanticE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        require_media_tools()
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        cls.fixture = create_lavfi_av_fixture(
            cls.root / "source.mp4",
            [MediaSegment("blue", 1, "0x102060", 440), MediaSegment("red", 1, "0x601010", 880)],
            fps=30,
        )

    def test_common_action_exports_zero_caption_cpu_artifacts(self) -> None:
        # The declared environment has no ASR/CUDA/NVENC; the selected command
        # and config still execute real FFmpeg and produce inspectable media.
        dependencies = RuntimeDependencyStatus(True, True, False, cuda=False, nvenc=False)
        for short in (False, True):
            with self.subTest(short=short):
                project = create_project(
                    video_path=self.fixture.path, output_dir=self.root, segments=[], duration_seconds=2,
                )
                project["short_video"] = {
                    "enabled": True,
                    "output": {"width": 180, "height": 320, "fps": 30},
                    "global_fit": "contain",
                    "clips": [{"start": 0, "end": 2}],
                }
                path = save_project(self.root / f"zero-{short}.subtitle-project.json", project)
                config_path = self.root / f"config-{short}.json"
                request = prepare_render_request(dependencies, project, str(path), config_path, short=short)
                self.assertEqual(request.video_codec, "libx264")
                config_path.write_text(json.dumps({"shared": {
                    "video_codec": request.video_codec, "audio_codec": "aac",
                    "audio_normalize": False, "x264_crf": 18,
                }}), encoding="utf-8")
                result = run_media_command(
                    request.command, context=f"zero captions, cpu fallback, short={short}",
                    timeout_seconds=60,
                )
                self.assertNotIn("subtitles=", result.stdout + result.stderr)
                self.assertFalse(derive_ass_path(path).exists())
                self.assertEqual(load_project(path)["segments"], [])
                probe = probe_media(request.output_path)
                stream = video_stream(probe)
                self.assertEqual(stream["codec_name"], "h264")
                self.assertEqual(stream["pix_fmt"], "yuv420p")
                self.assertEqual((stream["width"], stream["height"]), (180, 320) if short else (320, 180))
                self.assertAlmostEqual(media_duration_seconds(probe), 2, delta=0.07)
                for timestamp, dominant in ((0.5, 2), (1.5, 0)):
                    frame = extract_rgb_frame(request.output_path, timestamp, probe=probe)
                    color = mean_rgb(frame, region=FrameRegion(50, 140, 80, 40)) if short else mean_rgb(frame)
                    self.assertGreater(color[dominant], max(color[index] for index in range(3) if index != dominant) + 30)
                    if not short:
                        difference = compare_rgb_frames(extract_rgb_frame(self.fixture.path, timestamp), frame)
                        self.assertLess(difference.mean_absolute_channel_delta, 2.0, difference.describe())
                for frequency in (440, 880):
                    level = measure_audio_level(request.output_path, frequency_hz=frequency, bandwidth_hz=35)
                    self.assertGreater(level.mean_volume_db, -45, level.describe())
