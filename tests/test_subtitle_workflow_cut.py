import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from src.subtitle_project import create_project, load_project, save_project
from src.subtitle_workflow import build_project_ass, render_project_video


class SubtitleWorkflowCutTests(unittest.TestCase):
    def test_project_ass_maps_source_subtitles_to_manual_cut_output_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            video.write_bytes(b"video")
            project = create_project(
                video_path=video,
                output_dir=root,
                duration_seconds=5.0,
                segments=[
                    {"id": "before", "start": 0.5, "end": 1.5, "text": "before"},
                    {"id": "removed", "start": 1.2, "end": 1.8, "text": "removed"},
                    {"id": "after", "start": 2.2, "end": 3.0, "text": "after"},
                ],
                timeline={
                    "cuts": [
                        {"id": "cut", "source_start": 1.0, "source_end": 2.0}
                    ]
                },
            )
            project_path = save_project(root / "game.subtitle-project.json", project)
            captured: dict[str, object] = {}

            def capture_ass(transcript, output, **_kwargs):
                captured["segments"] = transcript["segments"]
                Path(output).write_text("ASS", encoding="utf-8")

            with patch("src.subtitle_workflow.build_ass_from_data", side_effect=capture_ass):
                build_project_ass(project_path)

            segments = captured["segments"]
            self.assertEqual([item["id"] for item in segments], ["before", "after"])
            self.assertEqual(
                [(item["start"], item["end"]) for item in segments],
                [(0.5, 1.0), (1.2, 2.0)],
            )

    def test_manual_cut_render_uses_kept_ranges_and_retimed_subtitles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            video.write_bytes(b"video")
            project = create_project(
                video_path=video,
                output_dir=root,
                duration_seconds=5.0,
                segments=[{"start": 2.5, "end": 3.0, "text": "after cut"}],
                timeline={
                    "cuts": [
                        {"id": "manual", "source_start": 1.0, "source_end": 2.0}
                    ]
                },
            )
            project_path = save_project(root / "game.subtitle-project.json", project)
            ass_path = root / "game.edited.ass"
            ass_path.write_text("ASS", encoding="utf-8")

            with (
                patch("src.subtitle_workflow.probe_audio_streams", return_value=[{"codec_name": "aac"}]),
                patch("src.subtitle_workflow.build_project_ass", return_value=ass_path),
                patch("src.subtitle_workflow.cut_media_ranges") as cut_media,
                patch("src.subtitle_workflow.run_ffmpeg_burn") as burn,
            ):
                output = render_project_video(project_path, audio_normalize=False)

            self.assertEqual(cut_media.call_args.args[2], [(0.0, 1.0), (2.0, 5.0)])
            self.assertTrue(cut_media.call_args.kwargs["include_audio"])
            intermediate = Path(cut_media.call_args.args[1])
            self.assertIn(".timeline-cut", intermediate.stem)
            self.assertEqual(Path(burn.call_args.args[0]), intermediate)
            self.assertEqual(Path(burn.call_args.args[1]), ass_path)
            self.assertEqual(Path(burn.call_args.args[2]), output)
            saved = load_project(project_path)
            self.assertEqual(saved["render_settings"]["manual_cut_count"], 1)
            self.assertEqual(saved["render_settings"]["output_duration_seconds"], 4.0)

    def test_manual_cut_render_supports_video_without_audio_or_subtitles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video-only.mkv"
            video.write_bytes(b"video")
            project = create_project(
                video_path=video,
                output_dir=root,
                duration_seconds=3.0,
                segments=[],
                timeline={
                    "cuts": [
                        {"id": "manual", "source_start": 1.0, "source_end": 2.0}
                    ]
                },
            )
            project_path = save_project(root / "video-only.subtitle-project.json", project)

            with (
                patch("src.subtitle_workflow.probe_audio_streams", return_value=[]),
                patch("src.subtitle_workflow.cut_media_ranges") as cut_media,
            ):
                render_project_video(project_path, audio_normalize=False)

            self.assertFalse(cut_media.call_args.kwargs["include_audio"])

    def test_automatic_silence_cut_is_intersected_after_manual_cut_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
            video.write_bytes(b"video")
            project = create_project(
                video_path=video,
                output_dir=root,
                duration_seconds=4.0,
                segments=[],
                audio_mix={
                    "channels": [
                        {
                            "id": "video:0:a:0",
                            "kind": "video",
                            "selector": "0:a:0",
                            "enabled": True,
                        }
                    ]
                },
                timeline={
                    "cuts": [
                        {"id": "manual", "source_start": 1.0, "source_end": 2.0}
                    ]
                },
            )
            project_path = save_project(root / "game.subtitle-project.json", project)

            with (
                patch("src.subtitle_workflow.probe_audio_streams", return_value=[{"codec_name": "aac"}]),
                patch("src.subtitle_workflow.detect_speech_ranges", return_value=[(0.0, 4.0)]),
                patch(
                    "src.subtitle_workflow.build_no_speech_plan",
                    return_value=([(0.0, 0.5), (3.0, 4.0)], [(0.5, 3.0)]),
                ),
                patch("src.subtitle_workflow.cut_media_ranges") as cut_media,
            ):
                render_project_video(
                    project_path,
                    cut_no_speech=True,
                    audio_normalize=False,
                )

            self.assertEqual(cut_media.call_args.args[2], [(0.5, 1.0), (2.0, 3.0)])

    def test_cut_no_speech_uses_video_audio_for_empty_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video-with-audio.mkv"
            video.write_bytes(b"video")
            project_path = root / "video-with-audio.subtitle-project.json"
            project = create_project(
                video_path=video,
                output_dir=root,
                segments=[],
                duration_seconds=3.0,
                audio_mix={
                    "customized": False,
                    "channels": [
                        {
                            "id": "video:0:a:0",
                            "kind": "video",
                            "selector": "0:a:0",
                            "enabled": True,
                            "muted": False,
                            "solo": False,
                            "volume_percent": 100.0,
                        }
                    ],
                },
            )
            save_project(project_path, project)
            detected: list[tuple[str, str | None]] = []

            def fake_detect(path: str, **kwargs: object) -> list[tuple[float, float]]:
                detected.append((path, kwargs.get("audio_track")))
                return [(0.0, 1.0)]

            with (
                patch("src.subtitle_workflow.probe_audio_streams", return_value=[{"codec_name": "aac", "channels": 2}]),
                patch("src.subtitle_workflow.detect_speech_ranges", side_effect=fake_detect),
                patch("src.subtitle_workflow.build_no_speech_plan", return_value=([(1.0, 2.0)], [(0.0, 1.0)])),
                patch("src.subtitle_workflow.cut_media_ranges"),
            ):
                render_project_video(project_path, cut_no_speech=True, audio_normalize=False)

            self.assertEqual(len(detected), 1)
            self.assertTrue(Path(detected[0][0]).samefile(video))
            self.assertEqual(detected[0][1], "0:a:0")

    def test_cut_render_uses_project_timeline_and_subtitle_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "game.mkv"
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
            project_path = root / "game.subtitle-project.json"
            save_project(project_path, project)
            normal_ass = root / "game.edited.ass"
            normal_ass.write_text("ASS", encoding="utf-8")
            ass_kwargs = {}

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
                patch("src.subtitle_workflow.run_ffmpeg_burn") as burn_subtitles,
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
            cut_output = Path(cut_media.call_args.args[1])
            self.assertIn(".silence-cut", cut_output.stem)
            cut_kwargs = cut_media.call_args.kwargs
            self.assertTrue(cut_kwargs["audio_mix"]["customized"])
            self.assertEqual(cut_kwargs["audio_offset_seconds"], 0.25)
            burn_subtitles.assert_called_once()
            self.assertEqual(Path(burn_subtitles.call_args.args[0]), cut_output)
            saved_project = load_project(project_path)
            self.assertEqual(
                Path(saved_project["render_settings"]["last_cut_output"]),
                cut_output.resolve(),
            )
            self.assertEqual(saved_project["render_settings"]["speech_threshold_db"], "-40dB")


if __name__ == "__main__":
    unittest.main()
