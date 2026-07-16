import unittest
from unittest import mock

import numpy as np

from src.craig_pipeline import (
    build_craig_segments_for_transcript,
    build_speaker_style_map,
    calculate_segment_volume_levels,
    estimate_offset,
    list_craig_audio_files,
    merge_craig_transcripts,
    normalize_db_threshold,
    parse_craig_speaker_name,
    resolve_alignment,
    resolve_craig_audio_files,
    resolve_craig_target_paths,
    resolve_reference_audio_path,
    shift_segment,
    transcribe_audio_file,
)


class CraigPipelineTests(unittest.TestCase):
    def test_main_reports_missing_dependencies_before_processing_media(self) -> None:
        import sys
        import src.craig_pipeline as craig_pipeline
        from src.runtime_dependencies import RuntimeDependencyStatus

        argv = ["craig_pipeline", "--video", "missing.mkv", "--audio-file", "missing.flac", "--run"]
        status = RuntimeDependencyStatus(ffmpeg=False, ffprobe=False, whisperx=False)
        with mock.patch.object(sys, "argv", argv), mock.patch.object(craig_pipeline, "check_runtime_dependencies", return_value=status):
            with self.assertRaisesRegex(SystemExit, "Missing runtime dependencies: ffmpeg, ffprobe, whisperx"):
                craig_pipeline.main()
    def test_normalize_db_threshold_accepts_number_or_ffmpeg_value(self) -> None:
        self.assertEqual(normalize_db_threshold(-40), "-40dB")
        self.assertEqual(normalize_db_threshold("-35dB"), "-35dB")

    def test_calculate_segment_volume_levels_is_relative_to_speaker_median(self) -> None:
        samples = np.concatenate([
            np.full(10, 0.1, dtype=np.float32),
            np.full(10, 0.8, dtype=np.float32),
        ])
        segments = [{"start": 0.0, "end": 1.0}, {"start": 1.0, "end": 2.0}]

        with mock.patch("src.craig_pipeline.decode_audio_samples", return_value=samples):
            levels = calculate_segment_volume_levels("speaker.flac", segments, sample_rate=10)

        self.assertLess(levels[0], 0.0)
        self.assertGreater(levels[1], 0.0)
        self.assertTrue(all(-1.0 <= level <= 1.0 for level in levels))

    def test_build_craig_segments_applies_volume_font_scale(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            transcript_path = Path(temp_dir) / "1-speaker-a.json"
            transcript_path.write_text(json.dumps({"segments": [{"start": 0.0, "end": 1.0, "text": "loud"}]}), encoding="utf-8")
            with mock.patch("src.craig_pipeline.calculate_segment_volume_levels", return_value=[1.0]):
                segments = build_craig_segments_for_transcript("1-speaker-a.flac", str(transcript_path), {"speaker-a": "Oz"}, 0.0, 50, 20.0)

        self.assertAlmostEqual(segments[0]["subtitle_font_scale"], 1.2)
        self.assertEqual(segments[0]["max_width"], 23)

    def test_normalize_db_threshold_rejects_invalid_value(self) -> None:
        with self.assertRaises(SystemExit):
            normalize_db_threshold("quiet")

    def test_merge_craig_transcripts_uses_segment_builder(self) -> None:
        import src.craig_pipeline as craig_pipeline

        original_builder = craig_pipeline.build_craig_segments_for_transcript
        try:
            craig_pipeline.build_craig_segments_for_transcript = lambda audio_path, transcript_path, style_map, offset_seconds: [{"start": 0.0, "end": 1.0, "speaker": "Oz", "text": audio_path, "layout_row": 0, "filter_reasons": [], "source_track": "craig:test", "max_width": 24}]
            merged, filtered = craig_pipeline.merge_craig_transcripts({"a.aac": "a.json", "b.aac": "b.json"}, {"a": "Oz", "b": "A"}, 0.0)
        finally:
            craig_pipeline.build_craig_segments_for_transcript = original_builder

        self.assertEqual(len(merged["segments"]), 2)
        self.assertEqual(len(filtered["segments"]), 0)

    def test_transcribe_audio_file_skips_existing_transcript(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / "1-speaker-a.json"
            transcript.write_text("{}", encoding="utf-8")
            result = transcribe_audio_file("1-speaker-a.flac", temp_dir, skip_existing=True)
            self.assertEqual(result, transcript)

    def test_build_craig_segments_for_transcript_builds_shifted_segments(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            transcript_path = Path(temp_dir) / "1-speaker-a.json"
            transcript_path.write_text(json.dumps({"segments": [{"start": 0.0, "end": 1.0, "text": "??!"}]}, ensure_ascii=False), encoding="utf-8")
            segments = build_craig_segments_for_transcript(str(Path(temp_dir) / "1-speaker-a.flac"), str(transcript_path), {"speaker-a": "Oz"}, 1.25)
            self.assertEqual(len(segments), 1)
            self.assertEqual(segments[0]["speaker"], "Oz")
            self.assertEqual(segments[0]["source_file"], "1-speaker-a.flac")
            self.assertAlmostEqual(float(segments[0]["start"]), 1.25)

    def test_resolve_alignment_honors_explicit_track(self) -> None:
        import src.craig_pipeline as craig_pipeline

        original_decode = craig_pipeline.decode_audio_samples
        try:
            def fake_decode(path: str, sample_rate: int = 120, stream_selector: str | None = None):
                if stream_selector:
                    return np.array([0.0, 0.0, 1.0, 0.2, 0.0], dtype=np.float32)
                return np.array([1.0, 0.2, 0.0], dtype=np.float32)

            craig_pipeline.decode_audio_samples = fake_decode
            matched_track, offset_seconds, score = resolve_alignment("video.mkv", "ref.flac", "0:a:2", 10)
        finally:
            craig_pipeline.decode_audio_samples = original_decode

        self.assertEqual(matched_track, "0:a:2")
        self.assertGreaterEqual(score, 0.0)

    def test_list_craig_audio_files_accepts_flac_and_aac(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            (base / "1-speaker-a.flac").write_text("x", encoding="utf-8")
            (base / "2-speaker-b.aac").write_text("x", encoding="utf-8")
            (base / "note.txt").write_text("x", encoding="utf-8")
            files = list_craig_audio_files(temp_dir)
            self.assertEqual([path.name for path in files], ["1-speaker-a.flac", "2-speaker-b.aac"])

    def test_resolve_selected_audio_files_accepts_multiple_directories(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "a" / "1-speaker-a.flac"
            second = root / "b" / "2-speaker-b.aac"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_bytes(b"a")
            second.write_bytes(b"b")

            files = resolve_craig_audio_files(None, [str(second), str(first)])

            self.assertEqual([path.name for path in files], ["1-speaker-a.flac", "2-speaker-b.aac"])
            self.assertTrue(all(path.is_absolute() for path in files))

    def test_resolve_reference_audio_accepts_absolute_path_and_file_name(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "1-speaker-a.flac"
            second = Path(temp_dir) / "2-speaker-b.flac"
            first.write_bytes(b"a")
            second.write_bytes(b"b")
            files = [first.resolve(), second.resolve()]

            self.assertEqual(resolve_reference_audio_path(files, str(second.resolve())), second.resolve())
            self.assertEqual(resolve_reference_audio_path(files, "2-speaker-b.flac"), second.resolve())
            self.assertEqual(resolve_reference_audio_path(files, None), first.resolve())

    def test_parse_craig_speaker_name_uses_suffix_after_index(self) -> None:
        self.assertEqual(parse_craig_speaker_name(r"C:\tmp\1-speaker-a.aac"), "speaker-a")

    def test_build_speaker_style_map_assigns_reference_then_palette(self) -> None:
        from pathlib import Path

        style_map = build_speaker_style_map([
            Path("1-speaker-a.aac"),
            Path("2-speaker-b.aac"),
            Path("3-speaker-c.aac"),
            Path("4-speaker-d.aac"),
        ])
        self.assertEqual(style_map["speaker-a"], "Oz")
        self.assertEqual(style_map["speaker-b"], "A")
        self.assertEqual(style_map["speaker-c"], "B")
        self.assertEqual(style_map["speaker-d"], "C")

    def test_build_speaker_style_map_sorts_by_file_name_across_directories(self) -> None:
        from pathlib import Path

        style_map = build_speaker_style_map([
            Path("a/2-speaker-b.flac"),
            Path("z/1-speaker-a.flac"),
        ])

        self.assertEqual(style_map["speaker-a"], "Oz")
        self.assertEqual(style_map["speaker-b"], "A")

    def test_estimate_offset_detects_positive_lag(self) -> None:
        reference = np.array([0.0, 1.0, 0.2, 0.0], dtype=np.float32)
        candidate = np.array([0.0, 0.0, 0.0, 1.0, 0.2, 0.0, 0.0], dtype=np.float32)
        offset_seconds, score = estimate_offset(reference, candidate, sample_rate=10)
        self.assertAlmostEqual(offset_seconds, 0.2, places=3)
        self.assertGreater(score, 0.0)

    def test_shift_segment_applies_offset_to_segment_and_words(self) -> None:
        shifted = shift_segment(
            {
                "start": 1.0,
                "end": 2.0,
                "words": [{"word": "hi", "start": 1.1, "end": 1.4}],
            },
            0.5,
        )
        self.assertIsNotNone(shifted)
        self.assertAlmostEqual(float(shifted["start"]), 1.5)
        self.assertAlmostEqual(float(shifted["words"][0]["start"]), 1.6)

    def test_merge_craig_transcripts_applies_style_map_and_offset(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            transcript_path = Path(temp_dir) / "1-speaker-a.json"
            transcript_path.write_text(
                json.dumps({
                    "segments": [
                        {
                            "start": 0.0,
                            "end": 1.0,
                            "text": "こんにちは!",
                            "words": [{"word": "こんにちは!", "start": 0.0, "end": 1.0}],
                        }
                    ]
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            merged, filtered = merge_craig_transcripts(
                {str(Path(temp_dir) / "1-speaker-a.aac"): str(transcript_path)},
                {"speaker-a": "Oz"},
                0.75,
            )
            self.assertEqual(len(filtered["segments"]), 0)
            self.assertEqual(merged["segments"][0]["speaker"], "Oz")
            self.assertAlmostEqual(float(merged["segments"][0]["start"]), 0.75)

    def test_resolve_craig_target_paths_finds_target_layout(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target_dir = root / "video_import" / "game_session_01"
            audio_dir = target_dir / "craig-example.flac"
            audio_dir.mkdir(parents=True)
            video = target_dir / "recording.mkv"
            video.write_bytes(b"video")
            (audio_dir / "1-speaker-a.flac").write_bytes(b"audio")

            resolved_video, resolved_audio_dir, resolved_output_dir = resolve_craig_target_paths(
                "game_session_01",
                None,
                None,
                None,
                input_root=str(root / "video_import"),
                export_root=str(root / "video_export"),
            )

            self.assertEqual(Path(resolved_video), video)
            self.assertEqual(Path(resolved_audio_dir), audio_dir)
            self.assertEqual(Path(resolved_output_dir), root / "video_export" / "game_session_01")

    def test_resolve_craig_target_paths_respects_explicit_output_dir(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target_dir = root / "video_import" / "target"
            audio_dir = target_dir / "craig-example"
            audio_dir.mkdir(parents=True)
            (target_dir / "input.mkv").write_bytes(b"video")
            (audio_dir / "1-speaker-a.flac").write_bytes(b"audio")
            explicit_output = root / "custom_output"

            _, _, resolved_output_dir = resolve_craig_target_paths(
                "target",
                None,
                None,
                str(explicit_output),
                input_root=str(root / "video_import"),
                export_root=str(root / "video_export"),
            )

            self.assertEqual(Path(resolved_output_dir), explicit_output)

    def test_resolve_craig_target_paths_rejects_multiple_videos(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target_dir = root / "video_import" / "target"
            audio_dir = target_dir / "craig-example"
            audio_dir.mkdir(parents=True)
            (target_dir / "a.mkv").write_bytes(b"video")
            (target_dir / "b.mp4").write_bytes(b"video")
            (audio_dir / "1-speaker-a.flac").write_bytes(b"audio")

            with self.assertRaises(SystemExit) as raised:
                resolve_craig_target_paths(
                    "target",
                    None,
                    None,
                    None,
                    input_root=str(root / "video_import"),
                    export_root=str(root / "video_export"),
                )

            self.assertIn("Multiple video file", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
