import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.craig_pipeline import CraigTranscriptionBatch
from src.subtitle_workflow import (
    SubtitleAlignmentStage,
    SubtitleRefineStage,
    SubtitleTranscriptionStage,
    SubtitleWorkflowInputStage,
    _refine_stage,
    _resolve_transcription_inputs,
    _transcription_stage,
    transcribe_to_project,
)


class SubtitleWorkflowStageTests(unittest.TestCase):
    def test_resolve_transcription_inputs_builds_stage_payload(self) -> None:
        with TemporaryDirectory() as temp_dir:
            audio_dir = Path(temp_dir)
            audio = audio_dir / "1-alice.flac"
            audio.write_bytes(b"audio")
            output = audio_dir / "output"
            output.mkdir()
            stage = _resolve_transcription_inputs(
                video_path=audio_dir / "game.mp4",
                audio_files=[str(audio)],
                output_dir=str(output),
                reference_audio=None,
                overwrite_project=True,
                track_color_map=None,
            )

        self.assertEqual(stage.output, output)
        self.assertEqual(stage.audio_files[0].name, "1-alice.flac")
        self.assertEqual(stage.reference_audio.name, "1-alice.flac")
        self.assertEqual(stage.style_map["alice"], "Oz")
        self.assertEqual(stage.speakers[0]["track_key"], "craig:alice")

    def test_transcription_stage_wraps_transcript_batch(self) -> None:
        batch = CraigTranscriptionBatch({"1": "tmp.json"}, [{"text": "x"}])
        with patch("src.subtitle_workflow.transcribe_craig_audio_files", return_value=batch):
            result = _transcription_stage(
                [Path("1-a.flac")],
                Path("transcripts"),
                {"a": "Oz"},
                0.5,
                model="large-v3",
                device="cpu",
                compute_type="int8",
                language="ja",
                vad_onset=0.2,
                vad_offset=0.1,
                skip_existing_transcripts=True,
                postprocess_workers=2,
                subtitle_font_size=40,
                subtitle_volume_scale_percent=60,
            )

        self.assertIsInstance(result, SubtitleTranscriptionStage)
        self.assertEqual(result.transcript_map, {"1": "tmp.json"})
        self.assertEqual(result.segments, [{"text": "x"}])

    def test_refine_stage_wraps_segment_output(self) -> None:
        result = _refine_stage(
            [{"start": 0.0, "end": 1.0, "text": "hello", "source_track": "craig:alice", "speaker": "alice"}],
            subtitle_max_gap_seconds=0.75,
            subtitle_end_padding_seconds=0.2,
            subtitle_min_duration_seconds=0.1,
        )

        self.assertIsInstance(result, SubtitleRefineStage)
        self.assertIsInstance(result.merged, list)
        self.assertIsInstance(result.filtered, list)

    def test_transcribe_to_project_uses_staged_outputs(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            video = temp_root / "game.mp4"
            audio = temp_root / "1-alice.flac"
            video.write_bytes(b"video")
            audio.write_bytes(b"audio")
            output = temp_root / "export"
            output.mkdir()

            inputs = SubtitleWorkflowInputStage(
                output=output,
                project_path=output / "game.subtitle-project.json",
                audio_files=(audio,),
                reference_audio=audio,
                style_map={"alice": "Oz"},
                speakers=[
                    {
                        "name": "alice",
                        "style": "Oz",
                        "track_key": "craig:alice",
                        "file_name": "1-alice.flac",
                        "path": str(audio.resolve()),
                        "color": "#FFFFFF",
                    }
                ],
            )
            alignment = SubtitleAlignmentStage(
                matched_track="0:a:0",
                offset_seconds=0.25,
                alignment_score=0.95,
            )
            transcription = SubtitleTranscriptionStage(
                transcript_map={"audio": "tmp.json"},
                segments=[{"start": 0.0, "end": 1.0}],
            )
            refined = SubtitleRefineStage(
                merged=[{"start": 0.0, "end": 1.0}],
                filtered=[],
            )

            with (
                patch("src.subtitle_workflow._resolve_transcription_inputs", return_value=inputs),
                patch("src.subtitle_workflow._alignment_stage", return_value=alignment),
                patch("src.subtitle_workflow._transcription_stage", return_value=transcription),
                patch("src.subtitle_workflow._refine_stage", return_value=refined),
                patch("src.subtitle_workflow._build_waveforms", return_value=[]),
                patch("src.subtitle_workflow.create_project") as create_project,
                patch("src.subtitle_workflow.video_track_entries", return_value=[]),
                patch("src.subtitle_workflow.probe_audio_streams", return_value=[]),
                patch("src.subtitle_workflow.reconcile_audio_mix"),
                patch("src.subtitle_workflow.save_project"),
                patch("src.subtitle_workflow.probe_media_duration", return_value=10.0),
            ):
                create_project.return_value = {"transcription": {}, "subtitle_settings": {}}
                result = transcribe_to_project(
                    video_path=str(video),
                    audio_files=[str(audio)],
                    output_dir=str(output),
                    overwrite_project=True,
                )

        self.assertEqual(result, output / "game.subtitle-project.json")
        create_project.assert_called_once()


if __name__ == "__main__":
    unittest.main()
