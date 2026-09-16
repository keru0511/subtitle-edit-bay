from __future__ import annotations

import math
import os
import tempfile
import unittest
from pathlib import Path

from src.subtitle_project import create_project, load_project, save_project
from src.subtitle_workflow import render_project_video
from src.video_encoding import select_automatic_video_codec
from src.video_sequence import VideoSequence
from tests.media_test_helpers import (
    audio_streams,
    MediaSegment,
    create_lavfi_av_fixture,
    extract_rgb_frame,
    mean_rgb,
    measure_audio_level,
    media_duration_seconds,
    probe_media,
    require_media_tools,
    video_stream,
)


FIXTURE_FPS = 30
TRANSITION_SECONDS = 0.25
DURATION_TOLERANCE_SECONDS = 1 / FIXTURE_FPS + 0.03
FIRST_TONE_HZ = 440
SECOND_TONE_HZ = 880
THIRD_TONE_HZ = 1_320
THIRD_AFTER_TONE_HZ = 1_540


@unittest.skipUnless(
    os.environ.get("RUN_FFMPEG_SMOKE") == "1",
    "set RUN_FFMPEG_SMOKE=1 to exercise semantic media E2E",
)
class SequenceRenderSemanticE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        require_media_tools()
        cls._temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._temporary.cleanup)
        cls.root = Path(cls._temporary.name)
        cls.first_fixture = create_lavfi_av_fixture(
            cls.root / "sequence source A.mp4",
            [
                MediaSegment("A-before", 1.0, "0x102040", FIRST_TONE_HZ),
                MediaSegment("A-after", 1.0, "0x401020", 660),
            ],
            fps=FIXTURE_FPS,
            audio_channel_layout="mono",
        )
        cls.second_fixture = create_lavfi_av_fixture(
            cls.root / "sequence source B.mp4",
            [
                MediaSegment("B-before", 1.0, "0x106010", SECOND_TONE_HZ),
                MediaSegment("B-after", 1.0, "0x604010", 1_100),
            ],
            fps=FIXTURE_FPS,
            audio_channel_layout="mono",
        )
        cls.third_fixture = create_lavfi_av_fixture(
            cls.root / "sequence source C.mp4",
            [
                MediaSegment("C-before", 1.0, "0x106060", THIRD_TONE_HZ),
                MediaSegment("C-after", 1.0, "0x602010", THIRD_AFTER_TONE_HZ),
            ],
            fps=FIXTURE_FPS,
            audio_channel_layout="mono",
        )
        cls.selected_codec = select_automatic_video_codec(nvenc_available=False)
        sequence_payload = {
            "schema_version": 1,
            "assets": [
                {
                    "id": "asset-a",
                    "path": str(cls.first_fixture.path),
                    "duration_seconds": cls.first_fixture.duration_seconds,
                },
                {
                    "id": "asset-b",
                    "path": str(cls.second_fixture.path),
                    "duration_seconds": cls.second_fixture.duration_seconds,
                },
                {
                    "id": "asset-c",
                    "path": str(cls.third_fixture.path),
                    "duration_seconds": cls.third_fixture.duration_seconds,
                },
            ],
            "clips": [
                {
                    "id": "clip-a",
                    "asset_id": "asset-a",
                    "source_start": 0.25,
                    "source_end": 1.75,
                    "transition": {"type": "cut", "duration": 0.0},
                    "volume": 0.5,
                },
                {
                    "id": "clip-b",
                    "asset_id": "asset-b",
                    "source_start": 0.25,
                    "source_end": 1.75,
                    "transition": {"type": "cut", "duration": 0.0},
                    "volume": 0.75,
                },
                {
                    "id": "clip-c",
                    "asset_id": "asset-c",
                    "source_start": 0.25,
                    "source_end": 1.75,
                    "transition": {"type": "crossfade", "duration": TRANSITION_SECONDS},
                    "audio_offset_seconds": -0.1,
                },
            ],
        }
        cls.sequence = VideoSequence.from_json(sequence_payload)
        project = create_project(
            video_path=cls.first_fixture.path,
            output_dir=cls.root,
            segments=[],
            duration_seconds=cls.first_fixture.duration_seconds,
            sequence=cls.sequence.to_json(),
        )
        cls.project_path = save_project(cls.root / "sequence semantic.subtitle-project.json", project)
        cls.output = cls.root / "sequence-render.mp4"
        render_project_video(
            cls.project_path,
            cls.output,
            video_codec=cls.selected_codec,
            audio_codec="aac",
            audio_normalize=False,
        )
        if not cls.output.is_file() or cls.output.stat().st_size <= 0:
            raise AssertionError(f"Sequence render did not create output: {cls.output}")
        cls.output_probe = probe_media(cls.output)
        cls.first_probe = probe_media(cls.first_fixture.path)

    def test_cpu_fallback_preserves_output_media_contract(self) -> None:
        self.assertEqual(self.selected_codec, "libx264")
        rendered_video = video_stream(self.output_probe)
        self.assertEqual(rendered_video.get("codec_name"), "h264")
        self.assertEqual(rendered_video.get("pix_fmt"), "yuv420p")
        rendered_audio = audio_streams(self.output_probe)
        self.assertEqual(len(rendered_audio), 1)
        self.assertEqual(rendered_audio[0].get("codec_name"), "aac")
        self.assertEqual(rendered_audio[0].get("sample_rate"), "48000")
        self.assertEqual(rendered_audio[0].get("channel_layout"), "mono")

    def test_trim_transition_duration_matches_sequence_contract(self) -> None:
        self.assertAlmostEqual(
            media_duration_seconds(self.output_probe),
            self.sequence.output_duration,
            delta=DURATION_TOLERANCE_SECONDS,
        )
        persisted = load_project(self.project_path)
        self.assertEqual(
            persisted["render_settings"]["sequence_clip_count"],
            3,
        )
        self.assertAlmostEqual(
            persisted["render_settings"]["output_duration_seconds"],
            4.25,
        )

    def test_clip_order_trim_boundaries_and_crossfade_are_visible(self) -> None:
        # A starts at source 0.25s, changes from blue to red at source 1.0s,
        # B follows at a cut, and C enters at output 2.75s after the
        # 0.25s crossfade.
        a_before = mean_rgb(extract_rgb_frame(self.output, 0.60, probe=self.output_probe))
        a_after = mean_rgb(extract_rgb_frame(self.output, 1.00, probe=self.output_probe))
        b_before = mean_rgb(extract_rgb_frame(self.output, 1.65, probe=self.output_probe))
        b_after = mean_rgb(extract_rgb_frame(self.output, 2.40, probe=self.output_probe))
        c_before = mean_rgb(extract_rgb_frame(self.output, 3.20, probe=self.output_probe))
        c_after = mean_rgb(extract_rgb_frame(self.output, 3.70, probe=self.output_probe))
        overlap = mean_rgb(extract_rgb_frame(self.output, 2.875, probe=self.output_probe))

        self.assertGreater(a_before[2], a_before[0] + 20)
        self.assertGreater(a_before[2], a_before[1] + 20)
        self.assertGreater(a_after[0], a_after[1] + 20)
        self.assertGreater(a_after[0], a_after[2] + 10)
        self.assertGreater(b_before[1], b_before[0] + 25)
        self.assertGreater(b_before[1], b_before[2] + 25)
        self.assertGreater(b_after[0], b_after[2] + 25)
        self.assertGreater(b_after[1], b_after[2] + 20)
        self.assertGreater(c_before[1], c_before[0] + 20)
        self.assertGreater(c_before[2], c_before[0] + 20)
        self.assertGreater(c_after[0], c_after[1] + 25)
        self.assertGreater(c_after[0], c_after[2] + 25)
        # The transition frame contains meaningful contributions from both
        # outgoing B and incoming C, rather than a hard clip boundary.
        for channel in overlap:
            self.assertGreater(channel, 20)

    def test_clip_audio_settings_survive_cut_crossfade_and_negative_offset(self) -> None:
        source_level = measure_audio_level(
            self.first_fixture.path,
            frequency_hz=FIRST_TONE_HZ,
            bandwidth_hz=70,
            start_seconds=0.35,
            duration_seconds=0.5,
        )
        rendered_level = measure_audio_level(
            self.output,
            frequency_hz=FIRST_TONE_HZ,
            bandwidth_hz=70,
            start_seconds=0.10,
            duration_seconds=0.5,
        )
        self.assertAlmostEqual(
            rendered_level.mean_volume_db - source_level.mean_volume_db,
            20 * math.log10(0.5),
            delta=2.5,
            msg=(
                "Expected clip-a volume=0.5 to reduce its source tone by about 6 dB.\n"
                f"source={source_level.describe()}\nrendered={rendered_level.describe()}"
            ),
        )
        middle_source_level = measure_audio_level(
            self.second_fixture.path,
            frequency_hz=SECOND_TONE_HZ,
            bandwidth_hz=70,
            start_seconds=0.35,
            duration_seconds=0.35,
        )
        middle_clip_level = measure_audio_level(
            self.output,
            frequency_hz=SECOND_TONE_HZ,
            bandwidth_hz=70,
            start_seconds=1.65,
            duration_seconds=0.35,
        )
        self.assertLess(
            abs(
                middle_clip_level.mean_volume_db
                - middle_source_level.mean_volume_db
                - 20 * math.log10(0.75)
            ),
            2.5,
            msg=(
                "Expected clip-b volume=0.75 to be preserved across concat.\n"
                f"source={middle_source_level.describe()}\nrendered={middle_clip_level.describe()}"
            ),
        )
        last_clip_level = measure_audio_level(
            self.output,
            frequency_hz=THIRD_AFTER_TONE_HZ,
            bandwidth_hz=70,
            start_seconds=3.42,
            duration_seconds=0.16,
        )
        self.assertGreater(
            last_clip_level.max_volume_db,
            -35.0,
            msg=(
                "The final clip audio did not survive the crossfade/negative offset. "
                f"{last_clip_level.describe()}"
            ),
        )
