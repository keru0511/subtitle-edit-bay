from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.gui_project_editor_controller import ProjectEditorController
from src.subtitle_project import SubtitleProject, create_project, load_project, save_project
from src.video_sequence import (
    VideoSequence,
)
from tests.typed_case import TypedTestCase


class VideoSequenceTests(TypedTestCase):
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
