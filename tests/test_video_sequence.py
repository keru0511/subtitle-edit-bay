from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.gui_project_editor_controller import ProjectEditorController
from src.subtitle_project import SubtitleProject, create_project, load_project, save_project
from src.video_sequence import (
    SequenceTransition,
    VideoSequence,
    VideoSequenceError,
)


class VideoSequenceTests(unittest.TestCase):
    def _sequence(self) -> VideoSequence:
        return VideoSequence.from_json(
            {
                "schema_version": 1,
                "assets": [
                    {"id": "asset-a", "path": "a.mp4", "duration_seconds": 10.0},
                    {"id": "asset-b", "path": "b.mp4", "duration_seconds": 8.0},
                ],
                "clips": [
                    {
                        "id": "clip-a",
                        "asset_id": "asset-a",
                        "source_start": 1.0,
                        "source_end": 5.0,
                    },
                ],
            }
        )

    def test_legacy_single_video_maps_to_stable_sequence_identity(self) -> None:
        sequence = VideoSequence.from_json(
            None,
            legacy_video={"path": "capture.mp4", "duration_seconds": 12.0},
        )

        self.assertEqual([asset.id for asset in sequence.assets], ["asset-video"])
        self.assertEqual([clip.id for clip in sequence.clips], ["clip-video"])
        self.assertEqual(sequence.clips[0].asset_id, "asset-video")
        self.assertEqual(sequence.output_duration, 12.0)

    def test_add_trim_reorder_and_audio_mutations_preserve_clip_ids(self) -> None:
        sequence = self._sequence()
        sequence = sequence.add_clip(
            "asset-b",
            0.5,
            4.5,
            clip_id="clip-b",
            audio_linked=False,
            volume=1.25,
            audio_offset_seconds=-0.1,
            muted=True,
        )
        sequence = sequence.set_transition("clip-b", "crossfade", 0.5)
        sequence = sequence.trim_clip("clip-b", 1.0, 4.0)
        sequence = sequence.reorder_clip("clip-b", 0)

        self.assertEqual([clip.id for clip in sequence.clips], ["clip-b", "clip-a"])
        clip = sequence.clips[0]
        self.assertEqual((clip.source_start, clip.source_end), (1.0, 4.0))
        self.assertFalse(clip.audio_linked)
        self.assertEqual(clip.volume, 1.25)
        self.assertEqual(clip.audio_offset_seconds, -0.1)
        self.assertTrue(clip.muted)
        self.assertEqual(clip.transition.type, "cut")

    def test_crossfade_mapping_is_deterministic_at_overlap_boundary(self) -> None:
        sequence = self._sequence().add_clip(
            "asset-b",
            2.0,
            6.0,
            clip_id="clip-b",
            transition=SequenceTransition(type="crossfade", duration=1.0),
        )

        timeline = sequence.timeline
        self.assertEqual(
            [(entry.output_start, entry.output_end, entry.overlap) for entry in timeline.clips],
            [(0.0, 4.0, 0.0), (3.0, 7.0, 1.0)],
        )
        self.assertEqual(timeline.total_duration, 7.0)
        self.assertEqual(timeline.output_to_source_seconds(2.5).to_json(), {
            "clip_id": "clip-a",
            "source_time": 3.5,
        })
        self.assertEqual(timeline.output_to_source_seconds(3.0).to_json(), {
            "clip_id": "clip-b",
            "source_time": 2.0,
        })
        self.assertEqual(timeline.source_to_output_seconds("clip-b", 3.5), 4.5)

    def test_round_trip_preserves_unknown_sequence_fields_and_clip_state(self) -> None:
        payload = {
            "schema_version": 1,
            "future_sequence_field": {"keep": True},
            "assets": [
                {"id": "asset-a", "path": "a.mp4", "duration_seconds": 5.0, "asset_note": "keep"},
            ],
            "clips": [
                {
                    "id": "clip-a",
                    "asset_id": "asset-a",
                    "source_start": 0.0,
                    "source_end": 2.0,
                    "transition": {"type": "cut", "duration": 0.0, "transition_note": "keep"},
                    "audio_linked": False,
                    "volume": 0.75,
                    "audio_offset_seconds": 0.2,
                    "muted": True,
                    "clip_note": "keep",
                },
            ],
        }

        restored = VideoSequence.from_json(payload).to_json()

        self.assertEqual(restored, payload)

    def test_invalid_references_ranges_transitions_and_full_removal_fail_closed(self) -> None:
        with self.assertRaisesRegex(VideoSequenceError, "unknown asset"):
            self._sequence().add_clip("missing", 0.0, 1.0)
        with self.assertRaisesRegex(VideoSequenceError, "clip ids must be unique"):
            VideoSequence.from_json(
                {
                    "assets": [{"id": "asset", "path": "a.mp4", "duration_seconds": 5}],
                    "clips": [
                        {"id": "same", "asset_id": "asset", "source_start": 0, "source_end": 1},
                        {"id": "same", "asset_id": "asset", "source_start": 1, "source_end": 2},
                    ],
                }
            )
        with self.assertRaisesRegex(VideoSequenceError, "exceeds the asset duration"):
            self._sequence().add_clip("asset-a", 0.0, 11.0)
        with self.assertRaisesRegex(VideoSequenceError, "first clip"):
            VideoSequence.from_json(
                {
                    "assets": [{"id": "asset", "path": "a.mp4", "duration_seconds": 5}],
                    "clips": [
                        {
                            "id": "clip",
                            "asset_id": "asset",
                            "source_start": 0,
                            "source_end": 1,
                            "transition": {"type": "fade", "duration": 0.2},
                        }
                    ],
                }
            )
        with self.assertRaisesRegex(VideoSequenceError, "exceeds adjacent"):
            self._sequence().add_clip(
                "asset-b",
                0.0,
                0.1,
                clip_id="short-clip",
                transition=SequenceTransition(type="crossfade", duration=0.2),
            )

        with self.assertRaisesRegex(VideoSequenceError, "cannot remove all"):
            self._sequence().remove_asset("asset-a", remove_clips=True)
        with self.assertRaisesRegex(VideoSequenceError, "last sequence clip"):
            self._sequence().remove_clip("clip-a")

    def test_project_persistence_migrates_legacy_and_saves_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy_project = create_project(
                video_path=root / "legacy.mp4",
                output_dir=root / "out",
                duration_seconds=6.0,
                segments=[],
            )
            legacy_project.pop("sequence")
            legacy_model = SubtitleProject.from_json(legacy_project)
            self.assertEqual(
                [clip.id for clip in legacy_model.sequence.clips],
                ["clip-video"],
            )

            project = create_project(
                video_path=root / "capture.mp4",
                output_dir=root / "out",
                duration_seconds=10.0,
                segments=[],
            )
            project["sequence"] = self._sequence().to_json()
            path = save_project(root / "capture.subtitle-project.json", project)
            loaded = load_project(path)

        self.assertEqual(
            [clip["id"] for clip in loaded["sequence"]["clips"]],
            ["clip-a"],
        )
        self.assertEqual(loaded["sequence"]["assets"][1]["id"], "asset-b")

    def test_reloading_zero_duration_updates_legacy_sequence_from_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "capture.mp4"
            video.write_bytes(b"video")
            project = create_project(
                video_path=video,
                output_dir=root / "out",
                duration_seconds=0.0,
                segments=[],
            )
            path = save_project(root / "capture.subtitle-project.json", project)

            with patch("src.subtitle_project.probe_media_duration", return_value=30.0):
                loaded = load_project(path, resolve_video_duration=True)

        self.assertEqual(loaded["video"]["duration_seconds"], 30.0)
        self.assertEqual(loaded["sequence"]["assets"][0]["path"], str(video.resolve()))
        self.assertEqual(loaded["sequence"]["assets"][0]["duration_seconds"], 30.0)
        self.assertEqual(loaded["sequence"]["clips"][0]["source_end"], 30.0)

    def test_controller_sequence_mutation_is_one_undoable_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "capture.subtitle-project.json"
            controller = ProjectEditorController(root)
            self.addCleanup(controller.shutdown)
            controller.save_new_project(path, self._project(root))
            controller.load(path)

            updated = controller.apply_sequence_mutation(
                lambda sequence: sequence.add_clip(
                    "asset-b",
                    0.0,
                    2.0,
                    clip_id="clip-b",
                )
            )

            self.assertIsNotNone(updated)
            self.assertEqual([clip.id for clip in updated.clips], ["clip-a", "clip-b"])
            self.assertTrue(controller.project_dirty)
            self.assertEqual(
                [clip["id"] for clip in controller.project["sequence"]["clips"]],
                ["clip-a", "clip-b"],
            )
            controller.autosave()
            revision = controller.autosave_revision
            autosave_path = controller.autosave_path
            controller.autosave_future.result()
            controller.finish_autosave(revision, autosave_path, "")
            self.assertFalse(controller.project_dirty)
            self.assertEqual(
                [clip["id"] for clip in load_project(path)["sequence"]["clips"]],
                ["clip-a", "clip-b"],
            )
            self.assertTrue(controller.undo())
            self.assertEqual(
                [clip["id"] for clip in controller.project["sequence"]["clips"]],
                ["clip-a"],
            )
            self.assertTrue(controller.redo())
            self.assertEqual(
                [clip["id"] for clip in controller.project["sequence"]["clips"]],
                ["clip-a", "clip-b"],
            )

    def _project(self, root: Path) -> dict[str, object]:
        project = create_project(
            video_path=root / "capture.mp4",
            output_dir=root / "out",
            duration_seconds=10.0,
            segments=[],
        )
        project["sequence"] = self._sequence().to_json()
        return project


if __name__ == "__main__":
    unittest.main()
