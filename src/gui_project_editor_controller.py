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

from .audio_mixer import AUDIO_CHANNEL_CHANGE_FIELDS
from .subtitle_project import (
    SubtitleProjectError,
    assign_project_layout_rows,
    load_project,
    normalize_segment,
    save_project,
)
from .video_timeline import VideoTimeline, timeline_from_project
from .short_video_schema import ShortVideo
from .video_sequence import VideoSequence, VideoSequenceError


SaveProject = Callable[..., Path]
LoadProject = Callable[..., dict[str, Any]]
LayoutRows = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
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
        assign_project_layout_rows_fn: LayoutRows = assign_project_layout_rows,
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
        self._assign_project_layout_rows_fn = assign_project_layout_rows_fn
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

    def _commit_edit(
        self,
        updates: dict[str, Any],
        *,
        history: dict[str, Any] | None = None,
        selected_index: int | None = None,
        remove_fields: tuple[str, ...] = (),
        history_move: str | None = None,
    ) -> bool:
        """準備・検証済みの変更を確定し、履歴・保存予約・通知を一括更新する。"""
        if self._project is None:
            return False
        changed = any(key not in self._project or self._project[key] != value for key, value in updates.items())
        changed = changed or any(key in self._project for key in remove_fields)
        if not changed and history_move is None:
            return False
        self._project.update(updates)
        for key in remove_fields:
            self._project.pop(key, None)
        if selected_index is not None:
            self._selected_segment_index = selected_index
        if history is not None:
            self._undo_stack.append(history)
            del self._undo_stack[:-100]
            self._redo_stack.clear()
        if history_move == "undo":
            self._redo_stack.append(self._undo_stack.pop())
        elif history_move == "redo":
            self._undo_stack.append(self._redo_stack.pop())
        # 通知先が参照する文書・履歴・revisionをすべて確定してから公開する。
        self._project_revision += 1
        self._project_dirty = True
        self._emit(self._on_project_changed)
        if "segments" in updates:
            self._emit(self._on_segments_changed)
            self._emit(self._on_selection_changed)
        else:
            self._emit(self._on_project_data_changed)
        if history is not None or history_move is not None:
            self._emit(self._on_history_changed)
        self._emit(self._on_dirty)
        return True

    def commit_section_change(self, section: str, payload: dict[str, Any]) -> bool:
        """音量・ショート編集を、呼び出し元の辞書から切り離して確定する。"""
        if self._project is None:
            return False
        if section not in {"audio_mix", "short_video"}:
            raise SubtitleProjectError(f"未対応の編集対象です: {section}")
        after = deepcopy(payload)
        if section == "short_video":
            ShortVideo.from_json(after)
        before = deepcopy(self._project.get(section, {}))
        return self._commit_edit(
            {section: after},
            history={
                "kind": section, "before": before, "after": deepcopy(after),
                "before_missing": section not in self._project,
            },
        )

    def _prepare_segments(
        self, segments: list[dict[str, Any]], selected_id: str | None,
        *, reflow_layout: bool,
    ) -> tuple[list[dict[str, Any]], int]:
        # レイアウト計算は辞書を更新するため、保存中・編集中の正本に触れない。
        ordered = sorted(
            (dict(item) for item in segments),
            key=lambda item: (item["start"], item["end"], item["id"]),
        )
        ids = [str(item["id"]) for item in ordered]
        if len(ids) != len(set(ids)):
            raise SubtitleProjectError("segment ids must be unique")
        if reflow_layout:
            ordered = self._assign_project_layout_rows_fn(ordered)
        originals = {str(item["id"]): item for item in segments}
        ordered = [originals[str(item["id"])] if item == originals[str(item["id"])] else item for item in ordered]
        selected = self._selected_segment_index
        if selected_id:
            selected = next((index for index, item in enumerate(ordered) if item["id"] == selected_id), -1)
        elif selected >= len(ordered):
            selected = len(ordered) - 1
        return ordered, selected

    def replace_segments(
        self, segments: list[dict[str, Any]], selected_id: str | None = None,
        *, reflow_layout: bool = True,
    ) -> None:
        if self._project is None:
            return
        ordered, selected = self._prepare_segments(segments, selected_id, reflow_layout=reflow_layout)
        self._commit_edit({"segments": ordered}, selected_index=selected)

    def commit_segment_change(
        self, before: list[dict[str, Any]], after: list[dict[str, Any]],
        selected_id: str | None = None, *, reflow_layout: bool = True,
    ) -> None:
        if self._project is None:
            return
        normalized = [normalize_segment(item, index) for index, item in enumerate(after)]
        affected_ids = {str(item["id"]) for item in [*before, *normalized]}
        current = self._project["segments"]
        segments = [item for item in current if str(item["id"]) not in affected_ids]
        segments.extend(normalized)
        ordered, selected = self._prepare_segments(segments, selected_id, reflow_layout=reflow_layout)
        history = {
            "kind": "segments",
            "before": deepcopy([item for item in current if str(item["id"]) in affected_ids]),
            "after": deepcopy(normalized),
            "reflow_layout": reflow_layout,
        }
        self._commit_edit({"segments": ordered}, history=history, selected_index=selected)

    def _prepare_timeline(self, payload: dict[str, Any]) -> dict[str, Any]:
        duration = timeline_from_project(self._project).source_duration
        return VideoTimeline.from_json(payload, source_duration=duration).to_json()

    def replace_timeline(self, payload: dict[str, Any]) -> None:
        if self._project is not None:
            self._commit_edit({"timeline": self._prepare_timeline(payload)})

    def commit_timeline_change(self, payload: dict[str, Any]) -> bool:
        if self._project is None:
            return False
        after = self._prepare_timeline(payload)
        return self._commit_edit(
            {"timeline": after},
            history={"kind": "timeline", "before": deepcopy(self._project.get("timeline", {})),
                     "after": deepcopy(after)},
        )

    def sequence_model(self) -> VideoSequence:
        """Return the current sequence domain model at the controller boundary."""

        if self._project is None:
            return VideoSequence()
        video = self._project.get("video")
        legacy_video = video if isinstance(video, dict) else None
        try:
            return VideoSequence.from_json(
                self._project.get("sequence"),
                legacy_video=legacy_video,
            )
        except VideoSequenceError as error:
            raise SubtitleProjectError(str(error)) from error

    def replace_sequence(self, payload: dict[str, Any]) -> None:
        if self._project is None:
            return
        try:
            sequence = VideoSequence.from_json(payload)
        except VideoSequenceError as error:
            raise SubtitleProjectError(str(error)) from error
        self._commit_edit({"sequence": sequence.to_json()})

    def commit_sequence_change(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> None:
        if self._project is None:
            return
        try:
            before_payload = VideoSequence.from_json(before).to_json()
            after_payload = VideoSequence.from_json(after).to_json()
        except VideoSequenceError as error:
            raise SubtitleProjectError(str(error)) from error
        if before_payload == after_payload:
            return
        self._commit_edit(
            {"sequence": after_payload},
            history={"kind": "sequence", "before": before_payload, "after": deepcopy(after_payload)},
        )

    def apply_sequence_mutation(
        self,
        mutation: Callable[[VideoSequence], VideoSequence],
    ) -> VideoSequence | None:
        """Apply one pure sequence operation and connect it to history/dirty state."""

        if self._project is None:
            return None
        before = self.sequence_model()
        after = before.apply(mutation)
        before_payload = before.to_json()
        after_payload = after.to_json()
        if before_payload != after_payload:
            self.commit_sequence_change(before_payload, after_payload)
        return after

    def _restore_audio_mix_settings(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """履歴の操作値だけを戻し、再リンク後の素材参照とチャンネル構成を維持する。"""
        restored = deepcopy(self._project.get("audio_mix", {}))
        historical_channels = {
            channel["id"]: channel for channel in snapshot.get("channels", [])
        }
        for channel in restored.get("channels", []):
            historical = historical_channels.get(channel["id"])
            if historical is None:
                continue
            for field in AUDIO_CHANNEL_CHANGE_FIELDS:
                if field in historical:
                    channel[field] = historical[field]
        restored["customized"] = bool(snapshot.get("customized", False))
        return restored

    def apply_history_entry(
        self, entry: dict[str, Any], state: str, *, history_move: str | None = None,
    ) -> None:
        if self._project is None:
            return
        kind = entry.get("kind")
        selected = None
        remove_fields: tuple[str, ...] = ()
        if kind in {"audio_mix", "short_video"}:
            if state == "before" and entry.get("before_missing"):
                updates = {}
                remove_fields = (kind,)
            elif kind == "audio_mix":
                updates = {kind: self._restore_audio_mix_settings(entry.get(state, {}))}
            else:
                updates = {kind: deepcopy(entry.get(state, {}))}
        elif kind == "timeline":
            updates = {"timeline": self._prepare_timeline(entry.get(state, {}))}
        elif kind == "sequence":
            try:
                updates = {"sequence": VideoSequence.from_json(entry.get(state, {})).to_json()}
            except VideoSequenceError as error:
                raise SubtitleProjectError(str(error)) from error
        else:
            affected_ids = {str(item["id"]) for item in [*entry.get("before", []), *entry.get("after", [])]}
            segments = [item for item in self._project["segments"] if str(item["id"]) not in affected_ids]
            segments.extend(deepcopy(entry.get(state, [])))
            ordered, selected = self._prepare_segments(
                segments, None, reflow_layout=bool(entry.get("reflow_layout", True)),
            )
            updates = {"segments": ordered}
        # 復元候補の検証が完了するまでは文書も履歴スタックも変更しない。
        self._commit_edit(
            updates, selected_index=selected, remove_fields=remove_fields, history_move=history_move,
        )
        self._emit(self._on_history_applied, entry, state)

    def undo(self) -> bool:
        if self._project is None or not self._undo_stack:
            return False
        self.apply_history_entry(self._undo_stack[-1], "before", history_move="undo")
        return True

    def redo(self) -> bool:
        if self._project is None or not self._redo_stack:
            return False
        self.apply_history_entry(self._redo_stack[-1], "after", history_move="redo")
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
            # The Qt signal below is queued back to the facade's thread.  Mark
            # a matching successful revision clean before emitting it so a
            # caller observing a completed Future cannot see stale dirty
            # state while that queued notification is waiting to run.
            if (
                not error
                and self._project is not None
                and path == self._project_path
                and revision == self._project_revision
            ):
                self._project_dirty = False
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
