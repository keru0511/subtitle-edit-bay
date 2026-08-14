import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from src.craig_pipeline import CraigTranscriptionBatch
from src.gui_state import build_gui_render_command, build_gui_transcribe_command
from src.subtitle_project import (
    SubtitleProjectError,
    AudioMix,
    AudioMixChannel,
    SpeakerInfo,
    create_project,
    derive_ass_path,
    derive_project_path,
    derive_render_path,
    load_project,
    load_project_model,
    project_to_transcript,
    project_to_view_payload,
    save_project_model,
    save_project,
    SubtitleProject,
    SubtitleSegment,
    WaveformInfo,
    waveform_peaks_from_samples,
)
from src.subtitle_workflow import (
    SubtitleAlignmentResult,
    SubtitlePipelineInputs,
    SubtitleRefineResult,
    build_project_stage,
    resolve_subtitle_inputs,
    run_subtitle_alignment_stage,
    run_subtitle_refine_stage,
    build_project_ass,
    render_project_video,
    transcribe_to_project,
)


class SubtitleProjectTests(unittest.TestCase):
    def test_subtitle_segment_model_round_trip(self) -> None:
        model = SubtitleSegment.from_json(
            {
                "id": "seg-1",
                "start": 0.1,
                "end": 0.9,
                "text": " hello ",
                "speaker": "Oz",
                "source_speaker": "alice",
            },
            index=0,
        )
        restored = SubtitleSegment.from_json(model.to_json(), index=0)
        self.assertEqual(restored.to_json(), model.to_json())

    def test_speaker_and_waveform_models_round_trip(self) -> None:
        speaker = SpeakerInfo.from_json(
            {"name": "alice", "style": "Speaker_Alice", "track_key": "craig:alice", "file_name": "1-alice.flac", "path": "/tmp/1-alice.flac", "color": "#445566"}
        )
        self.assertEqual(
            speaker.to_json(),
            {"name": "alice", "style": "Speaker_Alice", "track_key": "craig:alice", "file_name": "1-alice.flac", "path": "/tmp/1-alice.flac", "color": "#445566"},
        )
        waveform = WaveformInfo.from_json(
            {"speaker": "Oz", "style": "Oz", "color": "#445566", "source_path": "/tmp/audio.wav", "offset_seconds": 0.2, "duration_seconds": 1.5, "sample_rate": 400, "peaks": [0.1, 0.2]}
        )
        self.assertEqual(waveform.source_path, "/tmp/audio.wav")
        self.assertEqual(waveform.peaks, [0.1, 0.2])

    def test_audio_mix_model_round_trip(self) -> None:
        mix = AudioMix.from_json(
            {
                "version": 1,
                "customized": True,
                "channels": [
                    {"id": "video:0:a:0", "kind": "video", "label": "0:a:0", "selector": "0:a:0", "enabled": True, "muted": False, "solo": False, "volume_percent": 100.0}
                ],
            }
        )
        payload = mix.to_json()
        self.assertEqual(payload["version"], 1)
        self.assertEqual(len(payload["channels"]), 1)
        self.assertIsInstance(AudioMixChannel.from_json(payload["channels"][0]), AudioMixChannel)

    def test_project_model_parses_and_round_trips_payload(self) -> None:
        payload = {
            "schema_version": 1,
            "project_type": "subtitle-edit-project",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:01",
            "video": {"path": "video.mkv"},
            "output_dir": "/tmp/out",
            "audio_sources": [{"name": "alice", "style": "Speaker_Alice", "track_key": "craig:alice", "file_name": "1-alice.flac", "path": "/tmp/1-alice.flac", "color": "#445566"}],
            "speakers": [{"name": "alice", "style": "Speaker_Alice", "track_key": "craig:alice", "file_name": "1-alice.flac", "path": "/tmp/1-alice.flac", "color": "#445566"}],
            "waveforms": [{"speaker": "Oz", "style": "Oz", "color": "#445566", "source_path": "/tmp/audio.wav", "offset_seconds": 0.2, "duration_seconds": 1.5, "sample_rate": 400, "peaks": [0.1]}],
            "subtitle_settings": {"font_size": 64, "outline_color": "#000000", "outline_thickness": 3},
            "render_settings": {},
            "transcription": {},
            "transcription_context": {},
            "segments": [{"start": 0.0, "end": 1.0, "text": "hi", "speaker": "Oz"}],
            "audio_mix": {"version": 1, "customized": False, "channels": []},
        }
        model = SubtitleProject.from_json(payload)
        round_trip = model.to_json()
        self.assertEqual(round_trip["video"]["path"], "video.mkv")
        self.assertEqual(round_trip["segments"][0]["id"], "subtitle-000001")
        self.assertIsInstance(SubtitleProject.from_json(round_trip), SubtitleProject)

    def test_model_persistence_and_view_payload_keep_boundaries_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_path = root / "game.subtitle-project.json"
            project = create_project(
                video_path="video.mkv",
                output_dir=root,
                segments=[{"start": 0.0, "end": 1.0, "text": "hello", "speaker": "Oz", "view_only": "kept"}],
            )
            save_project_model(project_path, SubtitleProject.from_json(project))
            model = load_project_model(project_path)
            view = project_to_view_payload(model)

        self.assertIsInstance(model, SubtitleProject)
        self.assertEqual(model.segments[0].extras["view_only"], "kept")
        self.assertNotIn("updated_at", view)
        self.assertEqual(view["segments"][0]["text"], "hello")

    def test_create_project_normalizes_editable_segments(self) -> None:
        project = create_project(
            video_path="video.mkv",
            output_dir="out",
            segments=[
                {"start": 2, "end": 1, "text": " hello ", "speaker": "A", "subtitle_font_family": "Yu Mincho"},
                {"id": "kept", "start": 0, "end": 1, "text": "first", "speaker": "Oz"},
            ],
        )

        self.assertEqual([item["id"] for item in project["segments"]], ["kept", "subtitle-000001"])
        self.assertEqual(project["segments"][1]["text"], "hello")
        self.assertEqual(project["segments"][1]["subtitle_font_family"], "Yu Mincho")
        self.assertGreater(project["segments"][1]["end"], project["segments"][1]["start"])
        self.assertTrue(all(item["layout_packed"] for item in project["segments"]))
        self.assertEqual(project["subtitle_settings"]["outline_color"], "#000000")
        self.assertEqual(project["subtitle_settings"]["outline_thickness"], 3)
        self.assertFalse(project["audio_mix"]["customized"])
        self.assertEqual(project["audio_mix"]["channels"][0]["selector"], "0:a:0")

    def test_save_load_and_transcript_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            video.write_bytes(b"video")
            project = create_project(
                video_path=video,
                output_dir=root,
                segments=[{"start": 0, "end": 1, "text": "字幕", "speaker": "Oz"}],
            )
            path = root / "game.subtitle-project.json"
            save_project(path, project)
            loaded = load_project(path)

            self.assertEqual(loaded["project_type"], "subtitle-edit-project")
            self.assertEqual(project_to_transcript(loaded)["segments"][0]["text"], "字幕")
            self.assertFalse((root / ".game.subtitle-project.json.tmp").exists())

    def test_rejects_unknown_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "future.subtitle-project.json"
            project = create_project(video_path="video.mkv", output_dir=temp_dir, segments=[])
            project["schema_version"] = 999
            path.write_text(json.dumps(project), encoding="utf-8")
            with self.assertRaises(SubtitleProjectError):
                load_project(path)

    def test_waveform_peaks_are_bounded_and_downsampled(self) -> None:
        samples = np.asarray([0.0, -0.5, 1.0, -0.25, 0.1, 0.8], dtype=np.float32)
        peaks = waveform_peaks_from_samples(samples, bins=3)

        self.assertEqual(len(peaks), 3)
        self.assertTrue(all(0.0 <= value <= 1.0 for value in peaks))
        self.assertGreater(max(peaks), 0.9)

    def test_derived_output_names_are_stable(self) -> None:
        project = derive_project_path("recording.mkv", "export")
        self.assertEqual(project.name, "recording.subtitle-project.json")
        self.assertEqual(derive_ass_path(project).name, "recording.edited.ass")
        self.assertEqual(derive_render_path(project).name, "recording.edited.subtitled.mp4")


