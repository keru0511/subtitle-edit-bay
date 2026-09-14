from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.gui_project_editor_controller import ProjectEditorController
from src.subtitle_project import create_project, load_project


class ProjectEditorControllerTests(unittest.TestCase):
    def _project(self, root: Path) -> dict[str, object]:
        return create_project(
            video_path=root / "capture.mp4",
            output_dir=root / "out",
            duration_seconds=12.0,
            segments=[
                {"id": "segment-a", "start": 0.0, "end": 1.0, "text": "first"},
            ],
        )

    def test_load_save_round_trip_and_facade_state_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "capture.subtitle-project.json"
            controller = ProjectEditorController(root)
            self.addCleanup(controller.shutdown)

            project = self._project(root)
            controller.save_new_project(path, project)
            loaded = controller.load(path)

            self.assertEqual(controller.project, loaded)
            self.assertEqual(controller.project_path, str(path.resolve()))
            self.assertFalse(controller.project_dirty)
            self.assertEqual(load_project(path)["segments"][0]["text"], "first")
            self.assertEqual(controller.selected_segment_index, 0)

    def test_dirty_autosave_persists_snapshot_and_clears_dirty_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "capture.subtitle-project.json"
            controller = ProjectEditorController(root)
            self.addCleanup(controller.shutdown)
            controller.save_new_project(path, self._project(root))
            controller.load(path)

            controller.project["segments"][0]["text"] = "autosaved"
            controller.mark_dirty()
            controller.autosave()
            revision = controller.autosave_revision
            autosave_path = controller.autosave_path
            controller.autosave_future.result()
            controller.finish_autosave(revision, autosave_path, "")

            self.assertFalse(controller.project_dirty)
            self.assertEqual(load_project(path)["segments"][0]["text"], "autosaved")

    def test_segment_crud_move_selection_and_undo_redo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controller = ProjectEditorController(root)
            self.addCleanup(controller.shutdown)
            project = self._project(root)
            controller.adopt_loaded_project(project, root / "capture.subtitle-project.json")

            added = {"id": "segment-b", "start": 3.0, "end": 4.0, "text": "second"}
            controller.commit_segment_change([], [added], added["id"])
            self.assertEqual(
                [item["id"] for item in controller.project["segments"]],
                ["segment-a", "segment-b"],
            )
            self.assertEqual(controller.selected_segment_index, 1)

            moved = {**added, "start": 0.5, "end": 1.5}
            controller.commit_segment_change([added], [moved], added["id"])
            self.assertEqual(
                [item["id"] for item in controller.project["segments"]],
                ["segment-a", "segment-b"],
            )
            self.assertEqual(controller.project["segments"][1]["start"], 0.5)
            controller.select_segment(0)
            self.assertEqual(controller.selected_segment_index, 0)

            self.assertTrue(controller.undo())
            self.assertEqual(
                [item["id"] for item in controller.project["segments"]],
                ["segment-a", "segment-b"],
            )
            self.assertEqual(controller.project["segments"][1]["start"], 3.0)
            self.assertTrue(controller.redo())
            self.assertEqual(controller.project["segments"][1]["id"], "segment-b")

            current = controller.project["segments"][0]
            controller.commit_segment_change([current], [])
            self.assertEqual(len(controller.project["segments"]), 1)
            self.assertTrue(controller.undo())
            self.assertEqual(len(controller.project["segments"]), 2)

    def test_signals_cover_project_history_and_selection_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            events: list[str] = []
            controller = ProjectEditorController(
                root,
                on_project_changed=lambda: events.append("project"),
                on_project_data_changed=lambda: events.append("data"),
                on_segments_changed=lambda: events.append("segments"),
                on_history_changed=lambda: events.append("history"),
                on_selection_changed=lambda: events.append("selection"),
            )
            self.addCleanup(controller.shutdown)

            project = self._project(root)
            controller.adopt_loaded_project(project, root / "capture.subtitle-project.json")
            controller.publish_loaded()
            controller.commit_segment_change(
                [],
                [{"id": "segment-b", "start": 2.0, "end": 3.0, "text": "second"}],
                "segment-b",
            )
            controller.undo()

            self.assertIn("project", events)
            self.assertIn("segments", events)
            self.assertIn("history", events)
            self.assertIn("selection", events)


if __name__ == "__main__":
    unittest.main()
