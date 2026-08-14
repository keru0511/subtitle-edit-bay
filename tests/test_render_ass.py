import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.assemble_video import build_concat_command, build_loudnorm_filter, build_normalize_command, optional_clip, write_concat_manifest
from src.batch import derive_export_paths, derive_merged_export_paths, iter_video_files
from src.burn_subs import build_ass_filter, build_ffmpeg_command, run_ffmpeg_burn, temporary_ass_path
from src.merge_transcripts import assign_bottom_rows, merge_transcripts, speaker_for_track, split_segment
from src.pipeline import build_ass_from_transcript, derive_pipeline_paths, normalize_diarize_tracks, run_media_to_ass_many
from src.color_config import load_speaker_color_map
from src.render_ass import (
    format_ass_time,
    normalize_text,
    parse_track_color_args,
    render_ass,
    sanitize_ass_text,
    style_name_for_speaker,
)
from unittest import mock

from src.subtitle_packer import (
    break_candidates,
    budoux_boundaries,
    pack_segment_pages,
    pack_segments,
    score_break,
    score_truncated_break,
    split_into_atomic_units,
    text_width,
)
from src.video_encoding import build_video_encoding_args
from src.transcribe import (
    build_extract_audio_command,
    build_whisperx_command,
    expected_log_path,
    expected_transcript_path,
    run_command_with_utf8_log,
    validate_hf_token,
)
from src.youtube_text import derive_youtube_text_paths, write_youtube_texts


