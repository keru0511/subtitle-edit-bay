from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from src.subtitle_project import create_project, save_project
from src.subtitle_workflow import build_project_ass
from src.video_encoding import select_automatic_video_codec
from tests.media_test_utils import (
    FrameDifference,
    FrameRegion,
    MediaSegment,
    assert_frame_difference_absent,
    assert_frame_difference_present,
    assert_mp4_faststart,
    audio_streams,
    compare_rgb_frames,
    create_lavfi_av_fixture,
    extract_rgb_frame,
    mean_luma,
    mean_rgb,
    media_duration_seconds,
    probe_media,
    require_media_tools,
    run_media_command,
    video_stream,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_FPS = 30
SUBTITLE_START_SECONDS = 0.8
SUBTITLE_END_SECONDS = 2.2
MUX_TOLERANCE_SECONDS = 0.02
FRAME_AND_MUX_TOLERANCE_SECONDS = 1 / FIXTURE_FPS + MUX_TOLERANCE_SECONDS
SUBTITLE_REGION = FrameRegion(x=24, y=78, width=272, height=96)
LINE_COUNT_TEXT = "SEMANTIC LINE TEST"
MANUAL_BREAK_TEXT = "SEMANTIC LINE\nTEST"


class MediaCommandDiagnosticTests(unittest.TestCase):
    def test_failure_reports_command_output_and_fixture_context(self) -> None:
        command = [
            sys.executable,
            "-c",
            ("import sys; print('fixture stdout'); print('fixture stderr', file=sys.stderr); raise SystemExit(7)"),
        ]

        with self.assertRaises(AssertionError) as raised:
            run_media_command(command, context="fixture=diagnostic-test")

        message = str(raised.exception)
        for expected in (
            "exit code 7",
            "fixture=diagnostic-test",
            sys.executable,
            "fixture stdout",
            "fixture stderr",
        ):
            self.assertIn(expected, message)


@unittest.skipUnless(
    os.environ.get("RUN_FFMPEG_SMOKE") == "1",
    "set RUN_FFMPEG_SMOKE=1 to exercise semantic media E2E",
)
class MediaSemanticE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        require_media_tools()
        cls._temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._temporary.cleanup)
        cls.root = Path(cls._temporary.name)
        cls.fixture = create_lavfi_av_fixture(
            cls.root / "semantic fixture.mp4",
            [
                MediaSegment("before", 0.6, "0x102040", 440),
                MediaSegment("subtitle", 1.8, "0x202020", 660),
                MediaSegment("after", 0.6, "0x401020", 880),
            ],
            fps=FIXTURE_FPS,
        )
        cls.selected_codec = select_automatic_video_codec(nvenc_available=False)
        cls.control_ass = cls._build_ass(
            "control",
            text=None,
            subtitle_line_count="auto",
            max_width=80,
        )
        cls.one_line_ass = cls._build_ass(
            "one-line",
            text=LINE_COUNT_TEXT,
            subtitle_line_count="1",
            max_width=14,
        )
        cls.two_line_ass = cls._build_ass(
            "two-line",
            text=LINE_COUNT_TEXT,
            subtitle_line_count="2",
            max_width=14,
        )
        cls.manual_break_ass = cls._build_ass(
            "manual-break",
            text=MANUAL_BREAK_TEXT,
            subtitle_line_count="1",
            max_width=14,
        )
        cls.control_output = cls._render("control", cls.control_ass)
        cls.one_line_output = cls._render("one-line", cls.one_line_ass)
        cls.two_line_output = cls._render("two-line", cls.two_line_ass)
        cls.manual_break_output = cls._render("manual-break", cls.manual_break_ass)
        cls.output_probes = {
            cls.control_output: probe_media(cls.control_output),
            cls.one_line_output: probe_media(cls.one_line_output),
            cls.two_line_output: probe_media(cls.two_line_output),
            cls.manual_break_output: probe_media(cls.manual_break_output),
        }

    @classmethod
    def _build_ass(
        cls,
        name: str,
        *,
        text: str | None,
        subtitle_line_count: str,
        max_width: int,
    ) -> Path:
        segments = []
        if text is not None:
            segments.append(
                {
                    "start": SUBTITLE_START_SECONDS,
                    "end": SUBTITLE_END_SECONDS,
                    "text": text,
                    "speaker": "Oz",
                    "max_width": max_width,
                    "subtitle_line_count": subtitle_line_count,
                }
            )
        project = create_project(
            video_path=cls.fixture.path,
            output_dir=cls.root,
            segments=segments,
            subtitle_settings={"font_size": 110, "outline_thickness": 2},
            duration_seconds=cls.fixture.duration_seconds,
        )
        project_path = save_project(cls.root / f"{name}.subtitle-project.json", project)
        return build_project_ass(project_path, cls.root / f"{name}.ass")

    @classmethod
    def _render(cls, name: str, subtitle: Path) -> Path:
        output = cls.root / f"{name}.mp4"
        run_media_command(
            [
                sys.executable,
                "-m",
                "src.burn_subs",
                "--video",
                str(cls.fixture.path),
                "--subtitle",
                str(subtitle),
                "--output",
                str(output),
                "--video-codec",
                cls.selected_codec,
                "--audio-codec",
                "aac",
                "--x264-crf",
                "18",
                "--run",
            ],
            cwd=REPO_ROOT,
            context=(
                f"production subtitle render '{name}', codec={cls.selected_codec}, fixture={cls.fixture.describe()}"
            ),
        )
        if not output.is_file() or output.stat().st_size <= 0:
            raise AssertionError(f"Production render did not create output: {output}")
        return output

    @staticmethod
    def _dialogue_texts(path: Path) -> list[str]:
        return [
            line.split(",", 9)[9]
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("Dialogue:")
        ]

    def _subtitle_difference(self, output: Path, timestamp_seconds: float) -> FrameDifference:
        control = extract_rgb_frame(
            self.control_output,
            timestamp_seconds,
            probe=self.output_probes[self.control_output],
        )
        rendered = extract_rgb_frame(
            output,
            timestamp_seconds,
            probe=self.output_probes[output],
        )
        return compare_rgb_frames(control, rendered, region=SUBTITLE_REGION)

    def test_fixture_has_distinct_deterministic_time_bands(self) -> None:
        probe = probe_media(self.fixture.path)
        early_frame = extract_rgb_frame(self.fixture.path, 0.3, probe=probe)
        middle_frame = extract_rgb_frame(self.fixture.path, 1.5, probe=probe)
        late_frame = extract_rgb_frame(self.fixture.path, 2.7, probe=probe)
        early = mean_rgb(early_frame)
        middle = mean_rgb(middle_frame)
        late = mean_rgb(late_frame)

        self.assertGreater(early[2], early[0] + 25, (early, middle, late))
        self.assertLess(max(middle) - min(middle), 8, (early, middle, late))
        self.assertGreater(late[0], late[1] + 25, (early, middle, late))
        self.assertTrue(all(mean_luma(frame) > 10 for frame in (early_frame, middle_frame, late_frame)))

    def test_subtitle_pixels_follow_start_and_end_timing(self) -> None:
        sample_offset = FRAME_AND_MUX_TOLERANCE_SECONDS
        samples = {
            "before start": SUBTITLE_START_SECONDS - sample_offset,
            "after start": SUBTITLE_START_SECONDS + sample_offset,
            "middle": (SUBTITLE_START_SECONDS + SUBTITLE_END_SECONDS) / 2,
            "before end": SUBTITLE_END_SECONDS - sample_offset,
            "after end": SUBTITLE_END_SECONDS + sample_offset,
        }
        differences = {
            label: self._subtitle_difference(self.two_line_output, timestamp) for label, timestamp in samples.items()
        }

        assert_frame_difference_absent(differences["before start"], context="before subtitle start")
        assert_frame_difference_present(differences["after start"], context="after subtitle start")
        assert_frame_difference_present(differences["middle"], context="subtitle interval midpoint")
        assert_frame_difference_present(differences["before end"], context="before subtitle end")
        assert_frame_difference_absent(differences["after end"], context="after subtitle end")

    def test_line_count_override_changes_vertical_occupied_region(self) -> None:
        one_line_dialogues = self._dialogue_texts(self.one_line_ass)
        two_line_dialogues = self._dialogue_texts(self.two_line_ass)
        self.assertEqual(len(one_line_dialogues), 1)
        self.assertEqual(len(two_line_dialogues), 1)
        self.assertNotIn(r"\N", one_line_dialogues[0])
        self.assertEqual(two_line_dialogues[0].count(r"\N"), 1)

        midpoint = (SUBTITLE_START_SECONDS + SUBTITLE_END_SECONDS) / 2
        one_line_difference = self._subtitle_difference(self.one_line_output, midpoint)
        two_line_difference = self._subtitle_difference(self.two_line_output, midpoint)
        assert_frame_difference_present(one_line_difference, context="one-line subtitle")
        assert_frame_difference_present(two_line_difference, context="two-line subtitle")
        self.assertGreaterEqual(
            two_line_difference.occupied_height,
            one_line_difference.occupied_height + 8,
            f"one={one_line_difference.describe()}\ntwo={two_line_difference.describe()}",
        )

    def test_manual_line_break_is_preserved_in_ass_and_rendered_pixels(self) -> None:
        manual_dialogues = self._dialogue_texts(self.manual_break_ass)
        self.assertEqual(len(manual_dialogues), 1)
        self.assertIn(r"SEMANTIC LINE\NTEST", manual_dialogues[0])

        midpoint = (SUBTITLE_START_SECONDS + SUBTITLE_END_SECONDS) / 2
        one_line_difference = self._subtitle_difference(self.one_line_output, midpoint)
        manual_difference = self._subtitle_difference(self.manual_break_output, midpoint)
        assert_frame_difference_present(one_line_difference, context="manual line break one-line control")
        assert_frame_difference_present(manual_difference, context="manual line break subtitle")
        self.assertGreaterEqual(
            manual_difference.occupied_height,
            one_line_difference.occupied_height + 8,
            f"one={one_line_difference.describe()}\nmanual={manual_difference.describe()}",
        )

    def test_cpu_fallback_produces_compatible_h264_with_audio(self) -> None:
        self.assertEqual(self.selected_codec, "libx264")
        probe = self.output_probes[self.control_output]
        stream = video_stream(probe)
        audio = audio_streams(probe)

        self.assertEqual(stream.get("codec_name"), "h264", probe)
        self.assertEqual(stream.get("pix_fmt"), "yuv420p", probe)
        self.assertGreaterEqual(len(audio), 1, probe)
        self.assertTrue(all(item.get("codec_name") == "aac" for item in audio), probe)
        self.assertAlmostEqual(
            media_duration_seconds(probe),
            self.fixture.duration_seconds,
            delta=FRAME_AND_MUX_TOLERANCE_SECONDS,
        )
        assert_mp4_faststart(self.control_output)


if __name__ == "__main__":
    unittest.main()
