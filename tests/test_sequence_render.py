import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.sequence_render import (
    SequenceRenderError,
    SequenceRenderPlan,
    build_sequence_ffmpeg_command,
    build_sequence_filter_graph,
    prepare_sequence_render,
)
from src.subtitle_project import create_project, save_project
from src.video_sequence import VideoSequence


class SequenceRenderTests(unittest.TestCase):
    def _sequence(self, first: Path, second: Path) -> VideoSequence:
        return VideoSequence.from_json(
            {
                "schema_version": 1,
                "assets": [
                    {"id": "asset-a", "path": str(first), "duration_seconds": 4.0},
                    {"id": "asset-b", "path": str(second), "duration_seconds": 5.0},
                ],
                "clips": [
                    {
                        "id": "clip-a",
                        "asset_id": "asset-a",
                        "source_start": 0.5,
                        "source_end": 3.5,
                        "volume": 0.75,
                    },
                    {
                        "id": "clip-b",
                        "asset_id": "asset-b",
                        "source_start": 1.0,
                        "source_end": 5.0,
                        "transition": {"type": "crossfade", "duration": 0.5},
                        "audio_offset_seconds": 0.1,
                        "muted": True,
                    },
                ],
            }
        )

    def test_preflight_resolves_ordered_clips_and_audio_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first, second = root / "a.mp4", root / "b.mp4"
            first.write_bytes(b"a")
            second.write_bytes(b"b")
            sequence = self._sequence(first, second)
            plan = prepare_sequence_render(
                {"sequence": sequence.to_json(), "segments": [], "timeline": {"cuts": []}},
                probe_duration=lambda _path: 5.0,
                probe_audio_streams=lambda _path: [{"codec_name": "aac"}],
                probe_video_stream=lambda _path: {
                    "width": 1920,
                    "height": 1080,
                    "sample_aspect_ratio": "1:1",
                },
            )

        self.assertEqual([clip.id for clip in plan.clips], ["clip-a", "clip-b"])
        self.assertEqual(plan.output_duration, 6.5)
        self.assertTrue(plan.include_audio)

    def test_filter_graph_applies_trim_transition_and_clip_audio_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first, second = root / "a.mp4", root / "b.mp4"
            first.write_bytes(b"a")
            second.write_bytes(b"b")
            sequence = self._sequence(first, second)
            plan = SequenceRenderPlan(sequence, sequence.clips, sequence.output_duration, True)
            graph = build_sequence_filter_graph(plan, audio_filter="loudnorm=I=-16")
            command = build_sequence_ffmpeg_command(plan, root / "out.mp4", video_codec="libx264")

        self.assertIn("trim=start=0.500:end=3.500", graph)
        self.assertIn("fps=30,settb=AVTB,format=pix_fmts=yuv420p", graph)
        self.assertIn("[vjoin1raw]fps=30,settb=AVTB,format=pix_fmts=yuv420p[vjoin1]", graph)
        self.assertIn("xfade=transition=fade:duration=0.500:offset=2.500", graph)
        self.assertIn("volume=0.750", graph)
        self.assertIn("volume=0.000", graph)
        self.assertIn("adelay=100:all=1", graph)
        self.assertIn("[ajoin1raw]aformat=sample_rates=48000:sample_fmts=fltp,asetpts=PTS-STARTPTS[ajoin1]", graph)
        self.assertIn("loudnorm=I=-16", graph)
        self.assertEqual(command[-3:-1], ["-t", "6.500"])
        self.assertEqual(command[-1], str(root / "out.mp4"))

    def test_stale_and_ambiguous_projects_fail_before_ffmpeg(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first, second = root / "a.mp4", root / "b.mp4"
            first.write_bytes(b"a")
            second.write_bytes(b"b")
            sequence = self._sequence(first, second)
            base = {"sequence": sequence.to_json(), "segments": [], "timeline": {"cuts": []}}
            with self.assertRaisesRegex(SequenceRenderError, "stale"):
                prepare_sequence_render(
                    base,
                    probe_duration=lambda path: 3.0 if path == str(first) else 5.0,
                    probe_audio_streams=lambda _path: [{"codec_name": "aac"}],
                    probe_video_stream=lambda _path: {
                        "width": 1920,
                        "height": 1080,
                        "sample_aspect_ratio": "1:1",
                    },
                )
            base["segments"] = [{"start": 0.0, "end": 1.0, "text": "字幕"}]
            with self.assertRaisesRegex(SequenceRenderError, "subtitle mapping"):
                prepare_sequence_render(
                    base,
                    probe_duration=lambda _path: 5.0,
                    probe_audio_streams=lambda _path: [{"codec_name": "aac"}],
                    probe_video_stream=lambda _path: {
                        "width": 1920,
                        "height": 1080,
                        "sample_aspect_ratio": "1:1",
                    },
                )

    def test_mixed_video_resolution_or_sar_fails_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first, second = root / "a.mp4", root / "b.mp4"
            first.write_bytes(b"a")
            second.write_bytes(b"b")
            sequence = self._sequence(first, second)
            mismatches = {
                "resolution": (
                    {"width": 1920, "height": 1080, "sample_aspect_ratio": "1:1"},
                    {"width": 1280, "height": 720, "sample_aspect_ratio": "1:1"},
                ),
                "sar": (
                    {"width": 1920, "height": 1080, "sample_aspect_ratio": "1:1"},
                    {"width": 1920, "height": 1080, "sample_aspect_ratio": "4:3"},
                ),
            }
            for name, (first_stream, second_stream) in mismatches.items():
                with self.subTest(mismatch=name):
                    with self.assertRaisesRegex(SequenceRenderError, "mixed video resolution/SAR"):
                        prepare_sequence_render(
                            {"sequence": sequence.to_json(), "segments": [], "timeline": {"cuts": []}},
                            probe_duration=lambda _path: 5.0,
                            probe_audio_streams=lambda _path: [{"codec_name": "aac"}],
                            probe_video_stream=lambda path: (
                                first_stream if path == str(first) else second_stream
                            ),
                        )

    def test_mixed_video_parameters_fail_before_render_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "legacy.mp4"
            first, second = root / "a.mp4", root / "b.mp4"
            video.write_bytes(b"legacy")
            first.write_bytes(b"a")
            second.write_bytes(b"b")
            sequence = self._sequence(first, second)
            project = create_project(
                video_path=video,
                output_dir=root,
                duration_seconds=4.0,
                segments=[],
                sequence=sequence.to_json(),
            )
            project_path = save_project(root / "mixed.subtitle-project.json", project)
            with (
                patch("src.subtitle_workflow.probe_media_duration", return_value=5.0),
                patch("src.subtitle_workflow.probe_audio_streams", return_value=[{"codec_name": "aac"}]),
                patch(
                    "src.subtitle_workflow.probe_video_stream",
                    side_effect=[
                        {"width": 1920, "height": 1080, "sample_aspect_ratio": "1:1"},
                        {"width": 1280, "height": 720, "sample_aspect_ratio": "1:1"},
                    ],
                ),
                patch("src.subtitle_workflow.render_sequence_video") as sequence_render,
                patch("src.subtitle_workflow.run_ffmpeg_burn") as legacy_render,
            ):
                from src.subtitle_workflow import render_project_video

                with self.assertRaisesRegex(SystemExit, "mixed video resolution/SAR"):
                    render_project_video(
                        project_path,
                        output_path=root / "mixed.mp4",
                        audio_normalize=False,
                    )

        sequence_render.assert_not_called()
        legacy_render.assert_not_called()

    def test_project_render_routes_only_multi_clip_sequences(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "legacy.mp4"
            first, second = root / "a.mp4", root / "b.mp4"
            video.write_bytes(b"legacy")
            first.write_bytes(b"a")
            second.write_bytes(b"b")
            sequence = self._sequence(first, second)
            project = create_project(
                video_path=video,
                output_dir=root,
                duration_seconds=4.0,
                segments=[],
                sequence=sequence.to_json(),
            )
            project_path = save_project(root / "project.subtitle-project.json", project)
            plan = SequenceRenderPlan(sequence, sequence.clips, sequence.output_duration, True)
            with (
                patch("src.subtitle_workflow.prepare_sequence_render", return_value=plan),
                patch("src.subtitle_workflow.render_sequence_video", return_value=root / "out.mp4") as render,
            ):
                from src.subtitle_workflow import render_project_video

                output = render_project_video(project_path, output_path=root / "out.mp4", audio_normalize=False)

        self.assertEqual(output, root / "out.mp4")
        render.assert_called_once()


    def test_explicit_single_or_empty_sequence_fails_closed_without_legacy_render(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy_video = root / "legacy.mp4"
            edited_asset = root / "edited-asset.mp4"
            legacy_video.write_bytes(b"legacy")
            edited_asset.write_bytes(b"edited")
            common_asset = {
                "id": "asset-edited",
                "path": str(edited_asset),
                "duration_seconds": 4.0,
            }
            sequences = {
                "explicit-single": {
                    "schema_version": 1,
                    "assets": [common_asset],
                    "clips": [
                        {
                            "id": "clip-edited",
                            "asset_id": "asset-edited",
                            "source_start": 1.0,
                            "source_end": 3.0,
                            "volume": 0.25,
                        }
                    ],
                },
                "explicit-trimmed-single": {
                    "schema_version": 1,
                    "assets": [{**common_asset, "id": "asset-video"}],
                    "clips": [
                        {
                            "id": "clip-video",
                            "asset_id": "asset-video",
                            "source_start": 1.0,
                            "source_end": 3.0,
                        }
                    ],
                },
                "explicit-empty": {
                    "schema_version": 1,
                    "assets": [{**common_asset, "id": "asset-video"}],
                    "clips": [],
                },
            }

            for name, sequence in sequences.items():
                with self.subTest(sequence=name):
                    project = create_project(
                        video_path=legacy_video,
                        output_dir=root,
                        duration_seconds=4.0,
                        segments=[],
                        sequence=sequence,
                    )
                    project_path = save_project(
                        root / f"{name}.subtitle-project.json",
                        project,
                    )
                    with patch("src.subtitle_workflow.run_ffmpeg_burn") as legacy_render:
                        with self.assertRaisesRegex(
                            SystemExit,
                            "sequence render requires at least two clips",
                        ):
                            from src.subtitle_workflow import render_project_video

                            render_project_video(
                                project_path,
                                output_path=root / f"{name}.mp4",
                                audio_normalize=False,
                            )
                    legacy_render.assert_not_called()


if __name__ == "__main__":
    unittest.main()