class RenderAssTests(unittest.TestCase):
    def test_temporary_ass_path_cleans_apostrophe_copy_after_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            subtitle = Path(temp_dir) / "O'Brien" / "caption.ass"
            subtitle.parent.mkdir()
            subtitle.write_text("[Script Info]\n", encoding="utf-8")

            with temporary_ass_path(str(subtitle)) as safe_path:
                self.assertNotEqual(Path(safe_path), subtitle)
                self.assertTrue(Path(safe_path).exists())
                self.assertEqual(Path(safe_path).read_text(encoding="utf-8"), "[Script Info]\n")

            self.assertFalse(Path(safe_path).exists())

    def test_boundary_and_width_helpers_reuse_bounded_caches(self) -> None:
        budoux_boundaries.cache_clear()
        text_width.cache_clear()
        with mock.patch("src.subtitle_packer.parse_budoux_chunks", return_value=["abc", "def"]) as parse:
            self.assertEqual(budoux_boundaries("abcdef"), {3})
            self.assertEqual(budoux_boundaries("abcdef"), {3})
        budoux_boundaries.cache_clear()
        self.assertEqual(parse.call_count, 1)

        self.assertEqual(text_width("subtitle"), 8)
        self.assertEqual(text_width("subtitle"), 8)
        self.assertGreaterEqual(text_width.cache_info().hits, 1)

    def test_truncated_break_scoring_matches_standard_scoring(self) -> None:
        text = "ABCDEFGHIJKLMN"
        self.assertEqual(
            score_truncated_break(text, 7, 8, display_duration=0.7),
            score_break(text, 7, 8, display_duration=0.7),
        )

    def test_format_ass_time_uses_centiseconds(self) -> None:
        self.assertEqual(format_ass_time(65.43), "0:01:05.43")

    def test_normalize_text_wraps_and_truncates_for_visibility(self) -> None:
        text = r"\u3053\u308c\u306f\u304b\u306a\u308a\u9577\u3044\u5b57\u5e55\u30c6\u30ad\u30b9\u30c8\u3067\u753b\u9762\u306e\u5916\u306b\u306f\u307f\u51fa\u3055\u306a\u3044\u3088\u3046\u306b\u81ea\u52d5\u3067\u6298\u308a\u8fd4\u3057\u3066\u307b\u3057\u3044\u3067\u3059".encode("ascii").decode("unicode_escape")
        wrapped = normalize_text(text, max_width=12, max_lines=2)
        self.assertIn(r"\N", wrapped)
        self.assertIn(r"\u2026".encode("ascii").decode("unicode_escape"), wrapped)

    def test_normalize_text_preserves_manual_line_breaks_over_line_count(self) -> None:
        self.assertEqual(
            normalize_text("first line\nsecond line", max_width=4, max_lines=1),
            r"first line\Nsecond line",
        )
        self.assertEqual(
            normalize_text("first line\r\nsecond line", max_width=4, max_lines=2),
            r"first line\Nsecond line",
        )

    def test_normalize_text_uses_budoux_boundary_for_two_lines(self) -> None:
        class FakeParser:
            def parse(self, text: str) -> list[str]:
                return [
                    r"\u3053\u3053\u3067\u6575\u304c\u6765\u308b\u304b\u3089".encode("ascii").decode("unicode_escape"),
                    r"\u4e00\u56de\u5f15\u3044\u305f\u307b\u3046\u304c\u3044\u3044\u304b\u3082\u3057\u308c\u306a\u3044".encode("ascii").decode("unicode_escape"),
                ]

        text = r"\u3053\u3053\u3067\u6575\u304c\u6765\u308b\u304b\u3089\u4e00\u56de\u5f15\u3044\u305f\u307b\u3046\u304c\u3044\u3044\u304b\u3082\u3057\u308c\u306a\u3044".encode("ascii").decode("unicode_escape")
        with mock.patch("src.subtitle_packer.create_budoux_parser", return_value=FakeParser()):
            wrapped = normalize_text(text, max_width=24, max_lines=2)

        self.assertEqual(
            wrapped,
            r"\u3053\u3053\u3067\u6575\u304c\u6765\u308b\u304b\u3089".encode("ascii").decode("unicode_escape") +
            r"\N" +
            r"\u4e00\u56de\u5f15\u3044\u305f\u307b\u3046\u304c\u3044\u3044\u304b\u3082\u3057\u308c\u306a\u3044".encode("ascii").decode("unicode_escape"),
        )

    def test_normalize_text_avoids_particle_at_line_start(self) -> None:
        text = r"\u305d\u308c\u306f\u4eca\u3084\u308b\u306e\u306f\u3061\u3087\u3063\u3068\u5371\u306a\u3044\u304b\u3082\u3057\u308c\u306a\u3044".encode("ascii").decode("unicode_escape")
        wrapped = normalize_text(text, max_width=18, max_lines=2)
        self.assertNotIn(r"\N" + r"\u306f".encode("ascii").decode("unicode_escape"), wrapped)
        self.assertNotIn(r"\N" + r"\u304c".encode("ascii").decode("unicode_escape"), wrapped)
        self.assertNotIn(r"\N" + r"\u3092".encode("ascii").decode("unicode_escape"), wrapped)

    def test_normalize_text_avoids_small_tsu_at_line_start(self) -> None:
        text = r"\u305d\u308c\u306f\u4eca\u3084\u308b\u306e\u306f\u3061\u3087\u3063\u3068\u5371\u306a\u3044\u304b\u3082\u3057\u308c\u306a\u3044".encode("ascii").decode("unicode_escape")
        wrapped = normalize_text(text, max_width=18, max_lines=2)
        self.assertNotIn(r"\N" + r"\u3063".encode("ascii").decode("unicode_escape"), wrapped)

    def test_normalize_text_prefers_balanced_break_for_short_duration(self) -> None:
        text = "ABCDEFGHIJKLMN"
        with mock.patch("src.subtitle_packer.break_candidates", return_value=[6, 7]):
            with mock.patch("src.subtitle_packer.candidate_kind_bonus", side_effect=lambda text, idx: -50 if idx == 6 else 0):
                long_wrapped = normalize_text(text, max_width=8, max_lines=2, display_duration=3.0)
                short_wrapped = normalize_text(text, max_width=8, max_lines=2, display_duration=0.4)

        self.assertEqual(long_wrapped, r"ABCDEF\NGHIJKLMN")
        self.assertEqual(short_wrapped, r"ABCDEFG\NHIJKLMN")

    def test_normalize_text_does_not_split_ascii_word(self) -> None:
        text = r"\u3053\u308c\u306fOBS\u3092\u4ecb\u3055\u305a\u306b\u3053\u306eDiscord\u4e0a\u3067\u3053\u3044\u3064\u304c\u9332\u97f3\u3057\u3066\u304f\u308c\u308b\u3063\u3066\u3053\u3068?".encode("ascii").decode("unicode_escape")
        wrapped = normalize_text(text, max_width=24, max_lines=2)
        self.assertIn("Discord", wrapped)
        self.assertNotIn(r"D\Niscord", wrapped)

    def test_normalize_text_keeps_punctuation_with_same_line(self) -> None:
        text = r"\u3086\u304d\u3068\u3053\u308c\u3069\u3046\u306a\u3063\u3068\u3093\u306e?".encode("ascii").decode("unicode_escape")
        wrapped = normalize_text(text, max_width=24, max_lines=2)
        self.assertEqual(wrapped, text)

    def test_normalize_text_truncation_rebalances_without_tiny_line(self) -> None:
        text = r"\u3053\u308c\u306fOBS\u3092\u4ecb\u3055\u305a\u306b\u3053\u306eDiscord\u4e0a\u3067\u3053\u3044\u3064\u304c\u9332\u97f3\u3057\u3066\u304f\u308c\u308b\u3063\u3066\u3053\u3068?".encode("ascii").decode("unicode_escape")
        wrapped = normalize_text(text, max_width=24, max_lines=2)
        left, right = wrapped.split("\\N", 1)
        self.assertGreater(len(left), 3)
        self.assertTrue(right.endswith(r"\u2026".encode("ascii").decode("unicode_escape")))

    def test_render_ass_preserves_edited_long_text_without_ellipsis(self) -> None:
        ass = render_ass(
            {
                "segments": [
                    {
                        "start": 0.0,
                        "end": 3.0,
                        "speaker": "Oz",
                        "text": "alpha beta gamma delta epsilon zeta eta theta",
                        "layout_packed": True,
                        "manual_text": True,
                        "max_width": 8,
                    }
                ]
            }
        )

        dialogue_lines = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
        self.assertGreaterEqual(len(dialogue_lines), 1)
        self.assertTrue(all("…" not in line for line in dialogue_lines))
        self.assertTrue(any("theta" in line for line in dialogue_lines))

    def test_pack_segment_pages_split_long_duration_segment(self) -> None:
        text = r"\u306f\u3058\u3081\u308b\u3051\u3069\u79fb\u52d5\u3057\u3066\u3067\u3082\u56de\u5fa9\u3057\u3066\u3067\u3082\u96a0\u308c\u308b".encode("ascii").decode("unicode_escape")
        pages = pack_segment_pages({"start": 0.0, "end": 5.0, "speaker": "Oz", "text": text, "max_width": 18})

        self.assertGreaterEqual(len(pages), 2)
        self.assertLess(float(pages[0]["end"]) - float(pages[0]["start"]), 5.0)

    def test_pack_segments_uses_page_split_before_rendering(self) -> None:
        data = {
            "segments": [
                {"start": 0.0, "end": 4.0, "speaker": "Oz", "text": "ignored"}
            ]
        }

        with mock.patch("src.subtitle_packer.pack_segment_pages", return_value=[
            {"start": 0.0, "end": 1.5, "speaker": "Oz", "text": "first page", "max_width": 12},
            {"start": 1.5, "end": 3.0, "speaker": "Oz", "text": "second page", "max_width": 12},
        ]):
            events = pack_segments(data)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].text, "first page")
        self.assertEqual(events[1].text, "second page")

    def test_pack_segments_does_not_repack_layout_packed_segments(self) -> None:
        data = {
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "speaker": "Oz",
                    "text": "already packed",
                    "layout_packed": True,
                }
            ]
        }

        with mock.patch("src.subtitle_packer.pack_segment_pages") as pack_pages:
            events = pack_segments(data)

        pack_pages.assert_not_called()
        self.assertEqual([event.text for event in events], ["already packed"])

    def test_pack_segments_normalizes_text_before_rendering(self) -> None:
        text = r"\u3053\u3053\u304b\u3089\u9006\u3055\u3093\u3068\u60aa\u3044\u3063\u3066\u8a00\u3046\u306e\u304b\u306a\u3061\u3087\u3063\u3068\u9577\u3081\u306e\u5b57\u5e55\u3067\u3059".encode("ascii").decode("unicode_escape")
        data = {
            "segments": [
                {"start": 0.0, "end": 1.0, "speaker": "Oz", "text": text, "max_width": 18}
            ]
        }

        with mock.patch("src.subtitle_packer.pack_segment_pages", return_value=[
            {"start": 0.0, "end": 1.0, "speaker": "Oz", "text": text, "max_width": 18}
        ]):
            events = pack_segments(data)

        self.assertEqual(len(events), 1)
        self.assertIn(r"\N", events[0].text)
        self.assertEqual(events[0].metadata["source_text"], text)

    def test_split_into_atomic_units_uses_budoux_chunks(self) -> None:
        class FakeParser:
            def parse(self, text: str) -> list[str]:
                return ["ab", "cd", "ef"]

        with mock.patch("src.subtitle_packer.create_budoux_parser", return_value=FakeParser()):
            units = split_into_atomic_units("abcdef")

        self.assertEqual(units, ["ab", "cd", "ef"])

    def test_break_candidates_include_budoux_boundaries(self) -> None:
        class FakeParser:
            def parse(self, text: str) -> list[str]:
                return ["ab", "cd", "ef"]

        with mock.patch("src.subtitle_packer.create_budoux_parser", return_value=FakeParser()):
            candidates = break_candidates("abcdef", 24)

        self.assertIn(2, candidates)
        self.assertIn(4, candidates)

    def test_parse_track_color_args_parses_mapping(self) -> None:
        mapping = parse_track_color_args(["0:a:1=#FFFFFF", "0:a:3=#A8FFF6"])
        self.assertEqual(mapping["0:a:1"], "#FFFFFF")
        self.assertEqual(mapping["0:a:3"], "#A8FFF6")

    def test_render_ass_can_override_color_per_track(self) -> None:
        data = {
            "segments": [
                {"start": 0.0, "end": 1.0, "speaker": "Oz", "text": "hello", "layout_row": 0, "source_track": "0:a:1"}
            ]
        }

        output = render_ass(data, track_color_map={"0:a:1": "#112233"})

        self.assertIn("Style: Track_0_a_1", output)
        self.assertIn("Style: Track_0_a_1,Arial,50,&H00332211,&H0000FFFF,&H00000000", output)
        self.assertIn("Dialogue: 0,0:00:00.00,0:00:01.00,Track_0_a_1,Oz,0,0,34,,hello", output)

    def test_render_ass_can_override_color_per_speaker_from_config(self) -> None:
        data = {
            "segments": [
                {"start": 0.0, "end": 1.0, "speaker": "C", "text": "hello", "layout_row": 0, "source_track": "craig:speaker-d", "source_speaker": "speaker-d"}
            ]
        }
        speaker_style = style_name_for_speaker("speaker-d")

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "speaker_colors.json"
            config_path.write_text(
                json.dumps(
                    {
                        "speakers": {
                            "speaker-d": {"color": "#2244FF", "aliases": ["guest-d"]}
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output = render_ass(data, speaker_color_map=load_speaker_color_map(config_path))

        self.assertIn(f"Style: {speaker_style}", output)
        self.assertIn(f"Style: {speaker_style},Arial,50,&H00FF4422,&H0000FFFF,&H00000000", output)
        self.assertIn(
            f"Dialogue: 0,0:00:00.00,0:00:01.00,{speaker_style},C,0,0,34,,hello",
            output,
        )

    def test_render_ass_prefers_file_name_color_mapping(self) -> None:
        data = {
            "segments": [
                {"start": 0.0, "end": 1.0, "speaker": "Oz", "text": "hello", "layout_row": 0, "source_file": "1-speaker-a.aac", "source_speaker": "speaker-a"}
            ]
        }
        file_style = style_name_for_speaker("1-speaker-a.aac")

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "speaker_colors.json"
            config_path.write_text(
                json.dumps(
                    {
                        "files": {
                            "1-speaker-a.aac": {"color": "#123456"}
                        },
                        "speakers": {
                            "speaker-a": {"color": "#abcdef"}
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output = render_ass(data, speaker_color_map=load_speaker_color_map(config_path))

        self.assertIn(f"Style: {file_style}", output)
        self.assertIn(f"Style: {file_style},Arial,50,&H00563412,&H0000FFFF,&H00000000", output)
        self.assertIn(
            f"Dialogue: 0,0:00:00.00,0:00:01.00,{file_style},Oz,0,0,34,,hello",
            output,
        )

    def test_style_name_for_speaker_uniquifies_similar_names(self) -> None:
        self.assertEqual(style_name_for_speaker("speaker-d"), style_name_for_speaker("speaker-d"))
        self.assertNotEqual(style_name_for_speaker("speaker-d"), style_name_for_speaker("speaker.d"))
        self.assertNotEqual(style_name_for_speaker("speaker d"), style_name_for_speaker("speaker.d"))
        self.assertNotEqual(style_name_for_speaker("山田.wav"), style_name_for_speaker("佐藤.wav"))

    def test_style_name_for_speaker_preserves_simple_ascii_names(self) -> None:
        self.assertEqual(style_name_for_speaker("  speaker-d "), style_name_for_speaker("speaker-d"))

    def test_render_ass_sanitizes_speaker_name_for_dialogue_actor(self) -> None:
        output = render_ass(
            {
                "segments": [
                    {
                        "start": 0.0,
                        "end": 1.0,
                        "speaker": "Alice,Bob",
                        "text": "hello",
                    }
                ]
            }
        )
        dialogue = next(line for line in output.splitlines() if line.startswith("Dialogue:"))
        self.assertIn(",Alice_Bob,", dialogue)
        self.assertNotIn(",Alice,Bob,", dialogue)

    def test_sanitize_ass_text_replaces_dangerous_chars(self) -> None:
        self.assertEqual(sanitize_ass_text("A{lice}\\,Bob"), "A_lice___Bob")

    def test_render_ass_applies_base_size_and_per_caption_volume_scale(self) -> None:
        data = {
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "speaker": "Oz",
                    "text": "loud",
                    "layout_row": 0,
                    "layout_packed": True,
                    "subtitle_font_scale": 1.2,
                },
                {
                    "start": 1.0,
                    "end": 2.0,
                    "speaker": "Guest",
                    "text": "also loud",
                    "layout_row": 1,
                    "layout_packed": True,
                    "subtitle_font_scale": 1.2,
                }
            ]
        }

        output = render_ass(data, subtitle_font_size=60)

        self.assertIn("Style: Oz,Arial,60", output)
        self.assertIn("Style: Guest,Arial,60", output)
        self.assertIn("Style: ShoutGuest,Arial,60", output)
        self.assertIn(r",,{\fs72}loud", output)
        self.assertIn(r",0,0,259,,{\fs72}also loud", output)

    def test_render_ass_applies_global_outline_color_and_thickness_to_all_styles(self) -> None:
        data = {
            "segments": [{
                "start": 0.0,
                "end": 1.0,
                "speaker": "custom",
                "source_track": "craig:custom",
                "text": "hello",
            }]
        }

        output = render_ass(
            data,
            track_color_map={"craig:custom": "#FFFFFF"},
            subtitle_outline_color="#123456",
            subtitle_outline_thickness=7,
        )

        style_lines = [line for line in output.splitlines() if line.startswith("Style: ")]
        self.assertGreater(len(style_lines), 12)
        self.assertTrue(all(line.split(",")[5] == "&H00563412" for line in style_lines))
        self.assertTrue(all(line.split(",")[16] == "7" for line in style_lines))

    def test_render_ass_applies_font_family_per_caption(self) -> None:
        data = {
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "speaker": "Oz",
                    "text": "hello",
                    "layout_packed": True,
                    "subtitle_font_scale": 1.2,
                    "subtitle_font_family": r"Yu{\} Mincho",
                }
            ]
        }

        output = render_ass(data)

        self.assertIn(r",,{\fnYu Mincho\fs60}hello", output)

    def test_render_ass_row_margin_matches_layout_scale_multipliers(self) -> None:
        for base_font_size, expected_step in [(50, 156), (100, 312), (200, 624), (450, 596)]:
            with self.subTest(base_font_size=base_font_size):
                data = {
                    "segments": [
                        {
                            "start": 0.0,
                            "end": 1.0,
                            "speaker": "Oz",
                            "text": "first",
                            "layout_packed": True,
                            "layout_row": 0,
                        },
                        {
                            "start": 0.0,
                            "end": 1.0,
                            "speaker": "Guest",
                            "text": "second",
                            "layout_packed": True,
                            "layout_row": 1,
                        },
                    ]
                }
                output = render_ass(data, subtitle_font_size=base_font_size)
                rows = [
                    [part.strip() for part in line.split(",", 8) if part]
                    for line in output.splitlines()
                    if line.startswith("Dialogue:")
                ]
                margins = {
                    int(item[0].replace("Dialogue: ", "")): int(item[7])
                    for item in rows
                }
                self.assertEqual(margins[0], 34)
                self.assertEqual(margins[1], 34 + expected_step)

    def test_render_ass_keeps_large_font_rows_inside_playres_height(self) -> None:
        output = render_ass(
            {
                "segments": [
                    {"start": 0.0, "end": 1.0, "speaker": "Oz", "text": "first", "layout_row": 0},
                    {"start": 0.0, "end": 1.0, "speaker": "Guest", "text": "second", "layout_row": 1},
                ]
            },
            height=1080,
            subtitle_font_size=450,
        )

        dialogue_lines = [line for line in output.splitlines() if line.startswith("Dialogue:")]
        margins = [int(line.split(",", 8)[7]) for line in dialogue_lines]
        self.assertEqual(margins, [34, 630])
        self.assertLessEqual(max(margins), 1080)

    def test_render_ass_escapes_ass_reserved_characters(self) -> None:
        data = {
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "speaker": "Oz",
                    "text": r"a{b}c\d",
                    "layout_row": 0,
                }
            ]
        }

        output = render_ass(data)

        self.assertIn(r",,a\{b\}c\\d", output)

    @unittest.skipUnless(os.environ.get("RUN_FFMPEG_SMOKE") == "1", "set RUN_FFMPEG_SMOKE=1 to exercise FFmpeg/libass")
    def test_ffmpeg_libass_renders_reserved_characters(self) -> None:
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg is required for ASS rendering smoke tests")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "input.mp4"
            subtitle = root / "reserved.ass"
            output = root / "output.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:d=0.4:r=2",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "0.4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            subtitle.write_text(
                render_ass({
                    "segments": [{
                        "start": 0.0,
                        "end": 0.4,
                        "speaker": "Smoke",
                        "text": r"C:\temp\video {take}\N next",
                    }]
                }),
                encoding="utf-8",
            )
            run_ffmpeg_burn(str(video), str(subtitle), str(output), audio_codec="aac")
            self.assertGreater(output.stat().st_size, 0)

    def test_pack_segments_preserves_source_speaker_metadata(self) -> None:
        data = {
            "segments": [
                {"start": 0.0, "end": 1.0, "speaker": "Oz", "text": "hello", "layout_row": 0, "source_speaker": "speaker-a"}
            ]
        }

        events = pack_segments(data)

        self.assertEqual(events[0].metadata["source_speaker"], "speaker-a")
        self.assertEqual(events[0].metadata["source_file"], "")

    def test_render_ass_outputs_bottom_stack_styles_and_margins(self) -> None:
        data = {
            "segments": [
                {"start": 0.0, "end": 1.0, "speaker": "Oz", "text": "hello", "layout_row": 0},
                {"start": 1.0, "end": 2.0, "speaker": "Guest", "text": "tsukkomi", "layout_row": 1},
                {"start": 2.0, "end": 3.0, "speaker": "Guest", "text": "wow", "emphasis": "shout", "layout_row": 2},
            ]
        }

        output = render_ass(data)

        self.assertIn("Style: Guest", output)
        self.assertIn("Dialogue: 0,0:00:00.00,0:00:01.00,Oz,Oz,0,0,34,,hello", output)
        self.assertIn("Dialogue: 1,0:00:01.00,0:00:02.00,Guest,Guest,0,0,190,,tsukkomi", output)
        self.assertIn("Dialogue: 2,0:00:02.00,0:00:03.00,ShoutGuest,Guest,0,0,346,,wow", output)

    def test_build_ffmpeg_command_uses_ass_filter(self) -> None:
        command = build_ffmpeg_command("input.mp4", "out\\sample.ass", "out/final.mp4")
        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("ass='out/sample.ass'", command)
        self.assertEqual(command[command.index("-map") + 1], "0:v:0")
        self.assertIn("0:a:0", command)
        self.assertIn("out/final.mp4", command)

    def test_build_ffmpeg_command_uses_yuv420p(self) -> None:
        command = build_ffmpeg_command(
            "input.mp4",
            "out/sample.ass",
            "out/final.mp4",
            video_codec="libx264",
        )

        self.assertIn("-pix_fmt", command)
        self.assertEqual(command[command.index("-pix_fmt") + 1], "yuv420p")

    def test_build_ffmpeg_command_uses_faststart(self) -> None:
        command = build_ffmpeg_command("input.mp4", "out/sample.ass", "out/final.mp4")

        self.assertIn("-movflags", command)
        self.assertIn("+faststart", command)
        self.assertEqual(command[command.index("-movflags") + 1], "+faststart")

    def test_build_ass_filter_escapes_windows_path(self) -> None:
        self.assertEqual(build_ass_filter(r"C:\work\sample.ass"), r"ass='C\:/work/sample.ass'")

    def test_run_ffmpeg_burn_uses_temporary_copy_for_apostrophe_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            subtitle = root / "O'Brien" / "caption.ass"
            subtitle.parent.mkdir(parents=True, exist_ok=True)
            subtitle.write_text("dummy", encoding="utf-8")
            output = root / "output.mp4"
            video.write_bytes(b"")

            calls: list[list[str]] = []

            def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                calls.append(command)
                Path(command[-1]).write_bytes(b"ok")
    def test_run_ffmpeg_burn_preserves_existing_output_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            subtitle = root / "caption.ass"
            subtitle.write_text("dummy", encoding="utf-8")
            output = root / "output.mp4"
            output.write_bytes(b"previous output")
            video.write_bytes(b"")

            def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                Path(command[-1]).write_bytes(b"")
                raise subprocess.CalledProcessError(1, command)

            with mock.patch("src.burn_subs.subprocess.run", side_effect=fake_run):
                with self.assertRaises(subprocess.CalledProcessError):
                    run_ffmpeg_burn(str(video), str(subtitle), str(output))

            self.assertEqual(output.read_bytes(), b"previous output")
            self.assertEqual(
                [path.name for path in output.parent.iterdir() if path.name.startswith(f".{output.stem}.") and ".partial" in path.name],
                [],
            )

    def test_run_ffmpeg_burn_replaces_output_only_when_run_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            subtitle = root / "caption.ass"
            subtitle.write_text("dummy", encoding="utf-8")
            output = root / "output.mp4"
            output.write_bytes(b"previous output")
            video.write_bytes(b"")

            def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                Path(command[-1]).write_bytes(b"new output")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with mock.patch("src.burn_subs.subprocess.run", side_effect=fake_run):
                result = run_ffmpeg_burn(str(video), str(subtitle), str(output))

            self.assertEqual(result, output)
            self.assertEqual(output.read_bytes(), b"new output")

    def test_run_ffmpeg_burn_falls_back_from_nvenc_to_x264(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            subtitle = root / "caption.ass"
            output = root / "output.mp4"
            video.write_bytes(b"video")
            subtitle.write_text("dummy", encoding="utf-8")
            calls: list[list[str]] = []

            def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                calls.append(command)
                codec = command[command.index("-c:v") + 1]
                if codec == "h264_nvenc":
                    raise subprocess.CalledProcessError(1, command)
                Path(command[-1]).write_bytes(b"x264 output")
                return subprocess.CompletedProcess(command, 0)

            with mock.patch("src.burn_subs.subprocess.run", side_effect=fake_run):
                run_ffmpeg_burn(
                    str(video),
                    str(subtitle),
                    str(output),
                    video_codec="h264_nvenc",
                    audio_codec="aac",
                )

            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0][calls[0].index("-c:v") + 1], "h264_nvenc")
            self.assertEqual(calls[1][calls[1].index("-c:v") + 1], "libx264")
            self.assertEqual(output.read_bytes(), b"x264 output")

    def test_build_ffmpeg_command_uses_high_quality_nvenc(self) -> None:
        command = build_ffmpeg_command(
            "input.mp4",
            "out/sample.ass",
            "out/final.mp4",
            video_codec="h264_nvenc",
            nvenc_cq=18,
        )
        self.assertIn("-cq", command)
        self.assertEqual(command[command.index("-cq") + 1], "18")
        self.assertEqual(command[command.index("-profile:v") + 1], "high")
        self.assertIn("-spatial-aq", command)
        self.assertIn("-temporal-aq", command)

    def test_build_video_encoding_args_rejects_invalid_quality(self) -> None:
        with self.assertRaises(ValueError):
            build_video_encoding_args("h264_nvenc", nvenc_cq=52)

    def test_build_normalize_command_uses_loudnorm(self) -> None:
        command = build_normalize_command("op.mp4", "out/op.normalized.mp4", 1920, 1080, audio_normalize=True)
        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("loudnorm=I=-16:LRA=11:TP=-1.5", command[command.index("-af") + 1])
        self.assertIn("fps=60", command[command.index("-vf") + 1])
        self.assertIn("0:v:0", command)
        self.assertIn("0:a:0", command)
        self.assertIn("out/op.normalized.mp4", command)

    def test_build_normalize_command_adds_silent_audio_when_missing(self) -> None:
        command = build_normalize_command(
            "silent-op.mp4",
            "out/op.normalized.mp4",
            1920,
            1080,
            has_audio=False,
        )
        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=48000", command)
        self.assertIn("1:a:0", command)
        self.assertIn("-shortest", command)

    def test_build_loudnorm_filter_accepts_custom_targets(self) -> None:
        self.assertEqual(build_loudnorm_filter(-14.0, 9.0, -1.0), "loudnorm=I=-14:LRA=9:TP=-1")

    def test_build_ffmpeg_command_applies_audio_filter(self) -> None:
        command = build_ffmpeg_command(
            "input.mp4",
            "out/sample.ass",
            "out/final.mp4",
            audio_codec="aac",
            audio_filter="loudnorm=I=-16:LRA=11:TP=-1.5",
        )
        self.assertIn("-af", command)
        self.assertIn("loudnorm=I=-16:LRA=11:TP=-1.5", command)
        self.assertIn("aac", command)
        self.assertIn("48000", command)

    def test_build_ffmpeg_command_maps_custom_audio_mix(self) -> None:
        command = build_ffmpeg_command(
            "input.mp4",
            "out/sample.ass",
            "out/final.mp4",
            audio_mix={
                "channels": [
                    {"kind": "video", "selector": "0:a:0", "enabled": True, "volume_percent": 50},
                    {"kind": "external", "path": "voice.flac", "enabled": True, "volume_percent": 100},
                ]
            },
            audio_offset_seconds=0.2,
        )

        self.assertIn("voice.flac", command)
        self.assertIn("-filter_complex", command)
        self.assertIn("[mixed_audio]", command)
        self.assertIn("adelay=200:all=1", command[command.index("-filter_complex") + 1])
        self.assertIn("-shortest", command)
        self.assertEqual(command[command.index("-c:a") + 1], "aac")

    def test_build_normalize_command_can_use_nvenc(self) -> None:
        command = build_normalize_command("op.mp4", "out/op.normalized.mp4", 1920, 1080, video_codec="h264_nvenc", nvenc_preset="p4")
        self.assertIn("h264_nvenc", command)
        self.assertIn("-preset", command)
        self.assertIn("p4", command)
        self.assertIn("-cq", command)
        self.assertIn("-profile:v", command)

    def test_build_concat_command_uses_concat_demuxer(self) -> None:
        command = build_concat_command("out/concat.txt", "out/final.mp4")
        self.assertEqual(command[:2], ["ffmpeg", "-y"])
        self.assertEqual(command[command.index("-f") + 1], "concat")
        self.assertIn("+genpts", command)
        self.assertIn("0:v:0", command)
        self.assertIn("0:a:0", command)
        self.assertIn("out/final.mp4", command)

    def test_write_concat_manifest_writes_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            first = base / "a.mp4"
            second = base / "b.mp4"
            first.write_text("x", encoding="utf-8")
            second.write_text("x", encoding="utf-8")
            manifest = write_concat_manifest([str(first), str(second)], str(base / "concat.txt"))
            text = manifest.read_text(encoding="utf-8")
            self.assertIn(first.resolve().as_posix(), text)
            self.assertIn(second.resolve().as_posix(), text)

    def test_optional_clip_returns_existing_path_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            clip = Path(temp_dir) / "op.mp4"
            clip.write_text("x", encoding="utf-8")
            self.assertEqual(optional_clip(str(clip)), clip)
            self.assertIsNone(optional_clip(str(Path(temp_dir) / "missing.mp4")))

    def test_extract_command_maps_requested_track(self) -> None:
        command = build_extract_audio_command("input.mkv", "out/audio.wav", "0:a:1")
        self.assertEqual(command[5], "0:a:1")
        self.assertIn("pcm_s16le", command)

    def test_whisperx_command_uses_environment_token_without_exposing_it(self) -> None:
        with mock.patch.dict("os.environ", {"HF_TOKEN": "secret-token"}, clear=False):
            command = build_whisperx_command(
                "out/audio.wav",
                "out",
                model="large-v3",
                device="cpu",
                compute_type="int8",
                diarize=True,
                min_speakers=3,
                max_speakers=3,
                vad_onset=0.3,
                vad_offset=0.15,
            )
        self.assertEqual(command[:3], [sys.executable, "-m", "whisperx"])
        self.assertIn("--diarize", command)
        self.assertNotIn("--hf_token", command)
        self.assertNotIn("secret-token", command)
        self.assertIn("--min_speakers", command)
        self.assertIn("--vad_onset", command)
        self.assertIn("--vad_offset", command)

    def test_validate_hf_token_requires_environment_variable(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(SystemExit):
                validate_hf_token(diarize=True)

    def test_expected_paths_match_audio_stem(self) -> None:
        transcript = expected_transcript_path("out/input.0_a_1.wav", "out")
        log_path = expected_log_path("out/input.0_a_1.wav", "out")
        self.assertEqual(str(transcript).replace("\\", "/"), "out/input.0_a_1.json")
        self.assertEqual(str(log_path).replace("\\", "/"), "out/input.0_a_1.whisperx.log")

    def test_derive_pipeline_paths_uses_track_suffix(self) -> None:
        audio, transcript, ass_path = derive_pipeline_paths("input.mkv", "out", "0:a:2")
        self.assertEqual(str(audio).replace("\\", "/"), "out/input.0_a_2.wav")
        self.assertEqual(str(transcript).replace("\\", "/"), "out/input.0_a_2.json")
        self.assertEqual(str(ass_path).replace("\\", "/"), "out/input.0_a_2.ass")

    def test_derive_export_paths_creates_video_export_layout(self) -> None:
        work_dir, final_video = derive_export_paths("video_import/input.mkv", "video_export", "0:a:1")
        self.assertEqual(str(work_dir).replace("\\", "/"), "video_export/input")
        self.assertEqual(str(final_video).replace("\\", "/"), "video_export/input/input.0_a_1.subtitled.mp4")

    def test_derive_merged_export_paths_creates_single_video_layout(self) -> None:
        work_dir, final_video = derive_merged_export_paths("video_import/input.mkv", "video_export")
        self.assertEqual(str(work_dir).replace("\\", "/"), "video_export/input")
        self.assertEqual(str(final_video).replace("\\", "/"), "video_export/input/input.merged.subtitled.mp4")

    def test_iter_video_files_filters_supported_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            (base / "a.mkv").write_text("x", encoding="utf-8")
            (base / "b.mp4").write_text("x", encoding="utf-8")
            (base / "op.mp4").write_text("x", encoding="utf-8")
            (base / "ed.mp4").write_text("x", encoding="utf-8")
            (base / "c.txt").write_text("x", encoding="utf-8")
            files = iter_video_files(temp_dir)
            self.assertEqual([path.name for path in files], ["a.mkv", "b.mp4"])

    def test_build_ass_from_transcript_writes_ass_file(self) -> None:
        sample = {"segments": [{"start": 0.1, "end": 0.9, "speaker": "Oz", "text": "test line", "layout_row": 0}]}
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript_path = Path(temp_dir) / "sample.json"
            ass_path = Path(temp_dir) / "sample.ass"
            transcript_path.write_text(json.dumps(sample), encoding="utf-8")

            result = build_ass_from_transcript(str(transcript_path), str(ass_path))

            self.assertEqual(result, ass_path)
            self.assertIn("test line", ass_path.read_text(encoding="utf-8"))

    def test_run_media_to_ass_many_preserves_track_order(self) -> None:
        calls: list[str] = []

        def fake_run_media_to_ass(input_media: str, audio_track: str, output_dir: str, **_: object) -> Path:
            calls.append(audio_track)
            return Path(output_dir) / f"{Path(input_media).stem}.{audio_track.replace(':', '_')}.ass"

        import src.pipeline as pipeline

        original = pipeline.run_media_to_ass
        pipeline.run_media_to_ass = fake_run_media_to_ass
        try:
            results = run_media_to_ass_many("input.mkv", ["0:a:1", "0:a:3"], "out")
        finally:
            pipeline.run_media_to_ass = original

        self.assertEqual(calls, ["0:a:1", "0:a:3"])
        self.assertEqual([str(path).replace("\\", "/") for path in results], ["out/input.0_a_1.ass", "out/input.0_a_3.ass"])

    def test_normalize_diarize_tracks_requires_environment_token(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(SystemExit):
                normalize_diarize_tracks({"0:a:3"})
        with mock.patch.dict("os.environ", {"HF_TOKEN": "secret-token"}, clear=False):
            self.assertEqual(normalize_diarize_tracks({"0:a:3"}), {"0:a:3"})

    def test_speaker_for_track_defaults_guest_without_diarization(self) -> None:
        self.assertEqual(speaker_for_track("0:a:1", None), "Oz")
        self.assertEqual(speaker_for_track("0:a:3", None, None), "Guest")

    def test_split_segment_breaks_long_text_into_shorter_units(self) -> None:
        segment = {"start": 0.0, "end": 8.0, "text": "あの怪物でかいぞ!怪物にも個体差がある。だが対処法は同じだ。撃て!", "speaker": "Oz", "source_track": "0:a:1", "max_width": 22}
        parts = split_segment(segment)
        self.assertGreater(len(parts), 3)
        self.assertTrue(all((part["end"] - part["start"]) <= 3.6 for part in parts))

    def test_split_segment_uses_word_timing_when_available(self) -> None:
        segment = {
            "start": 0.0,
            "end": 4.0,
            "text": "こんにちは。さようなら。",
            "speaker": "Oz",
            "source_track": "0:a:1",
            "max_width": 8,
            "words": [
                {"word": "こんにちは。", "start": 0.2, "end": 1.0},
                {"word": "さようなら。", "start": 1.1, "end": 1.8},
            ],
        }
        parts = split_segment(segment)
        self.assertGreaterEqual(len(parts), 2)
        self.assertGreaterEqual(parts[0]["start"], 0.2)
        self.assertAlmostEqual(parts[-1]["end"], 1.88, places=2)

    def test_pack_segment_pages_uses_two_line_page_capacity(self) -> None:
        segment = {
            "start": 0.0,
            "end": 2.0,
            "text": "abcdefghijkl",
            "speaker": "Oz",
            "source_track": "0:a:1",
            "max_width": 6,
        }

        class FakeParser:
            def parse(self, text: str) -> list[str]:
                return ["abcdef", "ghijkl"]

        with mock.patch("src.subtitle_packer.create_budoux_parser", return_value=FakeParser()):
            parts = pack_segment_pages(segment)

        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["text"], "abcdefghijkl")

    def test_normalize_text_uses_morpheme_boundary_when_budoux_has_none(self) -> None:
        text = r"\u30c0\u30a6\u30f3\u30ed\u30fc\u30c9\u3067\u304d\u308b\u3088\u3046\u306b\u306a\u308b\u3068\u3046\u3093\u3046\u3093\u3046\u3093\u305d\u3046".encode("ascii").decode("unicode_escape")

        class FakeParser:
            def parse(self, text: str) -> list[str]:
                return [text]

        with mock.patch("src.subtitle_packer.create_budoux_parser", return_value=FakeParser()):
            wrapped = normalize_text(text, max_width=28, max_lines=2)

        self.assertNotIn(r"\u3088".encode("ascii").decode("unicode_escape") + r"\N" + r"\u3046".encode("ascii").decode("unicode_escape"), wrapped)
        self.assertNotIn(r"\u30c0\u30a6\u30f3".encode("ascii").decode("unicode_escape") + r"\N", wrapped)

    def test_pack_segment_pages_infers_silence_from_stretched_character(self) -> None:
        segment = {
            "start": 0.0,
            "end": 2.0,
            "text": "abcdeUVWXY",
            "speaker": "Oz",
            "source_track": "0:a:1",
            "max_width": 28,
            "words": [
                {"word": "a", "start": 0.0, "end": 0.1},
                {"word": "b", "start": 0.1, "end": 0.2},
                {"word": "c", "start": 0.2, "end": 0.3},
                {"word": "d", "start": 0.3, "end": 0.4},
                {"word": "e", "start": 0.4, "end": 1.4},
                {"word": "UVWXY", "start": 1.4, "end": 1.9},
            ],
        }

        class FakeParser:
            def parse(self, text: str) -> list[str]:
                return ["abcde", "UVWXY"] if text == "abcdeUVWXY" else [text]

        with mock.patch("src.subtitle_packer.create_budoux_parser", return_value=FakeParser()):
            parts = pack_segment_pages(segment, subtitle_max_gap_seconds=0.1)

        self.assertEqual([part["text"] for part in parts], ["abcde", "UVWXY"])
        self.assertLess(parts[0]["end"], parts[1]["start"])

    def test_pack_segment_pages_keeps_trailing_conjunction_with_previous_text(self) -> None:
        segment = {
            "start": 0.0,
            "end": 2.0,
            "text": "abcde" + r"\u304b\u3089".encode("ascii").decode("unicode_escape"),
            "speaker": "Oz",
            "source_track": "0:a:1",
            "max_width": 28,
            "words": [
                {"word": "abcde", "start": 0.0, "end": 0.5},
                {"word": r"\u304b\u3089".encode("ascii").decode("unicode_escape"), "start": 1.2, "end": 1.5},
            ],
        }

        class FakeParser:
            def parse(self, text: str) -> list[str]:
                suffix = r"\u304b\u3089".encode("ascii").decode("unicode_escape")
                return ["abcde", suffix] if text == "abcde" + suffix else [text]

        with mock.patch("src.subtitle_packer.create_budoux_parser", return_value=FakeParser()):
            parts = pack_segment_pages(segment, subtitle_max_gap_seconds=0.1)

        self.assertEqual(len(parts), 1)
        self.assertTrue(parts[0]["text"].endswith(r"\u304b\u3089".encode("ascii").decode("unicode_escape")))

    def test_pack_segment_pages_rejoins_single_characters_into_short_utterance(self) -> None:
        prefix = r"\u304a\u3081\u3048".encode("ascii").decode("unicode_escape")
        first = r"\u306f".encode("ascii").decode("unicode_escape")
        second = r"\u3044".encode("ascii").decode("unicode_escape")
        segment = {
            "start": 0.0,
            "end": 2.0,
            "text": prefix + first + second,
            "speaker": "Oz",
            "source_track": "0:a:1",
            "max_width": 28,
            "words": [
                {"word": prefix, "start": 0.0, "end": 0.3},
                {"word": first, "start": 1.0, "end": 1.8},
                {"word": second, "start": 1.8, "end": 1.9},
            ],
        }

        class FakeParser:
            def parse(self, text: str) -> list[str]:
                full_text = prefix + first + second
                return [prefix, first, second] if text == full_text else [text]

        with mock.patch("src.subtitle_packer.create_budoux_parser", return_value=FakeParser()):
            parts = pack_segment_pages(segment, subtitle_max_gap_seconds=0.1)

        self.assertEqual([part["text"] for part in parts], [prefix, first + second])

    def test_pack_segment_pages_requires_japanese_layout_dependencies(self) -> None:
        segment = {"start": 0.0, "end": 1.0, "text": "hello", "max_width": 28}

        with mock.patch("src.subtitle_packer.create_budoux_parser", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "pip install -r requirements.txt"):
                pack_segment_pages(segment)

    def test_pack_segment_pages_snaps_large_word_gap_to_natural_boundary(self) -> None:
        segment = {
            "start": 0.0,
            "end": 2.5,
            "text": "abcdeUVWXY",
            "speaker": "Oz",
            "source_track": "0:a:1",
            "max_width": 28,
            "words": [
                {"word": "abcde", "start": 0.0, "end": 0.5},
                {"word": "UVWXY", "start": 0.9, "end": 1.4},
            ],
        }

        class FakeParser:
            def parse(self, text: str) -> list[str]:
                return ["abcde", "UVWXY"]

        with mock.patch("src.subtitle_packer.create_budoux_parser", return_value=FakeParser()):
            parts = pack_segment_pages(segment, subtitle_max_gap_seconds=0.32)

        self.assertEqual(len(parts), 2)
        self.assertLess(parts[0]["end"], parts[1]["start"] + 0.01)

    def test_pack_segment_pages_splits_on_large_word_gap(self) -> None:
        segment = {
            "start": 0.0,
            "end": 2.5,
            "text": "abcdeUVWXY",
            "speaker": "Oz",
            "source_track": "0:a:1",
            "max_width": 28,
            "words": [
                {"word": "abcde", "start": 0.0, "end": 0.5},
                {"word": "UVWXY", "start": 0.9, "end": 1.4},
            ],
        }

        class FakeParser:
            def parse(self, text: str) -> list[str]:
                return ["abcde", "UVWXY"]

        with mock.patch("src.subtitle_packer.create_budoux_parser", return_value=FakeParser()):
            parts = pack_segment_pages(segment, subtitle_max_gap_seconds=0.32)

        self.assertEqual(len(parts), 2)
        self.assertLess(parts[0]["end"], parts[1]["start"] + 0.01)

    def test_pack_segment_pages_keeps_small_word_gap_together(self) -> None:
        segment = {
            "start": 0.0,
            "end": 2.5,
            "text": "abcdeUVWXY",
            "speaker": "Oz",
            "source_track": "0:a:1",
            "max_width": 28,
            "words": [
                {"word": "abcde", "start": 0.0, "end": 0.5},
                {"word": "UVWXY", "start": 0.75, "end": 1.4},
            ],
        }

        class FakeParser:
            def parse(self, text: str) -> list[str]:
                return [text]

        with mock.patch("src.subtitle_packer.create_budoux_parser", return_value=FakeParser()):
            parts = pack_segment_pages(segment, subtitle_max_gap_seconds=0.32)

        self.assertEqual(len(parts), 1)

    def test_pack_segment_pages_trims_end_using_word_timing_padding(self) -> None:
        segment = {
            "start": 0.0,
            "end": 3.0,
            "text": "abcde",
            "speaker": "Oz",
            "source_track": "0:a:1",
            "max_width": 28,
            "words": [
                {"word": "abcde", "start": 0.2, "end": 0.7},
            ],
        }

        class FakeParser:
            def parse(self, text: str) -> list[str]:
                return [text]

        with mock.patch("src.subtitle_packer.create_budoux_parser", return_value=FakeParser()):
            parts = pack_segment_pages(segment, subtitle_end_padding_seconds=0.08)

        self.assertEqual(len(parts), 1)
        self.assertAlmostEqual(parts[0]["end"], 0.78, places=2)

    def test_pack_segment_pages_respects_min_duration_after_trim(self) -> None:
        segment = {
            "start": 0.0,
            "end": 1.0,
            "text": "??",
            "speaker": "Oz",
            "source_track": "0:a:1",
            "max_width": 28,
            "words": [
                {"word": "??", "start": 0.2, "end": 0.25},
            ],
        }

        class FakeParser:
            def parse(self, text: str) -> list[str]:
                return [text]

        with mock.patch("src.subtitle_packer.create_budoux_parser", return_value=FakeParser()):
            parts = pack_segment_pages(segment, subtitle_end_padding_seconds=0.08, subtitle_min_duration_seconds=0.35)

        self.assertEqual(len(parts), 1)
        self.assertAlmostEqual(parts[0]["end"] - parts[0]["start"], 0.35, places=2)

    def test_pack_segment_pages_caps_single_long_word_duration(self) -> None:
        segment = {
            "start": 0.0,
            "end": 12.0,
            "text": "longword",
            "speaker": "Oz",
            "source_track": "0:a:1",
            "max_width": 28,
            "words": [
                {"word": "longword", "start": 0.2, "end": 10.0},
            ],
        }

        class FakeParser:
            def parse(self, text: str) -> list[str]:
                return [text]

        with mock.patch("src.subtitle_packer.create_budoux_parser", return_value=FakeParser()):
            parts = pack_segment_pages(segment, subtitle_end_padding_seconds=0.08)

        self.assertEqual(len(parts), 1)
        self.assertAlmostEqual(parts[0]["end"] - parts[0]["start"], 2.8, places=2)

    def test_split_segment_keeps_short_sentence_together(self) -> None:
        segment = {
            "start": 0.0,
            "end": 2.4,
            "text": "うわ! それ危ない。",
            "speaker": "Oz",
            "source_track": "0:a:1",
            "max_width": 28,
        }
        parts = split_segment(segment)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["text"], "うわ!それ危ない。")

    def test_split_segment_allows_two_short_sentences_together(self) -> None:
        segment = {
            "start": 0.0,
            "end": 2.6,
            "text": "行くぞ。準備して。",
            "speaker": "Oz",
            "source_track": "0:a:1",
            "max_width": 28,
        }
        parts = split_segment(segment)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["text"], "行くぞ。準備して。")

    def test_assign_bottom_rows_drops_shortest_without_shifting_time(self) -> None:
        segments = [
            {"start": 0.0, "end": 2.0, "speaker": "Guest", "text": "longer line", "layout_row": 0, "filter_reasons": []},
            {"start": 0.2, "end": 1.6, "speaker": "Guest", "text": "mid line", "layout_row": 0, "filter_reasons": []},
            {"start": 0.3, "end": 1.4, "speaker": "Oz", "text": "short", "layout_row": 0, "filter_reasons": []},
            {"start": 0.4, "end": 0.9, "speaker": "Guest", "text": "tiny", "layout_row": 0, "filter_reasons": []},
        ]
        assigned, overflow = assign_bottom_rows(segments)
        self.assertEqual(len(assigned), 3)
        self.assertEqual(len(overflow), 1)
        self.assertEqual(overflow[0]["text"], "tiny")
        self.assertIn("overflow_dropped", overflow[0]["filter_reasons"])
        self.assertEqual(sorted(set(segment["layout_row"] for segment in assigned)), [0, 1, 2])

    def test_assign_bottom_rows_reserves_two_rows_for_two_line_caption(self) -> None:
        segments = [
            {"start": 0.0, "end": 2.0, "speaker": "A", "text": "abcdefghijklmno", "layout_row": 0, "max_width": 12, "filter_reasons": []},
            {"start": 0.2, "end": 1.0, "speaker": "Oz", "text": "short", "layout_row": 0, "max_width": 28, "filter_reasons": []},
        ]

        assigned, overflow = assign_bottom_rows(segments)

        self.assertEqual(len(overflow), 0)
        by_text = {segment["text"]: segment for segment in assigned}
        self.assertEqual(by_text["abcdefghijklmno"]["layout_row"], 0)
        self.assertEqual(by_text["abcdefghijklmno"]["layout_row_span"], 2)
        self.assertEqual(by_text["short"]["layout_row"], 2)

    def test_merge_transcripts_filters_nonspeech_and_keeps_guest_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            track1 = base / "track1.json"
            track3 = base / "track3.json"
            track1.write_text(json.dumps({"segments": [{"start": 2.0, "end": 3.0, "text": "oz line"}]}), encoding="utf-8")
            track3.write_text(json.dumps({"segments": [
                {"start": 1.0, "end": 2.0, "text": "guest line"},
                {"start": 4.0, "end": 9.0, "text": "ガンマ型のアッシュはかなりの大型です 戦闘車両やコンバットフレームの天敵か"}
            ]}), encoding="utf-8")

            merged, filtered = merge_transcripts({"0:a:1": str(track1), "0:a:3": str(track3)})

            self.assertTrue(all(segment["layout_row"] in {0, 1, 2} for segment in merged["segments"]))
            self.assertTrue(any(segment["speaker"] == "Guest" for segment in merged["segments"]))
            self.assertGreater(len(filtered["segments"]), 0)
            self.assertTrue(any("game_terms" in ",".join(segment["filter_reasons"]) for segment in filtered["segments"]))

    def test_write_youtube_texts_creates_title_and_description_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            merged_path = Path(temp_dir) / "sample.merged.json"
            merged_path.write_text(
                json.dumps(
                    {
                        "segments": [
                            {"start": 12.0, "end": 13.8, "speaker": "Oz", "text": "うわ! ボス来た!"},
                            {"start": 26.0, "end": 28.0, "speaker": "Guest", "text": "ここで突っ込むの危ないって!"},
                            {"start": 45.0, "end": 47.5, "speaker": "Guest", "text": "最後に逆転できそう!"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            title_path, description_path = write_youtube_texts(
                str(merged_path),
                timestamp_offset_seconds=8.0,
            )

            self.assertTrue(title_path.exists())
            self.assertTrue(description_path.exists())
            self.assertIn("【実況】", title_path.read_text(encoding="utf-8"))
            description = description_path.read_text(encoding="utf-8")
            self.assertIn("おすすめタイトル案:", description)
            self.assertIn("00:20", description)

    def test_run_command_with_utf8_log_preserves_failed_process_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "whisperx.log"
            command = [
                sys.executable,
                "-c",
                "import sys; print('partial output', flush=True); print('failure detail', file=sys.stderr, flush=True); raise SystemExit(7)",
            ]
            with mock.patch("builtins.print") as streamed:
                with self.assertRaises(subprocess.CalledProcessError):
                    run_command_with_utf8_log(command, str(log_path))

            log_text = log_path.read_text(encoding="utf-8")
            self.assertIn("partial output", log_text)
            self.assertIn("failure detail", log_text)
            self.assertIn("code 7", log_text)
            streamed_text = "".join(str(call.args[0]) for call in streamed.call_args_list)
            self.assertIn("partial output", streamed_text)

    def test_derive_youtube_text_paths_uses_merged_stem(self) -> None:
        title_path, description_path = derive_youtube_text_paths("video_export/input/input.merged.json")
        self.assertEqual(str(title_path).replace("\\", "/"), "video_export/input/input.youtube_title.txt")
        self.assertEqual(str(description_path).replace("\\", "/"), "video_export/input/input.youtube_description.txt")


if __name__ == "__main__":
    unittest.main()
