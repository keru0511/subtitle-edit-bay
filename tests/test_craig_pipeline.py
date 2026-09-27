import json
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from src.data_boundary import coerce_float
from src.craig_pipeline import (
    AlignmentResult,
    SegmentRefinementResult,
    build_craig_segments_for_transcript,
    CraigTranscriptionBatch,
    build_speaker_style_map,
    calculate_segment_volume_levels,
    estimate_offset,
    find_best_reference_track,
    list_craig_audio_files,
    merge_craig_transcripts,
    normalize_db_threshold,
    parse_craig_speaker_name,
    resolve_alignment,
    resolve_craig_audio_files,
    resolve_craig_target_paths,
    resolve_reference_audio_path,
    run_alignment_stage,
    run_refine_stage,
    run_transcription_stage,
    shift_segment,
    transcribe_audio_file,
    transcribe_craig_audio_files,
)
from tests.typed_case import TypedTestCase
from tests.typed_data import entries, mock_kwargs


def _write_transcript(path: Path, segments: list[dict[str, object]]) -> None:
    payload: dict[str, object] = {"segments": segments}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _required_path(value: str | None) -> Path:
    if value is None:
        raise AssertionError("expected a resolved path")
    return Path(value)


class CraigPipelineTests(TypedTestCase):
    def test_main_reports_missing_dependencies_before_processing_media(self) -> None:
        import sys
        import src.craig_pipeline as craig_pipeline
        from src.runtime_dependencies import RuntimeDependencyStatus

        argv = ["craig_pipeline", "--video", "missing.mkv", "--audio-file", "missing.flac", "--run"]
        status = RuntimeDependencyStatus(ffmpeg=False, ffprobe=False, whisperx=False)
        with mock.patch.object(sys, "argv", argv), mock.patch.object(craig_pipeline, "check_runtime_dependencies", return_value=status):
            with self.assertRaisesRegex(SystemExit, "Missing runtime dependencies: ffmpeg, ffprobe, whisperx"):
                craig_pipeline.main()

    def test_main_preserves_null_language_for_auto_detection(self) -> None:
        import sys
        import tempfile
        from pathlib import Path

        import src.craig_pipeline as craig_pipeline

        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "1-speaker.flac"
            audio.touch()
            argv = ["craig_pipeline", "--video", "video.mkv", "--audio-file", str(audio), "--run"]
            result: dict[str, Path | str | float | None] = {
                key: None for key in (
                    "reference_audio", "matched_track", "offset_seconds", "alignment_score",
                    "merged_json", "filtered_json", "ass_path", "cut_merged_json",
                    "cut_ass_path", "final_video", "no_speech_report",
                )
            }
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(craig_pipeline, "load_command_runtime_config", return_value=dict[str, object]({"language": None})),
                mock.patch.object(craig_pipeline, "format_dependency_error", return_value=None),
                mock.patch.object(craig_pipeline, "resolve_alignment", return_value=("0:a:0", 0.0, 1.0)),
                mock.patch.object(craig_pipeline, "run_craig_pipeline", return_value=result) as run_pipeline,
            ):
                craig_pipeline.main()

            self.assertIsNone(mock_kwargs(run_pipeline)["language"])

    def test_normalize_db_threshold_accepts_number_or_ffmpeg_value(self) -> None:
        self.assertEqual(normalize_db_threshold(-40), "-40dB")
        self.assertEqual(normalize_db_threshold("-35dB"), "-35dB")

    def test_calculate_segment_volume_levels_is_relative_to_speaker_median(self) -> None:
        samples = np.concatenate([
            np.full(10, 0.1, dtype=np.float32),
            np.full(10, 0.8, dtype=np.float32),
        ])
        segments: list[dict[str, object]] = [{"start": 0.0, "end": 1.0}, {"start": 1.0, "end": 2.0}]

        with mock.patch("src.craig_pipeline.decode_audio_samples", return_value=samples):
            levels = calculate_segment_volume_levels("speaker.flac", segments, sample_rate=10)

        self.assertLess(levels[0], 0.0)
        self.assertGreater(levels[1], 0.0)
        self.assertTrue(all(-1.0 <= level <= 1.0 for level in levels))

    def test_build_craig_segments_applies_volume_font_scale(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            transcript_path = Path(temp_dir) / "1-speaker-a.json"
            _write_transcript(transcript_path, [{"start": 0.0, "end": 1.0, "text": "loud"}])
            with mock.patch("src.craig_pipeline.calculate_segment_volume_levels", return_value=list[float]([1.0])):
                segments = build_craig_segments_for_transcript("1-speaker-a.flac", str(transcript_path), {"speaker-a": "Oz"}, 0.0, 50, 20.0)

        self.assertAlmostEqual(coerce_float(segments[0]["subtitle_font_scale"]), 1.2)
        self.assertEqual(segments[0]["max_width"], 23)

    def test_normalize_db_threshold_rejects_invalid_value(self) -> None:
        with self.assertRaises(SystemExit):
            normalize_db_threshold("quiet")

    def test_merge_craig_transcripts_uses_segment_builder(self) -> None:
        import src.craig_pipeline as craig_pipeline

        def fake_builder(audio_path: str, _transcript_path: str, _styles: dict[str, str], _offset: float) -> list[dict[str, object]]:
            return [{"start": 0.0, "end": 1.0, "speaker": "Oz", "text": audio_path, "layout_row": 0, "filter_reasons": list[object](), "source_track": "craig:test", "max_width": 24}]

        with mock.patch.object(craig_pipeline, "build_craig_segments_for_transcript", side_effect=fake_builder):
            merged, filtered = craig_pipeline.merge_craig_transcripts({"a.aac": "a.json", "b.aac": "b.json"}, {"a": "Oz", "b": "A"}, 0.0)

        self.assertEqual(len(merged["segments"]), 2)
        self.assertEqual(len(filtered["segments"]), 0)

    def test_transcribe_audio_file_regenerates_legacy_cache(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / "1-speaker-a.json"
            transcript.write_text("{}", encoding="utf-8")
            with mock.patch("src.transcription_execution.run_command_with_utf8_log") as run:
                result = transcribe_audio_file("1-speaker-a.flac", temp_dir, skip_existing=True)
                cached = transcribe_audio_file("1-speaker-a.flac", temp_dir, skip_existing=True)
            run.assert_called_once()
            self.assertEqual(result, transcript)
            self.assertEqual(cached, transcript)

    def test_build_craig_segments_for_transcript_builds_shifted_segments(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            transcript_path = Path(temp_dir) / "1-speaker-a.json"
            _write_transcript(transcript_path, [{"start": 0.0, "end": 1.0, "text": "??!"}])
            segments = build_craig_segments_for_transcript(str(Path(temp_dir) / "1-speaker-a.flac"), str(transcript_path), {"speaker-a": "Oz"}, 1.25)
            self.assertEqual(len(segments), 1)
            self.assertEqual(segments[0]["speaker"], "Oz")
            self.assertEqual(segments[0]["source_file"], "1-speaker-a.flac")
            self.assertTrue(str(segments[0]["source_stream_id"]).startswith("audio-"))
            self.assertAlmostEqual(coerce_float(segments[0]["start"]), 1.25)

    def test_build_craig_segments_rejects_invalid_json_shapes(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        invalid_payloads: tuple[tuple[dict[str, object], str], ...] = (
            ({"segments": {}}, "transcript.segments must be an array"),
            ({"segments": [{"start": 0, "end": 1, "text": 3}]}, "text must be a string"),
            ({"segments": [{"start": 0, "end": 1, "text": "hello", "words": [None]}]}, "word must be an object"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript_path = Path(temp_dir) / "transcript.json"
            for payload, message in invalid_payloads:
                with self.subTest(message=message):
                    transcript_path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, message):
                        build_craig_segments_for_transcript(
                            "1-speaker-a.flac", str(transcript_path), {"speaker-a": "Oz"}, 0.0
                        )

    def test_build_craig_segments_uses_unique_source_ids_for_matching_file_names(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript_path = root / "transcript.json"
            _write_transcript(transcript_path, [{"start": 0.0, "end": 1.0, "text": "字幕"}])
            first = build_craig_segments_for_transcript(
                str(root / "a" / "1-speaker-a.flac"),
                str(transcript_path),
                {"speaker-a": "Oz"},
                0.0,
            )
            second = build_craig_segments_for_transcript(
                str(root / "b" / "1-speaker-a.flac"),
                str(transcript_path),
                {"speaker-a": "Oz"},
                0.0,
            )

            self.assertEqual(first[0]["source_file"], second[0]["source_file"])
            self.assertEqual(first[0]["source_track"], second[0]["source_track"])
            self.assertNotEqual(first[0]["source_stream_id"], second[0]["source_stream_id"])

    def test_resolve_alignment_honors_explicit_track(self) -> None:
        import src.craig_pipeline as craig_pipeline

        original_decode = craig_pipeline.decode_audio_samples
        try:
            def fake_decode(input_path: str, sample_rate: int = 120, stream_selector: str | None = None) -> np.ndarray:
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

    def test_run_alignment_stage_adjusts_offset(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "1-speaker-a.flac"
            audio.write_bytes(b"audio")
            with mock.patch("src.craig_pipeline.resolve_alignment", return_value=("0:a:2", 0.4, 0.77)) as resolve_alignment:
                alignment = run_alignment_stage(
                    video_path="video.mkv",
                    reference_audio=audio,
                    reference_track="0:a:2",
                    alignment_sample_rate=160,
                    alignment_offset_adjustment=0.25,
                )

            resolve_alignment.assert_called_once_with("video.mkv", str(audio), "0:a:2", 160)
            self.assertEqual(
                alignment,
                AlignmentResult(
                    matched_track="0:a:2",
                    offset_seconds=0.65,
                    score=0.77,
                    reference_audio=str(audio),
                ),
            )

    def test_run_refine_stage_returns_refinement_dataclass(self) -> None:
        merged = [{"start": 0.0, "end": 1.0, "text": "a", "speaker": "Oz", "layout_row": 0}]
        filtered = [{"start": 2.0, "end": 3.0, "text": "b", "speaker": "Oz", "layout_row": 0}]
        with mock.patch("src.craig_pipeline.refine_segments", return_value=(merged, filtered)) as refine_segments:
            result = run_refine_stage(
                [{"start": 0.0, "end": 1.0}],
                subtitle_max_gap_seconds=0.2,
                subtitle_end_padding_seconds=0.05,
                subtitle_min_duration_seconds=0.5,
            )

        refine_segments.assert_called_once()
        self.assertEqual(result, SegmentRefinementResult(merged_segments=merged, filtered_segments=filtered))

    def test_run_transcription_stage_delegates_to_batch_transcriber(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "1-speaker-a.flac"
            audio.write_bytes(b"audio")
            expected = CraigTranscriptionBatch(
                {str(audio): str(Path(temp_dir) / "transcript.json")},
                [{"start": 0.1, "end": 0.6, "text": "hello", "speaker": "Oz"}],
            )
            with mock.patch("src.craig_pipeline.transcribe_craig_audio_files", return_value=expected) as transcribe:
                result = run_transcription_stage(
                    audio_files=[audio],
                    output_dir=Path(temp_dir),
                    style_map={"speaker-a": "Oz"},
                    offset_seconds=0.25,
                    model="large-v3",
                    device="cpu",
                    compute_type="int8",
                    language="ja",
                    vad_onset=0.35,
                    vad_offset=0.2,
                    skip_existing_transcripts=True,
                    postprocess_workers=1,
                    subtitle_font_size=50,
                    subtitle_volume_scale_percent=20.0,
                )

            transcribe.assert_called_once()
            self.assertEqual(result.transcript_map, expected.transcript_map)
            self.assertEqual(result.segments, expected.segments)

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

    def test_reference_fft_is_reused_across_candidate_tracks(self) -> None:
        reference = np.array([1.0, 0.2, 0.0], dtype=np.float32)
        candidate = np.array([0.0, 1.0, 0.2, 0.0], dtype=np.float32)

        def fake_decode(_path: str, sample_rate: int = 120, stream_selector: str | None = None) -> np.ndarray:
            return candidate if stream_selector else reference

        with (
            mock.patch("src.craig_pipeline.probe_audio_streams", return_value=list[dict[str, object]]([{}, {}])),
            mock.patch("src.craig_pipeline.decode_audio_samples", side_effect=fake_decode),
            mock.patch("src.craig_pipeline.np.fft.rfft", wraps=np.fft.rfft) as rfft,
        ):
            matched, _offset, _score = find_best_reference_track("video.mkv", "reference.flac")

        self.assertEqual(matched, "0:a:0")
        self.assertEqual(rfft.call_count, 3)

    def test_shared_transcription_batch_preserves_audio_order(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio_files = [root / "1-a.flac", root / "2-b.flac"]
            for audio in audio_files:
                audio.write_bytes(b"audio")

            def fake_transcribe(audio_path: str | Path, output_dir: str | Path, **_kwargs: object) -> Path:
                path = Path(output_dir) / f"{Path(audio_path).stem}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")
                return path

            def fake_build(audio_path: str | Path, _transcript: object, _styles: object, _offset: object, *_args: object) -> list[dict[str, object]]:
                return [{"id": Path(audio_path).stem, "text": Path(audio_path).name}]

            with (
                mock.patch("src.craig_pipeline.transcribe_audio_file", side_effect=fake_transcribe),
                mock.patch("src.craig_pipeline.build_craig_segments_for_transcript", side_effect=fake_build),
            ):
                result = transcribe_craig_audio_files(
                    audio_files,
                    root / "transcripts",
                    {"a": "Oz", "b": "A"},
                    0.25,
                    postprocess_workers=2,
                )

        self.assertEqual([item["id"] for item in result.segments], ["1-a", "2-b"])
        self.assertEqual(list(result.transcript_map), [str(path.resolve()) for path in audio_files])

    def test_shift_segment_applies_offset_to_segment_and_words(self) -> None:
        shifted = shift_segment(
            {
                "start": 1.0,
                "end": 2.0,
                "words": [{"word": "hi", "start": 1.1, "end": 1.4}],
            },
            0.5,
        )
        if shifted is None:
            raise AssertionError("expected a shifted segment")
        self.assertAlmostEqual(coerce_float(shifted["start"]), 1.5)
        self.assertAlmostEqual(coerce_float(entries(shifted, "words")[0]["start"]), 1.6)

    def test_merge_craig_transcripts_applies_style_map_and_offset(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            transcript_path = Path(temp_dir) / "1-speaker-a.json"
            _write_transcript(transcript_path, [{
                "start": 0.0,
                "end": 1.0,
                "text": "こんにちは!",
                "words": [{"word": "こんにちは!", "start": 0.0, "end": 1.0}],
            }])
            merged, filtered = merge_craig_transcripts(
                {str(Path(temp_dir) / "1-speaker-a.aac"): str(transcript_path)},
                {"speaker-a": "Oz"},
                0.75,
            )
            self.assertEqual(len(filtered["segments"]), 0)
            self.assertEqual(merged["segments"][0]["speaker"], "Oz")
            self.assertAlmostEqual(coerce_float(merged["segments"][0]["start"]), 0.75)

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

            self.assertEqual(_required_path(resolved_video), video)
            self.assertEqual(_required_path(resolved_audio_dir), audio_dir)
            self.assertEqual(_required_path(resolved_output_dir), root / "video_export" / "game_session_01")

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

            self.assertEqual(_required_path(resolved_output_dir), explicit_output)

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
