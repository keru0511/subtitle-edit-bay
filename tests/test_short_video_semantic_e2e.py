from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

from src.short_video_schema import (
    ShortVideo,
    ShortVideoClip,
    ShortVideoOutput,
    ShortVideoTransition,
)
from src.subtitle_project import create_project, load_project, save_project
from src.subtitle_workflow import render_project_short_video
from src.video_encoding import select_automatic_video_codec
from tests.media_test_utils import (
    AudioLevelMeasurement,
    FrameRegion,
    MediaFixture,
    MediaSegment,
    assert_frame_difference_absent,
    assert_frame_difference_present,
    audio_streams,
    compare_rgb_frames,
    create_lavfi_av_fixture,
    extract_rgb_frame,
    mean_rgb,
    measure_audio_level,
    media_duration_seconds,
    probe_media,
    require_media_tools,
    video_stream,
)


FIXTURE_FPS = 15
CLIP_DURATION_SECONDS = 1.2
BOUNDARY_SECONDS = CLIP_DURATION_SECONDS
MUX_TOLERANCE_SECONDS = 0.02
FRAME_AND_MUX_TOLERANCE_SECONDS = 1 / FIXTURE_FPS + MUX_TOLERANCE_SECONDS
OUTPUT_WIDTH = 180
OUTPUT_HEIGHT = 320
SUBTITLE_REGION = FrameRegion(10, 190, 160, 125)
TONE_BY_CLIP = {"A": 440, "B": 880, "C": 1320}


