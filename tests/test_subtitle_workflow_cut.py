import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.subtitle_project import create_project, save_project
from src.subtitle_workflow import render_project_video


class SubtitleWorkflowCutTests(unittest.TestCase):
    def test_cut_render_uses_project_timeline_and_subtitle_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            audio = root / "1-alice.flac"
            video.write_bytes(b"video")
            audio.write_bytes(b"audio")
            speaker = {
                "name": "alice",
                "style": "Oz",
                "track_key": "craig:alice",
                "color": "#FFD966",
                "file_name": audio.name,
                "path": str(audio),
            }
            project = create_project(
                video_path=video,
                output_dir=root,
                duration_seconds=4,
                audio_sources=[speaker],
                speakers=[speaker],
                segments=[{
                    "start": 0.25,
                    "end": 0.75,
                    "text": "edited",
                    "speaker": "Oz",
                    "source_track": "craig:alice",
                }],
                subtitle_settings={
                    "font_size": 72,
                    "max_gap_seconds": 0.2,
                    "end_padding_seconds": 0.04,
                    "min_duration_seconds": 0.3,
                },
                transcription={"offset_seconds": 0.25},
            )
            project_path = root / "game.subtitle-project.json"
            save_project(project_path, project)
            normal_ass = root / "game.edited.ass"
            normal_ass.write_text("ASS", encoding="utf-8")
            ass_kwargs = {}

            def fake_build_ass(_transcript, output, **kwargs):
                ass_kwargs.update(kwargs)
                Path(output).write_text("CUT ASS", encoding="utf-8")
                return Path(output)

            with (
                patch("src.subtitle_workflow.build_project_ass", return_value=normal_ass),
                patch("src.subtitle_workflow.detect_speech_ranges", return_value=[(0.0, 1.0)]),
                patch("src.subtitle_workflow.build_no_speech_plan", return_value=([(1.0, 4.0)], [(0.0, 1.0)])),
                patch("src.subtitle_workflow.build_ass_from_transcript", side_effect=fake_build_ass),
                patch("src.subtitle_workflow.cut_media_ranges") as cut_media,
            ):
                render_project_video(project_path, cut_no_speech=True, audio_normalize=False)

            self.assertEqual(ass_kwargs["subtitle_font_size"], 72)
            self.assertEqual(ass_kwargs["track_color_map"], {"craig:alice": "#FFD966"})
            cut_media.assert_called_once()


if __name__ == "__main__":
    unittest.main()
