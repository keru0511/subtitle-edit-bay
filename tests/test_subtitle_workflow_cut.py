import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from src.subtitle_project import create_project, save_project
from src.subtitle_workflow import render_project_video


class SubtitleWorkflowCutTests(unittest.TestCase):
    def _build_stub_project(self, root: Path, name: str) -> tuple[Path, Path]:
        root.mkdir(parents=True, exist_ok=True)
        video = root / f"{name}.mkv"
        audio = root / "1-alice.flac"
        second_audio = root / "2-bob.flac"
        video.write_bytes(b"video")
        audio.write_bytes(b"audio")
        second_audio.write_bytes(b"audio")
        speaker = {
            "name": "alice",
            "style": "Oz",
            "track_key": "craig:alice",
            "color": "#FFD966",
            "file_name": audio.name,
            "path": str(audio),
        }
        second_speaker = {
            **speaker,
            "name": "bob",
            "style": "A",
            "track_key": "craig:bob",
            "color": "#6FA8DC",
            "file_name": second_audio.name,
            "path": str(second_audio),
        }
        project = create_project(
            video_path=video,
            output_dir=root,
            duration_seconds=4,
            audio_sources=[speaker, second_speaker],
            speakers=[speaker, second_speaker],
            segments=[{
                "start": 0.25,
                "end": 0.75,
                "text": "edited",
                "speaker": "Oz",
                "source_track": "craig:alice",
            }],
            subtitle_settings={
                "font_size": 72,
                "outline_color": "#234567",
                "outline_thickness": 5,
                "max_gap_seconds": 0.2,
                "end_padding_seconds": 0.04,
                "min_duration_seconds": 0.3,
            },
            transcription={"offset_seconds": 0.25},
            audio_mix={
                "customized": True,
                "channels": [{
                    "id": "external:craig:alice",
                    "kind": "external",
                    "path": str(audio),
                    "label": "alice",
                    "enabled": True,
                    "muted": False,
                    "solo": False,
                    "volume_percent": 110,
                }],
            },
        )
        project_path = root / f"{name}.subtitle-project.json"
        save_project(project_path, project)
        normal_ass = root / f"{name}.edited.ass"
        normal_ass.write_text("ASS", encoding="utf-8")
        return normal_ass, project_path

    def test_cut_render_uses_project_timeline_and_subtitle_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            normal_ass, project_path = self._build_stub_project(root, "game")
            ass_kwargs: dict[str, object] = {}

            def fake_build_ass(_transcript, output, **kwargs):
                ass_kwargs.update(kwargs)
                Path(output).write_text("CUT ASS", encoding="utf-8")
                return Path(output)

            speech_barrier = threading.Barrier(2)

            def fake_detect(_path, **_kwargs):
                speech_barrier.wait(timeout=1)
                return [(0.0, 1.0)]

            with (
                patch("src.subtitle_workflow.build_project_ass", return_value=normal_ass),
                patch("src.subtitle_workflow.detect_speech_ranges", side_effect=fake_detect) as detect,
                patch("src.subtitle_workflow.build_no_speech_plan", return_value=([(1.0, 4.0)], [(0.0, 1.0)])),
                patch("src.subtitle_workflow.build_ass_from_data", side_effect=fake_build_ass),
                patch("src.subtitle_workflow.cut_media_ranges") as cut_media,
            ):
                render_project_video(project_path, cut_no_speech=True, audio_normalize=False)

            self.assertEqual(ass_kwargs["subtitle_font_size"], 72)
            self.assertEqual(ass_kwargs["subtitle_outline_color"], "#234567")
            self.assertEqual(ass_kwargs["subtitle_outline_thickness"], 5)
            self.assertEqual(
                ass_kwargs["track_color_map"],
                {"craig:alice": "#FFD966", "craig:bob": "#6FA8DC"},
            )
            self.assertEqual(detect.call_count, 2)
            cut_media.assert_called_once()
            cut_kwargs = cut_media.call_args.kwargs
            self.assertTrue(cut_kwargs["audio_mix"]["customized"])
            self.assertEqual(cut_kwargs["audio_offset_seconds"], 0.25)

    def test_cut_render_uses_temporary_ass_filter_when_output_has_apostrophe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "work with apostrophe's path"
            normal_ass, project_path = self._build_stub_project(root, "game")
            ass_kwargs: dict[str, object] = {}

            def fake_build_ass(_transcript, output, **kwargs):
                ass_kwargs.update(kwargs)
                Path(output).write_text("CUT ASS", encoding="utf-8")
                return Path(output)

            with (
                patch("src.subtitle_workflow.build_project_ass", return_value=normal_ass),
                patch("src.subtitle_workflow.detect_speech_ranges", return_value=[(0.0, 1.0)]),
                patch("src.subtitle_workflow.build_no_speech_plan", return_value=([(1.0, 4.0)], [(0.0, 1.0)])),
                patch("src.subtitle_workflow.build_ass_from_data", side_effect=fake_build_ass),
                patch("src.subtitle_workflow.cut_media_ranges") as cut_media,
            ):
                render_project_video(project_path, cut_no_speech=True, audio_normalize=False)

            cut_kwargs = cut_media.call_args.kwargs
            video_filter = cut_kwargs["video_filter"]
            self.assertTrue(video_filter.startswith("ass='") and video_filter.endswith("'"))
            filter_path = video_filter.split("'")[1]
            self.assertNotIn("'", filter_path)
            self.assertEqual(ass_kwargs["subtitle_font_size"], 72)


if __name__ == "__main__":
    unittest.main()
