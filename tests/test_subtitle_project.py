import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from src.gui_state import build_gui_render_command, build_gui_transcribe_command
from src.subtitle_project import (
    SubtitleProjectError,
    create_project,
    derive_ass_path,
    derive_project_path,
    derive_render_path,
    load_project,
    project_to_transcript,
    save_project,
    waveform_peaks_from_samples,
)
from src.subtitle_workflow import build_project_ass, render_project_video, transcribe_to_project


class SubtitleProjectTests(unittest.TestCase):
    def test_create_project_normalizes_editable_segments(self) -> None:
        project = create_project(
            video_path="video.mkv",
            output_dir="out",
            segments=[
                {"start": 2, "end": 1, "text": " hello ", "speaker": "A"},
                {"id": "kept", "start": 0, "end": 1, "text": "first", "speaker": "Oz"},
            ],
        )

        self.assertEqual([item["id"] for item in project["segments"]], ["kept", "subtitle-000001"])
        self.assertEqual(project["segments"][1]["text"], "hello")
        self.assertGreater(project["segments"][1]["end"], project["segments"][1]["start"])
        self.assertTrue(all(item["layout_packed"] for item in project["segments"]))

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

            def fake_transcribe(_audio, output_dir, **_kwargs):
                path = Path(output_dir) / "1-alice.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"segments": [{"start": 0, "end": 1, "text": "hi"}]}), encoding="utf-8")
                return path

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
            with (
                patch("src.subtitle_workflow.resolve_alignment", return_value=("0:a:0", 0.25, 0.9)),
                patch("src.subtitle_workflow.transcribe_audio_file", side_effect=fake_transcribe),
                patch("src.subtitle_workflow.build_craig_segments_for_transcript", return_value=[fake_segment]),
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

    def test_build_ass_uses_canonical_project_segments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            video.write_bytes(b"video")
            project = create_project(
                video_path=video,
                output_dir=root,
                segments=[{"start": 1, "end": 2, "text": "edited", "speaker": "Oz"}],
                subtitle_settings={"font_size": 64},
            )
            project_path = root / "game.subtitle-project.json"
            save_project(project_path, project)

            def fake_build(transcript_path, ass_path, **kwargs):
                payload = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
                self.assertEqual(payload["segments"][0]["text"], "edited")
                self.assertEqual(kwargs["subtitle_font_size"], 64)
                Path(ass_path).write_text("ASS", encoding="utf-8")
                return Path(ass_path)

            with patch("src.subtitle_workflow.build_ass_from_transcript", side_effect=fake_build):
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
                patch("src.subtitle_workflow.transcribe_audio_file") as transcribe,
            ):
                output = render_project_video(project_path, audio_normalize=False)

            self.assertEqual(output.name, "game.edited.subtitled.mp4")
            burn.assert_called_once()
            self.assertFalse(transcribe.called)
            self.assertEqual(load_project(project_path)["render_settings"]["last_output"], str(output.resolve()))

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


@unittest.skipUnless(os.environ.get("RUN_QT_GUI_TESTS") == "1", "set RUN_QT_GUI_TESTS=1 for Qt backend history test")
class SubtitleEditorBackendTests(unittest.TestCase):
    def test_edit_undo_redo_and_autosave(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from src.gui import EditBayBackend

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            video.write_bytes(b"video")
            project = create_project(
                video_path=video,
                output_dir=root,
                segments=[{"start": 0, "end": 1, "text": "before", "speaker": "Oz"}],
            )
            path = root / "game.subtitle-project.json"
            save_project(path, project)
            app = EditBayBackend([], workspace_root=root)
            self.assertTrue(app._load_project_path(path, update_sources=False))

            app.updateSegment(0, {"text": "after"})
            self.assertEqual(app.subtitleSegments[0]["text"], "after")
            app.undoSubtitleEdit()
            self.assertEqual(app.subtitleSegments[0]["text"], "before")
            app.redoSubtitleEdit()
            self.assertEqual(app.subtitleSegments[0]["text"], "after")
            app.saveProject()
            self.assertEqual(load_project(path)["segments"][0]["text"], "after")
            app._shutdown_executor()


if __name__ == "__main__":
    unittest.main()
