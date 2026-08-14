import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.silence_cut import (
    build_concat_filter,
    build_keep_ranges,
    cut_media_ranges,
    build_no_speech_plan,
    build_silence_cut_command,
    build_silencedetect_command,
    invert_ranges,
    merge_ranges,
    parse_silencedetect_output,
    retime_segments_for_keep_ranges,
    shift_ranges,
)


class SilenceCutTests(unittest.TestCase):
    def test_build_silencedetect_command_uses_requested_thresholds(self) -> None:
        command = build_silencedetect_command("input.mp4", noise="-30dB", duration=0.6)
        self.assertEqual(command[:2], ["ffmpeg", "-i"])
        self.assertIn("silencedetect=noise=-30dB:d=0.6", command)

    def test_parse_silencedetect_output_extracts_ranges(self) -> None:
        log_text = """
[silencedetect @ 0000] silence_start: 1.25
[silencedetect @ 0000] silence_end: 2.10 | silence_duration: 0.85
[silencedetect @ 0000] silence_start: 5.00
[silencedetect @ 0000] silence_end: 5.45 | silence_duration: 0.45
"""
        self.assertEqual(parse_silencedetect_output(log_text), [(1.25, 2.1), (5.0, 5.45)])

    def test_parse_silencedetect_output_closes_trailing_silence(self) -> None:
        log_text = "[silencedetect] silence_start: 8.5"
        self.assertEqual(parse_silencedetect_output(log_text, media_duration=10.0), [(8.5, 10.0)])

    def test_merge_ranges_coalesces_overlap(self) -> None:
        merged = merge_ranges([(2.0, 3.0), (2.8, 4.0), (5.0, 6.0)])
        self.assertEqual(merged, [(2.0, 4.0), (5.0, 6.0)])

    def test_invert_ranges_returns_complement(self) -> None:
        self.assertEqual(invert_ranges(10.0, [(1.0, 2.0), (4.0, 7.0)]), [(0.0, 1.0), (2.0, 4.0), (7.0, 10.0)])

    def test_shift_ranges_clips_to_video_duration(self) -> None:
        self.assertEqual(shift_ranges([(0.0, 1.0), (8.0, 12.0)], 1.0, 10.0), [(1.0, 2.0), (9.0, 10.0)])

    def test_build_no_speech_plan_unions_speakers_and_keeps_padding(self) -> None:
        cut_ranges, keep_ranges = build_no_speech_plan(
            video_duration=10.0,
            speaker_speech_ranges=[(0.0, 1.0), (3.0, 4.0), (3.5, 5.0)],
            offset_seconds=1.0,
            min_no_speech_seconds=1.2,
            padding=0.25,
        )
        self.assertEqual(cut_ranges, [(2.0, 4.0), (6.0, 10.0)])
        self.assertEqual(keep_ranges, [(0.0, 2.25), (3.75, 6.25)])

    def test_build_keep_ranges_inverts_silence_ranges_with_padding(self) -> None:
        keep_ranges = build_keep_ranges(
            duration=10.0,
            silence_ranges=[(1.0, 2.0), (4.0, 5.0)],
            padding=0.1,
            min_clip_duration=0.25,
        )
        self.assertEqual(keep_ranges, [(0.0, 1.1), (1.9, 4.1), (4.9, 10.0)])

    def test_build_keep_ranges_drops_fully_silent_video(self) -> None:
        keep_ranges = build_keep_ranges(
            duration=10.0,
            silence_ranges=[(0.0, 10.0)],
            padding=0.25,
            min_clip_duration=0.25,
        )
        self.assertEqual(keep_ranges, [])

    def test_build_keep_ranges_preserves_padding_next_to_speech_only(self) -> None:
        keep_ranges = build_keep_ranges(
            duration=10.0,
            silence_ranges=[(0.0, 4.0), (6.0, 10.0)],
            padding=0.25,
            min_clip_duration=0.25,
        )
        self.assertEqual(keep_ranges, [(3.75, 6.25)])

    def test_build_keep_ranges_drops_tiny_clips(self) -> None:
        keep_ranges = build_keep_ranges(
            duration=3.0,
            silence_ranges=[(0.05, 2.9)],
            padding=0.0,
            min_clip_duration=0.2,
        )
        self.assertEqual(keep_ranges, [])

    def test_build_concat_filter_joins_trimmed_segments(self) -> None:
        filter_text = build_concat_filter([(0.0, 1.2), (2.0, 3.5)])
        self.assertIn("trim=start=0.000:end=1.200", filter_text)
        self.assertIn("atrim=start=2.000:end=3.500", filter_text)
        self.assertIn("concat=n=2:v=1:a=1[v][a]", filter_text)

    def test_build_concat_filter_uses_explicit_audio_track(self) -> None:
        filter_text = build_concat_filter([(0.0, 1.2)], audio_track="0:a:3")
        self.assertIn("[0:a:3]atrim=start=0.000:end=1.200", filter_text)

    def test_build_concat_filter_applies_audio_filter_after_concat(self) -> None:
        filter_text = build_concat_filter([(0.0, 1.2)], audio_filter="loudnorm=I=-16:LRA=11:TP=-1.5")
        self.assertIn("concat=n=1:v=1:a=1[v][acat]", filter_text)
        self.assertIn("[acat]loudnorm=I=-16:LRA=11:TP=-1.5[a]", filter_text)

    def test_build_concat_filter_renders_retimed_subtitles_after_concat(self) -> None:
        filter_text = build_concat_filter(
            [(2.0, 3.0)],
            video_filter="ass='sample.ass'",
        )
        self.assertIn("concat=n=1:v=1:a=1[vcat][a]", filter_text)
        self.assertIn("[vcat]ass='sample.ass'[v]", filter_text)

    def test_retime_segments_for_keep_ranges_maps_to_output_timeline(self) -> None:
        segments = [
            {"start": 0.5, "end": 1.5, "text": "first", "layout_row": 0, "words": [{"start": 0.5, "end": 1.5}]},
            {"start": 3.0, "end": 4.0, "text": "second", "layout_row": 1},
        ]

        retimed = retime_segments_for_keep_ranges(segments, [(0.0, 1.0), (3.0, 4.0)])

        self.assertEqual([(item["start"], item["end"]) for item in retimed], [(0.5, 1.0), (1.0, 2.0)])
        self.assertNotIn("words", retimed[0])
        self.assertEqual([item["text"] for item in retimed], ["first", "second"])

    def test_build_silence_cut_command_maps_concat_outputs(self) -> None:
        command = build_silence_cut_command("input.mp4", "output.mp4", [(0.0, 1.0)])
        self.assertEqual(command[:4], ["ffmpeg", "-y", "-i", "input.mp4"])
        self.assertIn("[v]", command)
        self.assertIn("[a]", command)
        self.assertIn("output.mp4", command)

    def test_build_silence_cut_command_uses_faststart(self) -> None:
        command = build_silence_cut_command("input.mp4", "output.mp4", [(0.0, 1.0)])

        self.assertIn("-movflags", command)
        self.assertIn("+faststart", command)
        self.assertEqual(command[command.index("-movflags") + 1], "+faststart")

    def test_build_silence_cut_command_uses_nvenc_and_audio_filter(self) -> None:
        command = build_silence_cut_command(
            "input.mp4",
            "output.mp4",
            [(0.0, 1.0)],
            video_codec="h264_nvenc",
            audio_codec="aac",
            nvenc_preset="p5",
            audio_filter="loudnorm=I=-16:LRA=11:TP=-1.5",
        )
        self.assertIn("h264_nvenc", command)
        self.assertIn("p5", command)
        self.assertIn("-cq", command)
        self.assertEqual(command[command.index("-cq") + 1], "18")
        self.assertEqual(command[command.index("-profile:v") + 1], "high")
        self.assertIn("aac", command)
        self.assertIn("48000", command)
        self.assertIn("loudnorm=I=-16:LRA=11:TP=-1.5", " ".join(command))

    def test_build_silence_cut_command_accepts_audio_mix(self) -> None:
        command = build_silence_cut_command(
            "input.mp4",
            "output.mp4",
            [(0.0, 0.4), (0.6, 1.0)],
            audio_mix={
                "channels": [
                    {"kind": "video", "selector": "0:a:0", "enabled": True, "volume_percent": 100},
                    {"kind": "external", "path": "voice.flac", "enabled": True, "volume_percent": 75},
                ]
            },
            audio_offset_seconds=0.1,
        )

        self.assertIn("voice.flac", command)
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("adelay=100:all=1", graph)
        self.assertIn("amix=inputs=2", graph)
        self.assertIn("[mixed_audio]asplit=2[mixed_audio_0][mixed_audio_1]", graph)
        self.assertIn("[mixed_audio_0]atrim=start=0.000:end=0.400", graph)
        self.assertIn("[mixed_audio_1]atrim=start=0.600:end=1.000", graph)
        self.assertIn("48000", command)

    def test_cut_media_ranges_uses_short_filter_script_command(self) -> None:
        keep_ranges = [(float(index * 2), float(index * 2 + 1)) for index in range(333)]
        observed: dict[str, int] = {}

        def inspect_command(command: list[str], check: bool) -> None:
            script_index = command.index("-filter_complex_script") + 1
            script_path = Path(command[script_index])
            observed["filter_length"] = len(script_path.read_text(encoding="utf-8"))
            observed["command_length"] = len(" ".join(command))
            self.assertTrue(check)
            Path(command[-1]).write_bytes(b"output")

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "output.mp4"
            with mock.patch("src.silence_cut.subprocess.run", side_effect=inspect_command):
                cut_media_ranges("input.mp4", str(output_path), keep_ranges)
            self.assertFalse((Path(temp_dir) / "output.ffmpeg-filter.txt").exists())

        self.assertGreater(observed["filter_length"], 8191)
        self.assertLess(observed["command_length"], 1000)

    def test_cut_media_ranges_preserves_existing_output_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "output.mp4"
            output_path.write_bytes(b"previous output")

            with mock.patch(
                "src.silence_cut.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, ["ffmpeg"]),
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    cut_media_ranges("input.mp4", str(output_path), [(0.0, 1.0)])

            self.assertEqual(output_path.read_bytes(), b"previous output")
            self.assertEqual(
                [
                    path
                    for path in output_path.parent.iterdir()
                    if ".partial" in path.name
                ],
                [],
            )
    def test_cut_media_ranges_falls_back_from_nvenc_to_x264(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "output.mp4"
            calls: list[list[str]] = []

            def fake_run(command: list[str], **_kwargs: object) -> None:
                calls.append(command)
                codec = command[command.index("-c:v") + 1]
                if codec == "h264_nvenc":
                    raise subprocess.CalledProcessError(1, command)
                Path(command[-1]).write_bytes(b"x264 output")

            with mock.patch("src.burn_subs.subprocess.run", side_effect=fake_run):
                cut_media_ranges(
                    "input.mp4",
                    str(output_path),
                    [(0.0, 1.0)],
                    video_codec="h264_nvenc",
                )

            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1][calls[1].index("-c:v") + 1], "libx264")
            self.assertEqual(output_path.read_bytes(), b"x264 output")

    def test_windows_long_silence_cut_keeps_filter_script_off_command_line(self) -> None:
        keep_ranges = [(float(index * 2), float(index * 2 + 1)) for index in range(333)]
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "output.mp4"

            def inspect_command(command: list[str], check: bool) -> None:
                self.assertTrue(check)
                self.assertIn("-/filter_complex", command)
                self.assertLess(len(" ".join(command)), 1000)
                Path(command[-1]).write_bytes(b"output")

            with mock.patch("src.silence_cut.os.name", "nt"), mock.patch(
                "src.silence_cut.subprocess.run", side_effect=inspect_command
            ):
                cut_media_ranges("input.mp4", str(output_path), keep_ranges)

    def test_build_silence_cut_command_uses_yuv420p(self) -> None:
        command = build_silence_cut_command(
            "input.mp4",
            "output.mp4",
            [(0.0, 1.0)],
            video_codec="libx264",
        )

        self.assertIn("-pix_fmt", command)
        self.assertEqual(command[command.index("-pix_fmt") + 1], "yuv420p")

    def test_build_silence_cut_command_can_use_filter_script(self) -> None:
        command = build_silence_cut_command(
            "input.mp4",
            "output.mp4",
            [(0.0, 1.0)],
            filter_script_path="filters.txt",
        )
        self.assertIn(command[command.index("filters.txt") - 1], {"-filter_complex_script", "-/filter_complex"})
        self.assertIn("filters.txt", command)
        self.assertNotIn("trim=start=0.000:end=1.000", " ".join(command))


if __name__ == "__main__":
    unittest.main()
