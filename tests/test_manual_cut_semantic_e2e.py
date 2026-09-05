from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.subtitle_project import create_project, derive_ass_path, load_project, save_project
from src.subtitle_workflow import render_project_video
from tests.media_test_utils import (
    FrameRegion,
    MediaSegment,
    assert_frame_difference_present,
    compare_rgb_frames,
    create_lavfi_av_fixture,
    extract_rgb_frame,
    mean_rgb,
    measure_audio_level,
    media_duration_seconds,
    probe_media,
    require_media_tools,
)


FIXTURE_FPS = 30
CUT_START_SECONDS = 1.0
CUT_END_SECONDS = 2.0
DURATION_TOLERANCE_SECONDS = 1 / FIXTURE_FPS + 0.03
SUBTITLE_REGION = FrameRegion(x=20, y=80, width=280, height=90)


@unittest.skipUnless(
    os.environ.get("RUN_FFMPEG_SMOKE") == "1",
    "set RUN_FFMPEG_SMOKE=1 to exercise semantic media E2E",
)
class ManualCutSemanticE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        require_media_tools()
        cls._temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._temporary.cleanup)
        cls.root = Path(cls._temporary.name)
        cls.fixture = create_lavfi_av_fixture(
            cls.root / "manual cut source.mp4",
            [
                MediaSegment("blue 440", 1.0, "0x102060", 440),
                MediaSegment("gray 660 removed", 1.0, "0x303030", 660),
                MediaSegment("red 880", 1.0, "0x601010", 880),
                MediaSegment("green 1100", 1.0, "0x106010", 1100),
            ],
            fps=FIXTURE_FPS,
        )
        timeline = {
            "cuts": [
                {
                    "id": "semantic-cut",
                    "source_start": CUT_START_SECONDS,
                    "source_end": CUT_END_SECONDS,
                }
            ]
        }
        cls.output = cls._render_case(
            "with-subtitle",
            [
                {"id": "removed", "start": 1.2, "end": 1.8, "text": "REMOVED"},
                {"id": "after", "start": 2.2, "end": 2.8, "text": "AFTER CUT"},
            ],
            timeline,
        )
        # A source-time subtitle beyond the media keeps the same two-pass render
        # path while producing no visible control caption.
        cls.control = cls._render_case(
            "control",
            [{"id": "outside", "start": 10.0, "end": 11.0, "text": "."}],
            timeline,
        )
        cls.output_probe = probe_media(cls.output)
        cls.control_probe = probe_media(cls.control)

    @classmethod
    def _render_case(
        cls,
        name: str,
        segments: list[dict[str, object]],
        timeline: dict[str, object],
    ) -> Path:
        project = create_project(
            video_path=cls.fixture.path,
            output_dir=cls.root,
            segments=segments,
            duration_seconds=cls.fixture.duration_seconds,
            subtitle_settings={"font_size": 72, "outline_thickness": 2},
            timeline=timeline,
        )
        project_path = save_project(cls.root / f"{name}.subtitle-project.json", project)
        output = cls.root / f"{name}.mp4"
        render_project_video(
            project_path,
            output,
            video_codec="libx264",
            audio_codec="aac",
            x264_crf=24,
            audio_normalize=False,
        )
        if not output.is_file() or output.stat().st_size <= 0:
            raise AssertionError(f"Manual-cut render did not create output: {output}")
        return output

    def test_final_duration_matches_the_output_timeline(self) -> None:
        self.assertAlmostEqual(
            media_duration_seconds(self.output_probe),
            3.0,
            delta=DURATION_TOLERANCE_SECONDS,
        )

    def test_fractional_cuts_keep_media_and_subtitles_on_one_output_clock(self) -> None:
        timeline = {
            "cuts": [
                {"id": f"cut-{index}", "source_start": round(index * 0.2 + 0.075, 3),
                 "source_end": round(index * 0.2 + 0.125, 3)}
                for index in range(15)
            ]
        }
        segments = [{"id": "tail", "start": 3.2, "end": 3.8, "text": "AFTER CUTS"}]
        output = self._render_case("fractional-subtitle", segments, timeline)
        control = self._render_case("fractional-control", [], timeline)
        # The same mapping must also work without an audio stream to set concat's clock.
        video_only = self.root / "fractional-source-video-only.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(self.fixture.path), "-an", "-c:v", "copy", str(video_only)],
            check=True,
            timeout=30,
        )
        project = create_project(
            video_path=video_only, output_dir=self.root, segments=[],
            duration_seconds=4.0, timeline=timeline,
        )
        project_path = save_project(self.root / "fractional-video-only.json", project)
        silent_output = render_project_video(
            project_path, self.root / "fractional-video-only.mp4", audio_normalize=False,
        )
        for result in (output, control, silent_output):
            with self.subTest(output=result.name):
                probe = probe_media(result)
                self.assertAlmostEqual(media_duration_seconds(probe), 3.25, delta=DURATION_TOLERANCE_SECONDS)
                for stream in probe["streams"]:
                    self.assertAlmostEqual(float(stream["duration"]), 3.25, delta=DURATION_TOLERANCE_SECONDS)
                # Source green starts at 3s, mapped to 2.25s, not the old 2.5s.
                red, green, blue = mean_rgb(extract_rgb_frame(result, 2.35, probe=probe))
                self.assertGreater(green, red + 30)
                self.assertGreater(green, blue + 30)

        ass = derive_ass_path(self.root / "fractional-subtitle.subtitle-project.json").read_text(encoding="utf-8")
        self.assertIn("0:00:02.45,0:00:03.05", ass)
        difference = compare_rgb_frames(
            extract_rgb_frame(control, 2.7), extract_rgb_frame(output, 2.7), region=SUBTITLE_REGION,
        )
        assert_frame_difference_present(difference, context="caption after fifteen fractional cuts")
        self.assertEqual(load_project(self.root / "fractional-subtitle.subtitle-project.json")["segments"][0]["start"], 3.2)

    def test_missing_duration_is_probed_and_preserves_the_uncaptioned_tail(self) -> None:
        project = create_project(
            video_path=self.fixture.path, output_dir=self.root,
            segments=[{"start": 0.0, "end": 2.0, "text": "EARLY CAPTION"}],
            duration_seconds=4.0,
            timeline={"cuts": [{"id": "cut", "source_start": 0.5, "source_end": 1.0}]},
        )
        # Simulate a legacy import, including cuts saved by the old fallback.
        project["video"]["duration_seconds"] = 0.0
        project_path = save_project(
            self.root / "legacy-missing-duration.json", project, project_is_validated=True,
        )
        output = render_project_video(
            project_path, self.root / "legacy-missing-duration.mp4", audio_normalize=False,
        )
        probe = probe_media(output)
        self.assertAlmostEqual(media_duration_seconds(probe), 3.5, delta=DURATION_TOLERANCE_SECONDS)
        red, green, blue = mean_rgb(extract_rgb_frame(output, 3.2, probe=probe))
        self.assertGreater(green, red + 30)
        self.assertGreater(green, blue + 30)
        self.assertAlmostEqual(load_project(project_path)["video"]["duration_seconds"], 4.0, delta=0.03)

    def test_output_frame_after_cut_comes_from_the_next_source_range(self) -> None:
        frame = extract_rgb_frame(self.output, 1.5, probe=self.output_probe)
        red, green, blue = mean_rgb(frame)

        self.assertGreater(red, green + 30, (red, green, blue))
        self.assertGreater(red, blue + 30, (red, green, blue))

    def test_removed_audio_frequency_is_absent_while_kept_frequencies_remain(self) -> None:
        removed = measure_audio_level(self.output, frequency_hz=660, bandwidth_hz=35)
        kept = [
            measure_audio_level(self.output, frequency_hz=frequency, bandwidth_hz=35) for frequency in (440, 880, 1100)
        ]

        self.assertGreater(
            min(measurement.mean_volume_db for measurement in kept),
            removed.mean_volume_db + 12.0,
            "\n\n".join([removed.describe(), *(measurement.describe() for measurement in kept)]),
        )

    def test_subtitle_is_dropped_or_retimed_and_visible_at_output_time(self) -> None:
        ass_path = derive_ass_path(self.root / "with-subtitle.subtitle-project.json")
        dialogue_lines = [
            line for line in ass_path.read_text(encoding="utf-8").splitlines() if line.startswith("Dialogue:")
        ]
        self.assertEqual(len(dialogue_lines), 1)
        self.assertIn("AFTER CUT", dialogue_lines[0])
        self.assertNotIn("REMOVED", dialogue_lines[0])
        self.assertIn("0:00:01.20", dialogue_lines[0])
        self.assertIn("0:00:01.80", dialogue_lines[0])

        control_frame = extract_rgb_frame(self.control, 1.5, probe=self.control_probe)
        subtitle_frame = extract_rgb_frame(self.output, 1.5, probe=self.output_probe)
        difference = compare_rgb_frames(
            control_frame,
            subtitle_frame,
            region=SUBTITLE_REGION,
        )
        assert_frame_difference_present(difference, context="retimed subtitle after manual cut")


if __name__ == "__main__":
    unittest.main()