class SubtitleWorkflowTests(unittest.TestCase):
    def test_transcribe_phase_creates_project_without_rendering_video(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            audio = root / "1-alice.flac"
            video.write_bytes(b"video")
            audio.write_bytes(b"audio")

            fake_segment = {
                "start": 0.25,
                "end": 1.25,
                "text": "hi",
                "speaker": "Oz",
                "source_speaker": "alice",
                "source_track": "craig:alice",
                "source_file": audio.name,
                "layout_packed": True,
            }
            transcript_path = root / "export" / "transcripts" / "1-alice.json"
            transcription = CraigTranscriptionBatch(
                {str(audio.resolve()): str(transcript_path.resolve())},
                [fake_segment],
            )
            with (
                patch("src.subtitle_workflow.resolve_alignment", return_value=("0:a:0", 0.25, 0.9)),
                patch("src.subtitle_workflow.transcribe_craig_audio_files", return_value=transcription),
                patch("src.subtitle_workflow.refine_segments", return_value=([fake_segment], [])),
                patch("src.subtitle_workflow._build_waveforms", return_value=[]),
                patch("src.subtitle_workflow.probe_media_duration", return_value=30.0),
                patch("src.subtitle_workflow.run_ffmpeg_burn") as burn,
            ):
                project_path = transcribe_to_project(
                    video_path=str(video),
                    audio_files=[str(audio)],
                    output_dir=str(root / "export"),
                    reference_audio=str(audio),
                )

            project = load_project(project_path)
            self.assertEqual(project["transcription"]["offset_seconds"], 0.25)
            self.assertEqual(project["segments"][0]["text"], "hi")
            self.assertFalse(burn.called)

    def test_resolve_subtitle_inputs_sets_reference_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            audio_one = root / "1-alice.flac"
            audio_two = root / "2-bob.flac"
            video.write_bytes(b"video")
            audio_one.write_bytes(b"audio")
            audio_two.write_bytes(b"audio")

            inputs = resolve_subtitle_inputs(
                video_path=str(video),
                audio_files=[str(audio_two), str(audio_one)],
                output_dir=str(root / "export"),
            )

            self.assertEqual(inputs.reference_path, audio_one.resolve())
            self.assertEqual(inputs.project_path, derive_project_path(str(video), inputs.output_dir))
            self.assertEqual(len(inputs.audio_files), 2)

    def test_alignment_stage_isolated(self) -> None:
        with patch("src.subtitle_workflow.resolve_alignment", return_value=("0:a:1", 0.5, 0.91)) as resolve_alignment:
            alignment = run_subtitle_alignment_stage(
                "video.mkv",
                Path("1-alice.flac"),
                "0:a:1",
                120,
            )

        resolve_alignment.assert_called_once_with("video.mkv", "1-alice.flac", "0:a:1", 120)
        self.assertEqual(alignment, SubtitleAlignmentResult("0:a:1", 0.5, 0.91, "1-alice.flac"))

    def test_refine_stage_isolated(self) -> None:
        merged = [{"start": 0.0, "end": 1.0, "text": "a", "speaker": "Oz", "layout_row": 0}]
        filtered = [{"start": 1.0, "end": 1.2, "text": "x", "speaker": "Oz", "layout_row": 0}]
        with patch("src.subtitle_workflow.refine_segments", return_value=(merged, filtered)) as refine_segments:
            result = run_subtitle_refine_stage(
                [{"start": 0.0, "end": 1.0}],
                subtitle_max_gap_seconds=0.2,
                subtitle_end_padding_seconds=0.1,
                subtitle_min_duration_seconds=0.5,
            )

        refine_segments.assert_called_once()
        self.assertEqual(result, SubtitleRefineResult(merged_segments=merged, filtered_segments=filtered))

    def test_project_stage_persists_subtitle_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            audio = root / "1-alice.flac"
            video.write_bytes(b"video")
            audio.write_bytes(b"audio")

            inputs = resolve_subtitle_inputs(
                video_path=str(video),
                audio_files=[str(audio)],
                output_dir=str(root / "export"),
            )
            project_inputs = SubtitlePipelineInputs(
                video_path=inputs.video_path,
                output_dir=inputs.output_dir,
                project_path=inputs.project_path,
                reference_path=inputs.reference_path,
                audio_files=inputs.audio_files,
                style_map=inputs.style_map,
                speakers=inputs.speakers,
            )
            alignment = SubtitleAlignmentResult(
                matched_track="0:a:0",
                offset_seconds=0.5,
                score=1.0,
                reference_audio=str(audio),
            )
            refine_result = SubtitleRefineResult(
                merged_segments=[{"start": 0.0, "end": 1.0, "text": "hi", "speaker": "Oz", "source_speaker": "alice", "source_track": "craig:alice", "source_file": audio.name, "layout_row": 0}],
                filtered_segments=[],
            )
            with (
                patch("src.subtitle_workflow.probe_audio_streams", return_value=[]),
                patch("src.subtitle_workflow.video_track_entries", return_value=[]),
            ):
                project_result = build_project_stage(
                    inputs=project_inputs,
                    alignment=alignment,
                    transcript_map={str(audio): "transcript.json"},
                    refine_result=refine_result,
                    waveforms=[],
                    model="large-v3",
                    device="cpu",
                    compute_type="int8",
                    language="ja",
                    subtitle_font_size=50,
                    subtitle_outline_color="#000000",
                    subtitle_outline_thickness=3,
                    subtitle_max_gap_seconds=0.32,
                    subtitle_end_padding_seconds=0.08,
                    subtitle_min_duration_seconds=0.35,
                    volume_scale_percent=20.0,
                    duration_seconds=10.0,
                )

            project_path = project_result.project_path
            project = load_project(project_path)

            self.assertEqual(project_path, inputs.project_path)
            self.assertTrue(Path(project["transcription"]["merged_json"]).exists())
            self.assertTrue(Path(project["transcription"]["filtered_json"]).exists())
            self.assertTrue(project_path.exists())
            self.assertEqual(project_result.merged_path.name, "game.craig.merged.json")
            self.assertEqual(project_result.filtered_path.name, "game.craig.filtered.json")

    def test_build_ass_uses_canonical_project_segments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            video.write_bytes(b"video")
            project = create_project(
                video_path=video,
                output_dir=root,
                segments=[{"start": 1, "end": 2, "text": "edited", "speaker": "Oz"}],
                subtitle_settings={
                    "font_size": 64,
                    "outline_color": "#123456",
                    "outline_thickness": 6,
                },
            )
            project_path = root / "game.subtitle-project.json"
            save_project(project_path, project)

            def fake_build(payload, ass_path, **kwargs):
                self.assertEqual(payload["segments"][0]["text"], "edited")
                self.assertEqual(kwargs["subtitle_font_size"], 64)
                self.assertEqual(kwargs["subtitle_outline_color"], "#123456")
                self.assertEqual(kwargs["subtitle_outline_thickness"], 6)
                Path(ass_path).write_text("ASS", encoding="utf-8")
                return Path(ass_path)

            with patch("src.subtitle_workflow.build_ass_from_data", side_effect=fake_build):
                result = build_project_ass(project_path)

            self.assertEqual(result.read_text(encoding="utf-8"), "ASS")
            self.assertFalse(any(root.glob(".*.render.json")))

    def test_render_phase_burns_existing_project_without_transcription(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            video.write_bytes(b"video")
            project = create_project(
                video_path=video,
                output_dir=root,
                segments=[{"start": 0, "end": 1, "text": "edited", "speaker": "Oz"}],
            )
            project_path = root / "game.subtitle-project.json"
            save_project(project_path, project)
            ass_path = root / "game.edited.ass"
            ass_path.write_text("ASS", encoding="utf-8")

            with (
                patch("src.subtitle_workflow.build_project_ass", return_value=ass_path),
                patch("src.subtitle_workflow.run_ffmpeg_burn") as burn,
                patch("src.subtitle_workflow.transcribe_craig_audio_files") as transcribe,
            ):
                output = render_project_video(project_path, audio_normalize=False)

            self.assertEqual(output.name, "game.edited.subtitled.mp4")
            burn.assert_called_once()
            self.assertFalse(transcribe.called)
            self.assertEqual(load_project(project_path)["render_settings"]["last_output"], str(output.resolve()))

    def test_render_phase_defaults_to_external_audio_when_video_track_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            video.write_bytes(b"video")
            audio = root / "speaker.wav"
            audio.write_bytes(b"audio")
            project = create_project(
                video_path=video,
                output_dir=root,
                audio_sources=[{"path": str(audio)}],
                segments=[{"start": 0, "end": 1, "text": "edited", "speaker": "Oz"}],
            )
            project_path = root / "game.subtitle-project.json"
            save_project(project_path, project)
            ass_path = root / "game.edited.ass"
            ass_path.write_text("ASS", encoding="utf-8")

            with (
                patch("src.subtitle_workflow.build_project_ass", return_value=ass_path),
                patch("src.subtitle_workflow.run_ffmpeg_burn") as burn,
            ):
                render_project_video(project_path, audio_normalize=False)

            args = burn.call_args.kwargs
            audio_mix = args["audio_mix"]
            self.assertIsNotNone(audio_mix)
            self.assertTrue(
                any(
                    channel.get("kind") == "external" and channel.get("enabled")
                    for channel in audio_mix.get("channels", [])
                )
            )
            self.assertEqual(args["audio_codec"], "aac")

    def test_render_phase_passes_custom_audio_mix_and_sync_offset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            video.write_bytes(b"video")
            project = create_project(
                video_path=video,
                output_dir=root,
                segments=[{"start": 0, "end": 1, "text": "edited", "speaker": "Oz"}],
                transcription={"offset_seconds": 0.25},
                audio_mix={
                    "customized": True,
                    "channels": [{
                        "id": "video:0:a:1",
                        "kind": "video",
                        "selector": "0:a:1",
                        "label": "voice",
                        "enabled": True,
                        "muted": False,
                        "solo": False,
                        "volume_percent": 80,
                    }],
                },
            )
            project_path = root / "game.subtitle-project.json"
            save_project(project_path, project)
            ass_path = root / "game.edited.ass"
            ass_path.write_text("ASS", encoding="utf-8")

            with (
                patch("src.subtitle_workflow.build_project_ass", return_value=ass_path),
                patch("src.subtitle_workflow.run_ffmpeg_burn") as burn,
            ):
                render_project_video(project_path, audio_normalize=False)

            kwargs = burn.call_args.kwargs
            self.assertTrue(kwargs["audio_mix"]["customized"])
            self.assertEqual(kwargs["audio_mix"]["channels"][0]["selector"], "0:a:1")
            self.assertEqual(kwargs["audio_offset_seconds"], 0.25)
            self.assertEqual(kwargs["audio_codec"], "aac")

    def test_render_phase_converts_copy_audio_codec_to_aac_for_mp4_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            video.write_bytes(b"video")
            project = create_project(
                video_path=video,
                output_dir=root,
                segments=[{"start": 0, "end": 1, "text": "edited", "speaker": "Oz"}],
            )
            project_path = root / "game.subtitle-project.json"
            save_project(project_path, project)
            ass_path = root / "game.edited.ass"
            ass_path.write_text("ASS", encoding="utf-8")

            with (
                patch("src.subtitle_workflow.build_project_ass", return_value=ass_path),
                patch("src.subtitle_workflow.run_ffmpeg_burn") as burn,
            ):
                render_project_video(project_path, audio_normalize=False, audio_codec="copy")

            self.assertEqual(burn.call_args.kwargs["audio_codec"], "aac")
            self.assertTrue(str(burn.call_args.args[2]).endswith(".mp4"))

    def test_render_phase_keeps_copy_audio_codec_for_non_mp4_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            video.write_bytes(b"video")
            project = create_project(
                video_path=video,
                output_dir=root,
                segments=[{"start": 0, "end": 1, "text": "edited", "speaker": "Oz"}],
            )
            project_path = root / "game.subtitle-project.json"
            save_project(project_path, project)
            ass_path = root / "game.edited.ass"
            ass_path.write_text("ASS", encoding="utf-8")
            output_path = root / "game.subtitled.mkv"

            with (
                patch("src.subtitle_workflow.build_project_ass", return_value=ass_path),
                patch("src.subtitle_workflow.run_ffmpeg_burn") as burn,
            ):
                render_project_video(project_path, output_path=output_path, audio_normalize=False, audio_codec="copy")

            self.assertEqual(burn.call_args.kwargs["audio_codec"], "copy")
            self.assertTrue(str(burn.call_args.args[2]).endswith(".mkv"))

    def test_render_reuses_loaded_project_for_ass_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            video.write_bytes(b"video")
            project = create_project(
                video_path=video,
                output_dir=root,
                segments=[{"start": 0, "end": 1, "text": "edited", "speaker": "Oz"}],
            )
            project_path = root / "game.subtitle-project.json"
            save_project(project_path, project)

            def fake_build(_transcript, ass_path, **_kwargs):
                Path(ass_path).write_text("ASS", encoding="utf-8")
                return Path(ass_path)

            with (
                patch("src.subtitle_workflow.load_project", wraps=load_project) as load,
                patch("src.subtitle_workflow.build_ass_from_data", side_effect=fake_build),
                patch("src.subtitle_workflow.run_ffmpeg_burn"),
            ):
                render_project_video(project_path, audio_normalize=False)

            self.assertEqual(load.call_count, 1)


    def test_gui_phase_commands_are_independent(self) -> None:
        transcribe = build_gui_transcribe_command(
            "config.json",
            video="game.mkv",
            audio_files=["1-a.flac"],
            output_dir="out",
        )
        render = build_gui_render_command("config.json", project_path="out/game.subtitle-project.json")

        self.assertIn("transcribe", transcribe)
        self.assertNotIn("render", transcribe)
        self.assertIn("render", render)
        self.assertNotIn("--audio-file", render)


if __name__ == "__main__":
    unittest.main()
