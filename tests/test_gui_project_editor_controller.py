from __future__ import annotations

from copy import deepcopy
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Callable

from src.data_boundary import is_string_object_dict, is_string_object_dict_list
from src.gui_project_editor_controller import LayoutRows, ProjectEditorController, SaveProject
from src.subtitle_project import SubtitleProjectError, assign_project_layout_rows, create_project, load_project, save_project
from tests.typed_case import TypedTestCase


def _active_project(controller: ProjectEditorController) -> dict[str, object]:
    project = controller.project
    if project is None:
        raise AssertionError("controller has no active project")
    return project


def _section(project: object, name: str) -> dict[str, object]:
    if not is_string_object_dict(project):
        raise AssertionError("project must be an object")
    section = project.get(name)
    if not is_string_object_dict(section):
        raise AssertionError(f"{name} must be an object")
    return section


def _entries(section: object, name: str) -> list[dict[str, object]]:
    if not is_string_object_dict(section):
        raise AssertionError("section must be an object")
    entries = section.get(name)
    if not is_string_object_dict_list(entries):
        raise AssertionError(f"{name} must be an array of objects")
    return entries


def _segments(project: object) -> list[dict[str, object]]:
    return _entries(project, "segments")


class ProjectEditorControllerTests(TypedTestCase):
    def _project(self, root: Path) -> dict[str, object]:
        project = create_project(
            video_path=root / "capture.mp4",
            output_dir=root / "out",
            duration_seconds=12.0,
            segments=[
                {"id": "segment-a", "start": 0.0, "end": 1.0, "text": "first"},
            ],
        )
        if not is_string_object_dict(project):
            self.fail("created project must be an object")
        return project

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
            self.assertEqual(_segments(load_project(path))[0]["text"], "first")
            self.assertEqual(controller.selected_segment_index, 0)

    def test_dirty_autosave_persists_snapshot_and_clears_dirty_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "capture.subtitle-project.json"
            controller = ProjectEditorController(root)
            self.addCleanup(controller.shutdown)
            controller.save_new_project(path, self._project(root))
            controller.load(path)

            _segments(controller.project)[0]["text"] = "autosaved"
            controller.mark_dirty()
            controller.autosave()
            revision = controller.autosave_revision
            autosave_path = controller.autosave_path
            future = controller.autosave_future
            if future is None:
                self.fail("autosave should be running")
            future.result()
            controller.finish_autosave(revision, autosave_path, "")

            self.assertFalse(controller.project_dirty)
            self.assertEqual(_segments(load_project(path))[0]["text"], "autosaved")

    def test_autosave_completion_clears_matching_revision_before_facade_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "capture.subtitle-project.json"
            completed = threading.Event()
            notifications: list[tuple[int, str, str]] = []

            def on_autosave_completed(revision: int, autosave_path: str, error: str) -> None:
                notifications.append((revision, autosave_path, error))
                completed.set()

            controller = ProjectEditorController(
                root,
                on_autosave_completed=on_autosave_completed,
            )
            self.addCleanup(controller.shutdown)
            controller.adopt_loaded_project(self._project(root), path)
            _segments(controller.project)[0]["text"] = "queued completion"
            controller.mark_dirty()
            controller.autosave()

            self.assertTrue(completed.wait(timeout=1))
            self.assertFalse(controller.project_dirty)
            revision, autosave_path, error = notifications[0]
            self.assertEqual(error, "")
            controller.finish_autosave(revision, autosave_path, error)

    def test_reflow_uses_facade_layout_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            calls: list[list[dict[object, object]]] = []

            def capture_layout(segments: list[dict[object, object]]) -> list[dict[object, object]]:
                calls.append(segments)
                return segments

            controller = ProjectEditorController(
                root,
                assign_project_layout_rows_fn=capture_layout,
            )
            self.addCleanup(controller.shutdown)
            controller.adopt_loaded_project(
                self._project(root),
                root / "capture.subtitle-project.json",
            )

            current = _segments(controller.project)[0]
            controller.commit_segment_change(
                [current],
                [{**current, "text": "reflowed"}],
                reflow_layout=True,
            )

            self.assertEqual(len(calls), 1)

    def test_segment_crud_move_selection_and_undo_redo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controller = ProjectEditorController(root)
            self.addCleanup(controller.shutdown)
            project = self._project(root)
            controller.adopt_loaded_project(project, root / "capture.subtitle-project.json")

            added = {"id": "segment-b", "start": 3.0, "end": 4.0, "text": "second"}
            controller.commit_segment_change([], [added], "segment-b")
            self.assertEqual(
                [item["id"] for item in _segments(controller.project)],
                ["segment-a", "segment-b"],
            )
            self.assertEqual(controller.selected_segment_index, 1)

            moved = {**added, "start": 0.5, "end": 1.5}
            controller.commit_segment_change([added], [moved], "segment-b")
            self.assertEqual(
                [item["id"] for item in _segments(controller.project)],
                ["segment-a", "segment-b"],
            )
            self.assertEqual(_segments(controller.project)[1]["start"], 0.5)
            controller.select_segment(0)
            self.assertEqual(controller.selected_segment_index, 0)

            self.assertTrue(controller.undo())
            self.assertEqual(
                [item["id"] for item in _segments(controller.project)],
                ["segment-a", "segment-b"],
            )
            self.assertEqual(_segments(controller.project)[1]["start"], 3.0)
            self.assertTrue(controller.redo())
            self.assertEqual(_segments(controller.project)[1]["id"], "segment-b")

            current = _segments(controller.project)[0]
            controller.commit_segment_change([current], [])
            self.assertEqual(len(_segments(controller.project)), 1)
            self.assertTrue(controller.undo())
            self.assertEqual(len(_segments(controller.project)), 2)

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

    def _editing_controller(
        self,
        *,
        on_dirty: Callable[[], None] | None = None,
        on_segments_changed: Callable[[], None] | None = None,
        on_autosave_retry: Callable[[], None] | None = None,
        assign_project_layout_rows_fn: LayoutRows = assign_project_layout_rows,
        save_project_fn: SaveProject = save_project,
    ) -> ProjectEditorController:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        controller = ProjectEditorController(
            root,
            on_dirty=on_dirty,
            on_segments_changed=on_segments_changed,
            on_autosave_retry=on_autosave_retry,
            assign_project_layout_rows_fn=assign_project_layout_rows_fn,
            save_project_fn=save_project_fn,
        )
        self.addCleanup(controller.shutdown)
        controller.adopt_loaded_project(self._project(root), root / "edit.subtitle-project.json")
        return controller

    def test_rejected_edit_preserves_document_history_revision_selection_and_notifications(self) -> None:
        events = []
        controller = self._editing_controller(on_dirty=lambda: events.append("dirty"))
        current = _segments(controller.project)[0]
        controller.commit_segment_change([current], [{**current, "text": "変更"}])
        controller.undo()
        events.clear()
        before = deepcopy((controller.project, controller.undo_stack, controller.redo_stack,
                           controller.project_revision, controller.selected_segment_index, controller.project_dirty))
        duplicate = {**_segments(controller.project)[0], "id": "duplicate"}
        with self.assertRaises(SubtitleProjectError):
            controller.commit_segment_change([], [duplicate, duplicate])
        self.assertEqual(before, (controller.project, controller.undo_stack, controller.redo_stack,
                                 controller.project_revision, controller.selected_segment_index, controller.project_dirty))
        self.assertEqual(events, [])

    def test_layout_failure_does_not_mutate_existing_segments_or_history(self) -> None:
        def fail_layout(segments: list[dict[object, object]]) -> list[dict[object, object]]:
            segments[0]["layout_row"] = 999
            raise ValueError("レイアウト失敗")
        controller = self._editing_controller(assign_project_layout_rows_fn=fail_layout)
        before = deepcopy(controller.project)
        revision = controller.project_revision
        with self.assertRaises(ValueError):
            controller.commit_segment_change([], [{"id": "new", "start": 2, "end": 3, "text": "追加"}])
        self.assertEqual(controller.project, before)
        self.assertEqual(controller.project_revision, revision)
        self.assertFalse(controller.can_undo)
        self.assertFalse(controller.project_dirty)

    def test_failed_undo_preserves_history_and_document(self) -> None:
        controller = self._editing_controller()
        current = _segments(controller.project)[0]
        controller.commit_segment_change([current], [{**current, "text": "変更"}])
        before = deepcopy((controller.project, controller.undo_stack, controller.redo_stack, controller.project_revision))
        def fail_layout(segments: list[dict[object, object]]) -> list[dict[object, object]]:
            del segments
            raise ValueError("復元失敗")
        controller._assign_project_layout_rows_fn = fail_layout
        with self.assertRaises(ValueError):
            controller.undo()
        self.assertEqual(before, (controller.project, controller.undo_stack, controller.redo_stack, controller.project_revision))

    def test_noop_preserves_redo_and_does_not_request_save(self) -> None:
        events: list[str] = []
        controller = self._editing_controller(on_dirty=lambda: events.append("dirty"))
        current = deepcopy(_segments(controller.project)[0])
        controller.commit_segment_change([current], [{**current, "text": "変更"}])
        controller.undo()
        revision = controller.project_revision
        events.clear()
        controller.commit_segment_change([current], [current])
        self.assertEqual(controller.project_revision, revision)
        self.assertTrue(controller.can_redo)
        self.assertEqual(events, [])

    def test_callbacks_observe_committed_history_revision_and_document(self) -> None:
        observed: list[tuple[int, bool, bool, object]] = []

        def on_segments_changed() -> None:
            observed.append(
                (controller.project_revision, controller.project_dirty, controller.can_undo,
                 _segments(controller.project)[0]["text"])
            )

        controller = self._editing_controller(
            on_segments_changed=on_segments_changed,
        )
        revision = controller.project_revision
        current = _segments(controller.project)[0]
        controller.commit_segment_change([current], [{**current, "text": "確定済み"}])
        self.assertEqual(observed, [(revision + 1, True, True, "確定済み")])

    def test_short_edit_roundtrip_removes_previously_absent_section(self) -> None:
        events: list[str] = []
        controller = self._editing_controller(on_dirty=lambda: events.append("dirty"))
        _active_project(controller).pop("short_video", None)
        clips: list[dict[str, object]] = [{"start": 0, "end": 1}]
        section: dict[str, object] = {"enabled": True, "clips": clips}
        controller.commit_section_change("short_video", section)
        clips[0]["end"] = 10
        self.assertEqual(_entries(_section(controller.project, "short_video"), "clips")[0]["end"], 1)
        controller.undo()
        self.assertNotIn("short_video", _active_project(controller))
        controller.redo()
        self.assertEqual(_entries(_section(controller.project, "short_video"), "clips")[0]["end"], 1)
        self.assertEqual(events, ["dirty"] * 3)

    def test_audio_history_keeps_current_channel_topology(self) -> None:
        controller = self._editing_controller()
        _active_project(controller)["audio_mix"] = {"customized": False, "channels": [
            {"id": "kept", "path": "old.wav", "volume_percent": 100},
            {"id": "removed", "path": "removed.wav", "volume_percent": 100},
        ]}
        edited = deepcopy(_section(controller.project, "audio_mix"))
        _entries(edited, "channels")[0]["volume_percent"] = 130
        edited["customized"] = True
        controller.commit_section_change("audio_mix", edited)
        _section(controller.project, "audio_mix")["channels"] = [
            {"id": "kept", "path": "new.wav", "volume_percent": 130},
            {"id": "added", "path": "added.wav", "volume_percent": 80},
        ]
        for action, volume in ((controller.undo, 100), (controller.redo, 130)):
            action()
            self.assertEqual(_section(controller.project, "audio_mix")["channels"], [
                {"id": "kept", "path": "new.wav", "volume_percent": volume},
                {"id": "added", "path": "added.wav", "volume_percent": 80},
            ])

    def test_mixed_edit_history_roundtrips_without_overwriting_other_sections(self) -> None:
        from src.video_timeline import timeline_from_project

        controller = self._editing_controller()
        states = [deepcopy(controller.project)]
        mix = deepcopy(_section(controller.project, "audio_mix"))
        _entries(mix, "channels")[0]["volume_percent"] = 80
        mix["customized"] = True
        controller.commit_section_change("audio_mix", mix)
        states.append(deepcopy(controller.project))
        current = _segments(controller.project)[0]
        controller.commit_segment_change([current], [{**current, "text": "字幕を修正"}])
        states.append(deepcopy(controller.project))
        short = deepcopy(_section(controller.project, "short_video"))
        short["enabled"] = True
        short["clips"] = [{"start": 0, "end": 1}]
        controller.commit_section_change("short_video", short)
        states.append(deepcopy(controller.project))
        timeline = timeline_from_project(_active_project(controller)).add_cut(4, 5)
        controller.commit_timeline_change(timeline.to_json())
        states.append(deepcopy(controller.project))
        for expected in reversed(states[:-1]):
            self.assertTrue(controller.undo())
            self.assertEqual(controller.project, expected)
        self.assertFalse(controller.can_undo)
        for expected in states[1:]:
            self.assertTrue(controller.redo())
            self.assertEqual(controller.project, expected)
        self.assertFalse(controller.can_redo)

    def test_invalid_short_edit_does_not_create_history(self) -> None:
        controller = self._editing_controller()
        before = deepcopy(controller.project)
        with self.assertRaises(ValueError):
            controller.commit_section_change("short_video", {"global_fit": "invalid"})
        self.assertEqual(controller.project, before)
        self.assertFalse(controller.can_undo)
        self.assertFalse(controller.project_dirty)

    def test_edit_during_save_keeps_snapshot_and_requests_latest_revision(self) -> None:
        started, release = threading.Event(), threading.Event()
        def delayed_save(
            path: str | Path,
            project: dict[object, object],
            *,
            project_is_validated: bool = False,
            update_project: bool = True,
        ) -> Path:
            started.set()
            if not release.wait(3):
                raise TimeoutError("保存テストの同期が失敗しました")
            return save_project(
                path, project,
                project_is_validated=project_is_validated,
                update_project=update_project,
            )
        retries: list[bool] = []
        controller = self._editing_controller(save_project_fn=delayed_save, on_autosave_retry=lambda: retries.append(True))
        self.addCleanup(release.set)
        current = _segments(controller.project)[0]
        controller.commit_segment_change([current], [{**current, "text": "保存対象"}])
        controller.autosave()
        self.assertTrue(started.wait(1))
        revision, path = controller.autosave_revision, controller.autosave_path
        current = _segments(controller.project)[0]
        controller.commit_segment_change([current], [{**current, "text": "次の編集", "start": 0.5}])
        controller.autosave()
        release.set()
        future = controller.autosave_future
        if future is None:
            self.fail("autosave should be running")
        future.result(timeout=3)
        controller.finish_autosave(revision, path, "")
        self.assertEqual(_segments(load_project(path))[0]["text"], "保存対象")
        self.assertEqual(_segments(controller.project)[0]["text"], "次の編集")
        self.assertTrue(controller.project_dirty)
        self.assertEqual(retries, [True])
        controller.autosave()
        revision, path = controller.autosave_revision, controller.autosave_path
        future = controller.autosave_future
        if future is None:
            self.fail("autosave should be running")
        future.result(timeout=3)
        controller.finish_autosave(revision, path, "")
        self.assertEqual(_segments(load_project(path))[0]["text"], "次の編集")
        self.assertFalse(controller.project_dirty)


if __name__ == "__main__":
    unittest.main()
