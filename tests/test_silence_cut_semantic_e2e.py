"""無音カット後の動画、字幕、保存済み編集状態を実メディアで確認する。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from src.subtitle_project import create_project, load_project, save_project
from src.subtitle_workflow import render_project_video
from tests.media_test_helpers import (
    FrameRegion,
    MediaSegment,
    assert_frame_difference_present,
    compare_rgb_frames,
    create_lavfi_av_fixture,
    extract_rgb_frame,
    mean_rgb,
    media_duration_seconds,
    probe_media,
    require_media_tools,
    run_media_command,
)


@unittest.skipUnless(
    os.environ.get("RUN_FFMPEG_SMOKE") == "1",
    "set RUN_FFMPEG_SMOKE=1 to exercise semantic media E2E",
)
class SilenceCutSemanticE2ETests(unittest.TestCase):
    def test_silence_cut_retimes_subtitles_and_preserves_source_edits(self) -> None:
        require_media_tools()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = create_lavfi_av_fixture(
                root / "silence source.mp4",
                [
                    MediaSegment("red speech", 1.0, "0x601010", 440),
                    MediaSegment("gray silence", 1.0, "0x303030", 660),
                    MediaSegment("green speech", 1.0, "0x106010", 880),
                ],
                fps=30,
            )
            detection_audio = root / "speech with silence.wav"
            run_media_command(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:sample_rate=48000:duration=3",
                    "-af",
                    "volume=0:enable='between(t,1,2)'",
                    str(detection_audio),
                ],
                context="無音区間を含む検出用音声",
            )

            def render_case(name: str, segments: list[dict[str, object]]) -> tuple[Path, Path]:
                project = create_project(
                    video_path=video.path,
                    output_dir=root,
                    duration_seconds=video.duration_seconds,
                    audio_sources=[{"path": str(detection_audio)}],
                    segments=segments,
                    subtitle_settings={"font_size": 72, "outline_thickness": 2},
                )
                project_path = save_project(root / f"{name}.subtitle-project.json", project)
                output = root / f"{name}.mp4"
                render_project_video(
                    project_path,
                    output,
                    cut_no_speech=True,
                    no_speech_min_seconds=0.7,
                    speech_padding_seconds=0.0,
                    speech_min_clip_seconds=0.25,
                    audio_normalize=False,
                )
                return project_path, output

            original_caption = {"id": "after-silence", "start": 2.2, "end": 2.8, "text": "AFTER SILENCE"}
            project_path, captioned = render_case("captioned", [original_caption])
            _, control = render_case("control", [])
            probe = probe_media(captioned)

            self.assertAlmostEqual(media_duration_seconds(probe), 2.0, delta=0.12)
            self.assertTrue(any(stream["codec_type"] == "audio" for stream in probe["streams"]))
            self.assertGreater(mean_rgb(extract_rgb_frame(captioned, 0.5, probe=probe))[0], 70)
            self.assertGreater(mean_rgb(extract_rgb_frame(captioned, 1.5, probe=probe))[1], 70)
            difference = compare_rgb_frames(
                extract_rgb_frame(control, 1.5),
                extract_rgb_frame(captioned, 1.5, probe=probe),
                region=FrameRegion(x=20, y=80, width=280, height=90),
            )
            assert_frame_difference_present(difference, context="無音カット後の字幕")

            saved = load_project(project_path)
            self.assertEqual(saved["segments"][0]["start"], original_caption["start"])
            self.assertEqual(saved["segments"][0]["end"], original_caption["end"])
            self.assertTrue(saved["render_settings"]["cut_no_speech"])
            self.assertAlmostEqual(saved["render_settings"]["output_duration_seconds"], 2.0, delta=0.02)
            self.assertTrue(Path(saved["render_settings"]["last_cut_output"]).is_file())


if __name__ == "__main__":
    unittest.main()