@unittest.skipUnless(
    os.environ.get("RUN_FFMPEG_SMOKE") == "1",
    "set RUN_FFMPEG_SMOKE=1 to exercise semantic media E2E",
)
class ShortVideoSemanticE2ETests(unittest.TestCase):
    """Verify the rendered C -> A short against source-timeline semantics.

    Clip ranges are source-video times in the current short-video contract.  The
    non-source order deliberately makes a duration-only assertion insufficient.
    """

    _temporary: ClassVar[tempfile.TemporaryDirectory[str]]
    root: ClassVar[Path]
    fixture: ClassVar[MediaFixture]
    selected_codec: ClassVar[str]
    subtitle_output: ClassVar[Path]
    subtitle_project: ClassVar[Path]
    plain_output: ClassVar[Path]
    plain_project: ClassVar[Path]
    subtitle_probe: ClassVar[dict[str, object]]
    plain_probe: ClassVar[dict[str, object]]

    @classmethod
    def setUpClass(cls) -> None:
        require_media_tools()
        cls._temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._temporary.cleanup)
        cls.root = Path(cls._temporary.name)
        cls.fixture = create_lavfi_av_fixture(
            cls.root / "short semantic source.mp4",
            [
                MediaSegment("A", CLIP_DURATION_SECONDS, "red", TONE_BY_CLIP["A"]),
                MediaSegment("B", CLIP_DURATION_SECONDS, "green", TONE_BY_CLIP["B"]),
                MediaSegment("C", CLIP_DURATION_SECONDS, "blue", TONE_BY_CLIP["C"]),
            ],
            fps=FIXTURE_FPS,
        )
        cls.selected_codec = select_automatic_video_codec(nvenc_available=False)
        cls.subtitle_output, cls.subtitle_project = cls._render(
            "with-subtitles",
            segments=[
                {
                    "id": "subtitle-a",
                    "start": 0.45,
                    "end": 0.95,
                    "text": "CLIP A",
                    "speaker": "A",
                    "words": [],
                },
                {
                    "id": "subtitle-b",
                    "start": 1.35,
                    "end": 1.95,
                    "text": "UNSELECTED B",
                    "speaker": "B",
                    "words": [],
                },
                {
                    "id": "subtitle-c",
                    "start": 2.55,
                    "end": 3.05,
                    "text": "CLIP C",
                    "speaker": "C",
                    "words": [],
                },
            ],
        )
        cls.plain_output, cls.plain_project = cls._render("without-subtitles", segments=[])
        cls.subtitle_probe = probe_media(cls.subtitle_output)
        cls.plain_probe = probe_media(cls.plain_output)

    @classmethod
    def _render(
        cls,
        name: str,
        *,
        segments: list[dict[str, object]],
    ) -> tuple[Path, Path]:
        project = create_project(
            video_path=cls.fixture.path,
            output_dir=cls.root,
            segments=segments,
            duration_seconds=cls.fixture.duration_seconds,
            subtitle_settings={"font_size": 52, "outline_thickness": 2},
            # Removing A from the normal-video timeline makes source time and
            # cut-output time observably different. Short clips still select C
            # and A by their original-source ranges.
            timeline={"cuts": [{"id": "normal-cut-a", "source_start": 0.0, "source_end": 1.2}]},
        )
        project["short_video"] = ShortVideo(
            enabled=True,
            output=ShortVideoOutput(
                width=OUTPUT_WIDTH,
                height=OUTPUT_HEIGHT,
                fps=FIXTURE_FPS,
            ),
            global_fit="cover",
            subtitle_scale_percent=100,
            transition=ShortVideoTransition(type="cut", duration=0.0),
            clips=[
                # Source order is A -> B -> C; short order is intentionally C -> A.
                ShortVideoClip(segment_id="C", start=2.4, end=3.6),
                ShortVideoClip(segment_id="A", start=0.0, end=1.2),
            ],
        ).to_json()
        project_path = save_project(cls.root / f"{name}.subtitle-project.json", project)
        output = render_project_short_video(
            project_path,
            cls.root / f"{name}.mp4",
            video_codec=cls.selected_codec,
            audio_codec="aac",
            x264_crf=18,
        )
        return output, project_path

    def _assert_expected_color(self, timestamp: float, expected_clip: str) -> None:
        frame = extract_rgb_frame(self.plain_output, timestamp, probe=self.plain_probe)
        measured = mean_rgb(frame)
        expected_channel = {"A": 0, "B": 1, "C": 2}[expected_clip]
        other_channels = [value for index, value in enumerate(measured) if index != expected_channel]
        self.assertGreater(
            measured[expected_channel],
            max(other_channels) + 80,
            f"expected clip {expected_clip} at output {timestamp:.3f}s; measured RGB={measured}",
        )

    def _band_measurement(self, clip: str, *, start: float) -> AudioLevelMeasurement:
        return measure_audio_level(
            self.plain_output,
            frequency_hz=TONE_BY_CLIP[clip],
            bandwidth_hz=70,
            start_seconds=start,
            duration_seconds=0.55,
        )

    def test_clip_order_boundary_duration_and_unselected_media(self) -> None:
        tolerance = FRAME_AND_MUX_TOLERANCE_SECONDS
        self._assert_expected_color(0.35, "C")
        self._assert_expected_color(BOUNDARY_SECONDS - tolerance, "C")
        self._assert_expected_color(BOUNDARY_SECONDS + tolerance, "A")
        self._assert_expected_color(2.05, "A")

        for timestamp in (0.2, 0.7, 1.45, 2.0):
            frame = extract_rgb_frame(self.plain_output, timestamp, probe=self.plain_probe)
            red, green, blue = mean_rgb(frame)
            self.assertLess(
                green,
                max(red, blue) - 50,
                f"unselected clip B leaked at output {timestamp:.3f}s; measured RGB={(red, green, blue)}",
            )

        self.assertAlmostEqual(
            media_duration_seconds(self.plain_probe),
            2 * CLIP_DURATION_SECONDS,
            delta=FRAME_AND_MUX_TOLERANCE_SECONDS,
        )

        early = {clip: self._band_measurement(clip, start=0.25) for clip in TONE_BY_CLIP}
        late = {clip: self._band_measurement(clip, start=1.55) for clip in TONE_BY_CLIP}
        early_levels = {clip: value.mean_volume_db for clip, value in early.items()}
        late_levels = {clip: value.mean_volume_db for clip, value in late.items()}
        self.assertGreater(
            early_levels["C"],
            max(early_levels["A"], early_levels["B"]) + 12,
            "expected clip C audio first at output 0.250..0.800s; "
            + "\n".join(value.describe() for value in early.values()),
        )
        self.assertGreater(
            late_levels["A"],
            max(late_levels["B"], late_levels["C"]) + 12,
            "expected clip A audio second at output 1.550..2.100s; "
            + "\n".join(value.describe() for value in late.values()),
        )

    def test_subtitles_follow_selected_clips_on_short_output_timeline(self) -> None:
        saved = load_project(self.subtitle_project)
        ass_path = Path(saved["render_settings"]["short_last_ass"])
        dialogue = "\n".join(
            line for line in ass_path.read_text(encoding="utf-8").splitlines() if line.startswith("Dialogue:")
        )
        self.assertIn("CLIP C", dialogue)
        self.assertIn("CLIP A", dialogue)
        self.assertNotIn("UNSELECTED B", dialogue)

        for timestamp, expected_clip in ((0.4, "C"), (1.9, "A")):
            plain = extract_rgb_frame(self.plain_output, timestamp, probe=self.plain_probe)
            subtitled = extract_rgb_frame(
                self.subtitle_output,
                timestamp,
                probe=self.subtitle_probe,
            )
            difference = compare_rgb_frames(plain, subtitled, region=SUBTITLE_REGION)
            assert_frame_difference_present(
                difference,
                context=f"subtitle for clip {expected_clip} at short output {timestamp:.3f}s",
            )

        plain = extract_rgb_frame(self.plain_output, 1.1, probe=self.plain_probe)
        subtitled = extract_rgb_frame(self.subtitle_output, 1.1, probe=self.subtitle_probe)
        difference = compare_rgb_frames(plain, subtitled, region=SUBTITLE_REGION)
        assert_frame_difference_absent(
            difference,
            context="selected subtitle gap before the C/A clip boundary",
        )

    def test_short_without_subtitles_keeps_final_media_contract(self) -> None:
        saved = load_project(self.plain_project)
        self.assertNotIn("short_last_ass", saved["render_settings"])
        stream = video_stream(self.plain_probe)
        self.assertEqual((stream["width"], stream["height"]), (OUTPUT_WIDTH, OUTPUT_HEIGHT))
        self.assertEqual(stream["r_frame_rate"], f"{FIXTURE_FPS}/1")
        self.assertEqual(stream["pix_fmt"], "yuv420p")
        self.assertEqual(stream["codec_name"], "h264")
        self.assertTrue(audio_streams(self.plain_probe))

    def test_source_time_basis_uses_original_source_ranges_in_final_media(self) -> None:
        # The normal-video timeline removes source 0.0..1.2s, so its output
        # time 2.4s points past source C. Only original-source interpretation
        # maps the short's 2.4s range to C at the output start.
        self._assert_expected_color(0.35, "C")
        source_c = self._band_measurement("C", start=0.25)
        source_a = self._band_measurement("A", start=0.25)
        self.assertGreater(
            source_c.mean_volume_db,
            source_a.mean_volume_db + 12,
            f"source-time clip C was not mapped to short output start:\n{source_c.describe()}\n{source_a.describe()}",
        )


if __name__ == "__main__":
    unittest.main()
