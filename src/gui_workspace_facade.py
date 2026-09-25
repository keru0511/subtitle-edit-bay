from __future__ import annotations

from typing import TYPE_CHECKING

from copy import deepcopy
from typing import Any

from PySide6.QtCore import (
    Property,
    Signal,
    Slot,
)

from .editor_workspace import (
    EditModeCapabilities,
    TimeMapping,
    build_edit_mode_capabilities,
)
from .video_timeline import VideoTimeline, VideoTimelineError, timeline_from_project

from .gui_feature_facade import FeatureFacade

if TYPE_CHECKING:
    from .gui import EditBayBackend


class WorkspaceFacade(FeatureFacade):
    """編集モード・再生位置・カット編集の画面窓口。"""

    cutTimelineChanged = Signal()
    editorCapabilitiesChanged = Signal()
    editorModeChanged = Signal()
    editorPlayheadChanged = Signal()
    workspaceChanged = Signal()
    workspacePlayerStateChanged = Signal()

    def __init__(self, backend: "EditBayBackend") -> None:
        super().__init__(backend)
        backend.cutTimelineChanged.connect(self.cutTimelineChanged.emit)
        backend.editorCapabilitiesChanged.connect(self.editorCapabilitiesChanged.emit)
        backend.editorModeChanged.connect(self.editorModeChanged.emit)
        backend.editorPlayheadChanged.connect(self.editorPlayheadChanged.emit)
        backend.workspaceChanged.connect(self.workspaceChanged.emit)
        backend.workspacePlayerStateChanged.connect(self.workspacePlayerStateChanged.emit)

    @Property(str, notify=workspaceChanged)
    def currentWorkspace(self) -> str:
        """Return the backend-owned workspace identity for QML."""

        backend = self._backend

        return backend._workspace_navigation.current_workspace

    @Property("QVariantMap", notify=workspacePlayerStateChanged)
    def workspacePlayerState(self) -> dict[str, Any]:
        """Return the active workspace player state at the navigation boundary."""

        backend = self._backend

        return backend._workspace_navigation.current_player.as_dict()

    @Property("QVariantMap", notify=workspacePlayerStateChanged)
    def workspacePlayerStates(self) -> dict[str, dict[str, Any]]:
        """Expose isolated transport snapshots for diagnostics and QML."""

        backend = self._backend

        return backend._workspace_navigation.player_states()

    @Slot(str, int, bool, result=bool)
    def setWorkspacePlayerState(
        self,
        workspace: str,
        position_ms: int,
        playing: bool,
    ) -> bool:
        backend = self._backend
        changed = backend._workspace_navigation.update_player_state(
            workspace,
            position_ms,
            playing=playing,
        )
        if changed:
            backend.workspacePlayerStateChanged.emit()
        return changed

    @Slot(str, result=bool)
    def switchWorkspace(self, workspace: str) -> bool:
        backend = self._backend
        result = backend._workspace_navigation.switch_workspace(
            workspace,
            running=backend._running,
            active_job=backend._active_job,
        )
        if not result.accepted:
            backend._set_status(result.reason, "BUSY" if backend._running or backend._active_job else "CHECK")
            return False
        if result.changed:
            backend.workspaceChanged.emit()
            backend.workspacePlayerStateChanged.emit()
        return True

    def _edit_mode_capabilities(self) -> EditModeCapabilities:
        backend = self._backend
        return build_edit_mode_capabilities(
            project_loaded=backend.projectLoaded,
            preview_available=bool(backend.previewUrl),
            audio_available=backend.audio.audioMixerAvailable,
            cut_available=backend._cut_editor_available,
        )

    def _refresh_editor_workspace(self) -> None:
        backend = self._backend
        mode_changed = backend._editor_workspace.ensure_available_mode(self._edit_mode_capabilities())
        backend.editorCapabilitiesChanged.emit()
        if mode_changed:
            backend.editorModeChanged.emit()

    @Property(str, notify=editorModeChanged)
    def currentEditMode(self) -> str:
        backend = self._backend
        return backend._editor_workspace.current_mode

    @Property("QVariantMap", notify=editorCapabilitiesChanged)
    def editorModeCapabilities(self) -> dict[str, object]:
        return self._edit_mode_capabilities().as_dict()

    @Property("QVariantMap", notify=editorPlayheadChanged)
    def editorPlayhead(self) -> dict[str, object]:
        backend = self._backend
        return backend._editor_workspace.playhead

    def _cut_timeline_model(self) -> VideoTimeline:
        backend = self._backend
        if backend._project is None:
            return VideoTimeline.from_json(None, source_duration=0.0)
        return timeline_from_project(backend._project)

    @Property("QVariantMap", notify=cutTimelineChanged)
    def cutTimeline(self) -> dict[str, Any]:
        return self._cut_timeline_model().as_view()

    @Property(float, notify=cutTimelineChanged)
    def cutOutputDuration(self) -> float:
        return self._cut_timeline_model().output_duration

    @Slot(int, result=int)
    def sourceTimeToOutputMs(self, position_ms: int) -> int:
        return self._cut_timeline_model().source_to_output(position_ms)

    @Slot(int, result=int)
    def outputTimeToSourceMs(self, position_ms: int) -> int:
        return self._cut_timeline_model().output_to_source(position_ms)

    @Slot(int, result=bool)
    def isSourceTimeCut(self, position_ms: int) -> bool:
        return self._cut_timeline_model().contains_source_seconds(position_ms / 1000.0)

    @Slot(int, result=int)
    def nextCutPreviewSourceMs(self, position_ms: int) -> int:
        source_position = self._cut_timeline_model().next_playable_source_seconds(position_ms / 1000.0)
        return int(round(source_position * 1000))

    @Slot(str, result=bool)
    def selectEditMode(self, mode: str) -> bool:
        backend = self._backend
        changed = backend._editor_workspace.select_mode(mode, self._edit_mode_capabilities())
        if changed:
            backend.editorModeChanged.emit()
        return changed

    @Slot(int, str, result=bool)
    def setEditorPlayhead(self, position_ms: int, basis: str) -> bool:
        backend = self._backend
        changed = backend._editor_workspace.set_playhead(position_ms, basis)
        if changed:
            backend.editorPlayheadChanged.emit()
        return changed

    def set_editor_time_mapping(self, mapping: TimeMapping | None) -> None:
        """Install the source/output time mapper supplied by the cut editor."""

        backend = self._backend

        backend._editor_workspace.set_mapping(mapping)
        backend.editorPlayheadChanged.emit()

    def _sync_project_timeline(self) -> None:
        backend = self._backend
        self.set_editor_time_mapping(self._cut_timeline_model() if backend._project is not None else None)
        backend.cutTimelineChanged.emit()

    def set_cut_editor_available(self, available: bool) -> None:
        """Enable the cut mode when the non-destructive cut editor is connected."""

        backend = self._backend

        available = bool(available)
        if backend._cut_editor_available == available:
            return
        backend._cut_editor_available = available
        self._refresh_editor_workspace()

    def _reset_editor_timing(self) -> None:
        backend = self._backend
        backend._editor_workspace.set_mapping(None)
        backend._editor_workspace.reset_playhead()
        backend.editorPlayheadChanged.emit()

    def _replace_timeline(self, payload: dict[str, Any]) -> None:
        backend = self._backend
        source_duration = self._cut_timeline_model().source_duration
        timeline = VideoTimeline.from_json(
            payload,
            source_duration=source_duration,
        )
        backend._project_editor_controller.replace_timeline(timeline.to_json())
        self.set_editor_time_mapping(timeline)
        backend.cutTimelineChanged.emit()

    def _commit_timeline(self, timeline: VideoTimeline, status: str) -> bool:
        backend = self._backend
        if backend._project is None:
            return False
        before = deepcopy(backend._project.get("timeline", {}))
        after = timeline.to_json()
        if before == after:
            return False
        backend.subtitles._record_timeline_history(before, after)
        self._replace_timeline(after)
        backend._set_status(status, "EDIT")
        return True

    @Slot(float, float, result=bool)
    def addCut(self, source_start: float, source_end: float) -> bool:
        backend = self._backend
        if backend._project is None or backend._running:
            return False
        try:
            timeline = self._cut_timeline_model().add_cut(source_start, source_end)
        except VideoTimelineError as error:
            backend._set_status(f"カット範囲を追加できません: {error}", "CHECK")
            return False
        return self._commit_timeline(timeline, "カット範囲を追加しました")

    @Slot(str, float, float, result=bool)
    def updateCutRange(self, cut_id: str, source_start: float, source_end: float) -> bool:
        backend = self._backend
        if backend._project is None or backend._running:
            return False
        try:
            timeline = self._cut_timeline_model().update_cut(
                str(cut_id),
                source_start,
                source_end,
            )
        except VideoTimelineError as error:
            backend._set_status(f"カット範囲を変更できません: {error}", "CHECK")
            return False
        return self._commit_timeline(timeline, "カット範囲を変更しました")

    @Slot(str, result=bool)
    def restoreCut(self, cut_id: str) -> bool:
        backend = self._backend
        if backend._project is None or backend._running:
            return False
        try:
            timeline = self._cut_timeline_model().restore_cut(str(cut_id))
        except VideoTimelineError as error:
            backend._set_status(f"カット範囲を復元できません: {error}", "CHECK")
            return False
        return self._commit_timeline(timeline, "カット範囲を復元しました")

    @Slot(float, float, result=bool)
    def restoreRange(self, source_start: float, source_end: float) -> bool:
        backend = self._backend
        if backend._project is None or backend._running:
            return False
        try:
            timeline = self._cut_timeline_model().restore_range(source_start, source_end)
        except VideoTimelineError as error:
            backend._set_status(f"範囲を復元できません: {error}", "CHECK")
            return False
        return self._commit_timeline(timeline, "選択範囲を復元しました")

    @Slot(result=bool)
    def clearCuts(self) -> bool:
        backend = self._backend
        if backend._project is None or backend._running:
            return False
        return self._commit_timeline(
            self._cut_timeline_model().clear_cuts(),
            "すべてのカットを解除しました",
        )
