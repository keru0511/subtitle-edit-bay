from __future__ import annotations

"""Project editing state and persistence for the Qt GUI facade.

The public QML object remains ``EditBayBackend`` for compatibility, but the
project document itself is owned by this controller.  The controller is
deliberately small at its boundary: domain validation and persistence stay in
``subtitle_project`` while facade callbacks handle signal dispatch and editor-specific rendering
remain in the facade.
"""

from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from .subtitle_project import (
    SubtitleProjectError,
    assign_project_layout_rows,
    load_project,
    save_project,
)


SaveProject = Callable[..., Path]
LoadProject = Callable[..., dict[str, Any]]
Callback = Callable[..., None]


class ProjectEditorController:
    """Own project document state, history, selection, and autosave.

    ``EditBayBackend`` forwards these callback notifications and keeps its
    existing QML properties/slots.  The controller has no reference to the backend, which
    prevents a project-editor/source/audio controller cycle.
    """

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        load_project_fn: LoadProject = load_project,
        save_project_fn: SaveProject = save_project,
        on_project_changed: Callback | None = None,
        on_project_data_changed: Callback | None = None,
        on_segments_changed: Callback | None = None,
        on_history_changed: Callback | None = None,
        on_selection_changed: Callback | None = None,
        on_autosave_completed: Callback | None = None,
        on_dirty: Callback | None = None,
        on_autosave_retry: Callback | None = None,
        on_history_applied: Callback | None = None,
        autosave_interval_ms: int = 700,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self._load_project_fn = load_project_fn
        self._save_project_fn = save_project_fn
        self._on_project_changed = on_project_changed
        self._on_project_data_changed = on_project_data_changed
        self._on_segments_changed = on_segments_changed
        self._on_history_changed = on_history_changed
        self._on_selection_changed = on_selection_changed
        self._on_autosave_completed = on_autosave_completed
        self._on_dirty = on_dirty
        self._on_autosave_retry = on_autosave_retry
        self._on_history_applied = on_history_applied
        self._project: dict[str, Any] | None = None
        self._project_path = ""
        self._project_dirty = False
        self._project_revision = 0
        self._undo_stack: list[dict[str, Any]] = []
        self._redo_stack: list[dict[str, Any]] = []
        self._selected_segment_index = -1

        self._autosave_future: Future[Path] | None = None
        self._autosave_revision = -1
        self._autosave_path = ""
        self._autosave_pending = False
        self._ignored_autosaves: set[tuple[int, str]] = set()
        self._autosave_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="project-save",
        )
        self.autosave_interval_ms = int(autosave_interval_ms)

    @staticmethod
    def _emit(callback: Callback | None, *args: object) -> None:
        if callback is not None:
            callback(*args)

    @property
    def project(self) -> dict[str, Any] | None:
        return self._project

    @project.setter
    def project(self, value: dict[str, Any] | None) -> None:
        self._project = value

    @property
    def project_path(self) -> str:
        return self._project_path

    @project_path.setter
    def project_path(self, value: str | Path) -> None:
        self._project_path = str(value)

    @property
    def project_dirty(self) -> bool:
        return self._project_dirty

    @project_dirty.setter
    def project_dirty(self, value: bool) -> None:
        self._project_dirty = bool(value)

    @property
    def project_revision(self) -> int:
        return self._project_revision

    @project_revision.setter
    def project_revision(self, value: int) -> None:
        self._project_revision = int(value)

    @property
    def undo_stack(self) -> list[dict[str, Any]]:
        return self._undo_stack

    @property
    def redo_stack(self) -> list[dict[str, Any]]:
        return self._redo_stack

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    @property
    def selected_segment_index(self) -> int:
        return self._selected_segment_index

    @selected_segment_index.setter
    def selected_segment_index(self, value: int) -> None:
        self._selected_segment_index = int(value)

    @property
    def autosave_future(self) -> Future[Path] | None:
        return self._autosave_future

    @autosave_future.setter
    def autosave_future(self, value: Future[Path] | None) -> None:
        self._autosave_future = value

    @property
    def autosave_revision(self) -> int:
        return self._autosave_revision

    @autosave_revision.setter
    def autosave_revision(self, value: int) -> None:
        self._autosave_revision = int(value)

    @property
    def autosave_path(self) -> str:
        return self._autosave_path

    @autosave_path.setter
    def autosave_path(self, value: str | Path) -> None:
        self._autosave_path = str(value)

    @property
    def autosave_pending(self) -> bool:
        return self._autosave_pending

    @autosave_pending.setter
    def autosave_pending(self, value: bool) -> None:
        self._autosave_pending = bool(value)

    @property
    def ignored_autosaves(self) -> set[tuple[int, str]]:
        return self._ignored_autosaves

    @property
    def autosave_executor(self) -> ThreadPoolExecutor:
        return self._autosave_executor

    def load(self, path: str | Path) -> dict[str, Any]:
        """Read and adopt a project while leaving source/UI side effects outside."""

        project = self._load_project_fn(Path(path), resolve_video_duration=True)
        self.adopt_loaded_project(project, path)
        return project

    def adopt_loaded_project(
        self,
        project: dict[str, Any],
        path: str | Path,
        *,
        selected_segment_index: int | None = None,
        emit: bool = False,
    ) -> None:
        self._project = project
        self._project_path = str(Path(path).resolve())
        self._project_dirty = False
        self._project_revision += 1
        self._undo_stack.clear()
        self._redo_stack.clear()
        if selected_segment_index is None:
            selected_segment_index = 0 if project.get("segments") else -1
        self._selected_segment_index = int(selected_segment_index)
        self._autosave_pending = False
        if emit:
            self.publish_loaded()

    def publish_loaded(self) -> None:
        self._emit(self._on_project_changed)
        self._emit(self._on_project_data_changed)
        self._emit(self._on_segments_changed)
        self._emit(self._on_history_changed)
        self._emit(self._on_selection_changed)

    def clear(self, *, emit: bool = True) -> None:
        self._project = None
        self._project_path = ""
        self._project_dirty = False
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._selected_segment_index = -1
        self._project_revision += 1
        self._autosave_pending = False
        if emit:
            self._emit(self._on_project_changed)
            self._emit(self._on_project_data_changed)
            self._emit(self._on_segments_changed)
            self._emit(self._on_history_changed)
            self._emit(self._on_selection_changed)

    def save(self, path: str | Path | None = None, *, emit: bool = True) -> Path:
        raw_path = self._project_path if path is None else path
        if self._project is None or not str(raw_path).strip():
            raise SubtitleProjectError("保存する字幕編集プロジェクトがありません")
        target = Path(raw_path)
        self.wait_for_autosave()
        saved = self._save_project_fn(
            target,
            self._project,
            project_is_validated=True,
        )
        self._project_path = str(target.resolve())
        self._project_dirty = False
        if emit:
            self._emit(self._on_project_changed)
        return saved

    def save_as(self, path: str | Path, *, emit: bool = True) -> Path:
        if not str(path).strip():
            raise SubtitleProjectError("保存先が指定されていません")
        saved = self.save(path, emit=False)
        self._project_revision += 1
        if emit:
            self._emit(self._on_project_changed)
        return saved

    def save_new_project(
        self,
        path: str | Path,
        project: dict[str, Any],
    ) -> Path:
        """Persist a newly-created document before it becomes the active one."""

        return self._save_project_fn(path, project)

    def mark_dirty(self) -> None:
        if self._project is None:
            return
        self._project_revision += 1
        self._project_dirty = True
        self._emit(self._on_project_changed)
        self._emit(self._on_dirty)

    def push_history(self, entry: dict[str, Any]) -> None:
        self._undo_stack.append(entry)
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._emit(self._on_history_changed)

    def record_history(
        self,
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
        reflow_layout: bool = True,
    ) -> None:
        if self._project is None or (not before and not after):
            return
        self.push_history(
            {
                "kind": "segments",
                "before": deepcopy(before),
                "after": deepcopy(after),
                "reflow_layout": reflow_layout,
            }
        )

    def record_timeline_history(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> None:
        if self._project is None or before == after:
            return
        self.push_history(
            {
                "kind": "timeline",
                "before": deepcopy(before),
                "after": deepcopy(after),
            }
        )

    def replace_segments(
        self,
        segments: list[dict[str, Any]],
        selected_id: str | None = None,
        *,
        reflow_layout: bool = True,
    ) -> None:
        if self._project is None:
            return
        if self._autosave_future is not None and not self._autosave_future.done():
            segments = [dict(item) for item in segments]
        ordered = sorted(segments, key=lambda item: (item["start"], item["end"], item["id"]))
        ids = [str(item["id"]) for item in ordered]
        if len(ids) != len(set(ids)):
            raise SubtitleProjectError("segment ids must be unique")
        self._project["segments"] = (
            assign_project_layout_rows(ordered) if reflow_layout else ordered
        )
        if selected_id:
            self._selected_segment_index = next(
                (
                    index
                    for index, item in enumerate(self._project["segments"])
                    if item["id"] == selected_id
                ),
                -1,
            )
        elif self._selected_segment_index >= len(self._project["segments"]):
            self._selected_segment_index = len(self._project["segments"]) - 1
        self._emit(self._on_segments_changed)
        self._emit(self._on_selection_changed)
        self.mark_dirty()

    def commit_segment_change(
        self,
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
        selected_id: str | None = None,
        *,
        reflow_layout: bool = True,
    ) -> None:
        if self._project is None:
            return
        affected_ids = {str(item["id"]) for item in [*before, *after]}
        segments = [
            item
            for item in self._project["segments"]
            if str(item["id"]) not in affected_ids
        ]
        segments.extend(after)
        self.record_history(before, after, reflow_layout)
        self.replace_segments(segments, selected_id, reflow_layout=reflow_layout)

    def replace_timeline(self, payload: dict[str, Any]) -> None:
        if self._project is None:
            return
        self._project["timeline"] = deepcopy(payload)
        self._emit(self._on_project_data_changed)
        self.mark_dirty()

    def apply_history_entry(self, entry: dict[str, Any], state: str) -> None:
        if self._project is None:
            return
        if entry.get("kind") == "audio_mix":
            self._project["audio_mix"] = deepcopy(entry.get(state, {}))
            self._emit(self._on_project_data_changed)
            self.mark_dirty()
            self._emit(self._on_history_applied, entry, state)
            return
        if entry.get("kind") == "timeline":
            self.replace_timeline(deepcopy(entry.get(state, {})))
            self._emit(self._on_history_applied, entry, state)
            return
        affected_ids = {
            str(item["id"])
            for item in [*entry.get("before", []), *entry.get("after", [])]
        }
        segments = [
            item
            for item in self._project["segments"]
            if str(item["id"]) not in affected_ids
        ]
        segments.extend(deepcopy(entry.get(state, [])))
        self.replace_segments(
            segments,
            reflow_layout=bool(entry.get("reflow_layout", True)),
        )
        self._emit(self._on_history_applied, entry, state)

    def undo(self) -> bool:
        if self._project is None or not self._undo_stack:
            return False
        entry = self._undo_stack.pop()
        self._redo_stack.append(entry)
        self.apply_history_entry(entry, "before")
        self._emit(self._on_history_changed)
        return True

    def redo(self) -> bool:
        if self._project is None or not self._redo_stack:
            return False
        entry = self._redo_stack.pop()
        self._undo_stack.append(entry)
        self.apply_history_entry(entry, "after")
        self._emit(self._on_history_changed)
        return True

    def select_segment(self, index: int) -> None:
        count = len(self._project.get("segments", [])) if self._project else 0
        resolved = int(index) if 0 <= int(index) < count else -1
        if resolved != self._selected_segment_index:
            self._selected_segment_index = resolved
            self._emit(self._on_selection_changed)

    def autosave(self) -> None:
        if not self._project_dirty or self._project is None or not self._project_path:
            return
        if self._autosave_future is not None:
            self._autosave_pending = True
            return

        snapshot = {
            key: (list(value) if key == "segments" else deepcopy(value))
            for key, value in self._project.items()
        }
        revision = self._project_revision
        path = self._project_path
        self._autosave_revision = revision
        self._autosave_path = path
        self._autosave_pending = False
        future = self._autosave_executor.submit(
            self._save_project_fn,
            path,
            snapshot,
            project_is_validated=True,
            update_project=False,
        )
        self._autosave_future = future

        def report_completion(done: Future[Path]) -> None:
            try:
                done.result()
                error = ""
            except Exception as failure:  # pragma: no cover - exercised through Qt
                error = str(failure)
            self._emit(self._on_autosave_completed, revision, path, error)

        future.add_done_callback(report_completion)

    def finish_autosave(self, revision: int, path: str, error: str) -> None:
        token = (revision, path)
        if token in self._ignored_autosaves:
            self._ignored_autosaves.discard(token)
            return
        self._autosave_future = None
        pending = self._autosave_pending
        self._autosave_pending = False
        if error:
            return
        if (
            self._project is not None
            and path == self._project_path
            and revision == self._project_revision
        ):
            self._project_dirty = False
            self._emit(self._on_project_changed)
            return
        if pending or self._project_dirty:
            self._emit(self._on_autosave_retry)

    def wait_for_autosave(self) -> None:
        future = self._autosave_future
        if future is None:
            return
        token = (self._autosave_revision, self._autosave_path)
        self._ignored_autosaves.add(token)
        try:
            future.result()
        except Exception:
            pass
        if self._autosave_future is future:
            self._autosave_future = None
        self._autosave_pending = False

    def shutdown(self) -> None:
        self.wait_for_autosave()
        self._autosave_executor.shutdown(wait=True, cancel_futures=False)
