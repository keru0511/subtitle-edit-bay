from __future__ import annotations

from dataclasses import replace
import json
import math
import os
import subprocess
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

# PySide6 exposes typing.Self on Python 3.10. Initialize the optional backport
# first so PyTorch keeps its compatible Self implementation when WhisperX is
# installed. Lightweight development/test environments may omit it.
try:
    from typing_extensions import Self as _TypingSelf  # noqa: F401
except ImportError:  # pragma: no cover - release runtimes always lock it
    _TypingSelf = None  # type: ignore[assignment]

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QModelIndex,
    QObject,
    QProcess,
    QTimer,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QDesktopServices, QFontDatabase
from PySide6.QtMultimedia import QAudioBuffer, QAudioBufferOutput
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QFileDialog

from .platform_paths import audio_preview_directory

from .audio_mixer import (
    reconcile_audio_mix,
)
from .audio_mix_proposal import (
    AudioMixProposal,
)
from .audio_preview_cache import (
    AudioPreviewCacheResult,
    clear_audio_preview_cache,
    prepare_audio_preview_cache,
)
from .gui_codex_state import (
    CodexSessionController,
    CodexSessionSnapshot,
)
from .codex_app_server_client import CodexAppServerClient
from .codex_actions import ActionResult, ActionScope, build_gui_action_dispatcher
from .gui_codex_chat_state import (
    CodexChatController,
    CodexChatSnapshot,
)
from .gui_ai_chat_state import AIProviderChatRouter
from .gemini_acp_provider import GeminiAcpProvider
from .gui_audio_preview_controller import AudioPreviewController
from .gui_project_editor_controller import ProjectEditorController
from .application_logging import ApplicationLogger, ProcessDiagnosticSnapshot
from .application_info import resolve_application_info
from .realtime_audio_mixer import RealtimeAudioMixer
from .color_config import normalize_rgb_color
from .gui_base import APP_TITLE, LegacyEditBayBackend
from .gui_source_state import SourceSelection, build_speaker_entries_from_files
from .gui_workspace_controller import WorkspaceNavigationController
from .editor_workspace import (
    EditModeCapabilities,
    EditorWorkspaceState,
    TimeMapping,
)
from .workflow_actions import (
    ActionCapability,
)
from .media_probe import probe_media_duration
from .subtitle_project import (
    SubtitleProjectError,
    assign_project_layout_rows,
    create_project,
    derive_project_path,
    load_project,
    project_work_directory,
    save_project,
)
from .processing_progress import ProcessingProgress
from .subtitle_line_count import segment_editor_text
from .render_ass import style_name_for_speaker
from .runtime_dependencies import runtime_diagnostic_info
from .video_timeline import VideoTimeline
from .video_sequence import VideoSequence, VideoSequenceError
from . import updater

from .gui_workspace_facade import WorkspaceFacade
from .gui_subtitles_facade import SubtitleFacade
from .gui_short_video_facade import ShortVideoFacade
from .gui_audio_facade import AudioFacade
from .gui_sequence_facade import SequenceFacade
from .gui_workflow_facade import WorkflowFacade
from .gui_ai_facade import AIChatFacade
from .gui_updates_facade import UpdateFacade


def build_font_choices(font_families: list[str]) -> list[dict[str, str]]:
    unique: dict[str, str] = {}
    for value in font_families:
        family = str(value).strip()
        if not family or family.startswith("@"):
            continue
        unique.setdefault(family.casefold(), family)
    families = sorted(unique.values(), key=str.casefold)
    return [
        {"label": "既定フォント", "family": ""},
        *({"label": family, "family": family} for family in families),
    ]


class SubtitleListModel(QAbstractListModel):
    SegmentIdRole = Qt.ItemDataRole.UserRole + 1
    StartRole = SegmentIdRole + 1
    EndRole = SegmentIdRole + 2
    TextRole = SegmentIdRole + 3
    SpeakerRole = SegmentIdRole + 4
    LayoutRowRole = SegmentIdRole + 5
    FontScaleRole = SegmentIdRole + 6
    FontFamilyRole = SegmentIdRole + 7
    EditorTextRole = SegmentIdRole + 8

    _ROLE_NAMES = {
        SegmentIdRole: b"segmentId",
        StartRole: b"start",
        EndRole: b"end",
        TextRole: b"text",
        SpeakerRole: b"speaker",
        LayoutRowRole: b"layoutRow",
        FontFamilyRole: b"subtitleFontFamily",
        FontScaleRole: b"subtitleFontScale",
        EditorTextRole: b"editorText",
    }

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._segments: list[dict[str, Any]] = []

    def roleNames(self) -> dict[int, bytes]:
        return self._ROLE_NAMES

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._segments)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._segments):
            return None
        segment = self._segments[index.row()]
        if role == self.SegmentIdRole:
            return str(segment["id"])
        if role == self.StartRole:
            return float(segment["start"])
        if role == self.EndRole:
            return float(segment["end"])
        if role == self.TextRole:
            return str(segment.get("text", ""))
        if role == self.EditorTextRole:
            return segment_editor_text(segment)
        if role == self.SpeakerRole:
            return str(segment.get("speaker", ""))
        if role == self.LayoutRowRole:
            return int(segment.get("layout_row", 0))
        if role == self.FontScaleRole:
            return float(segment.get("subtitle_font_scale", 1.0))
        if role == self.FontFamilyRole:
            return str(segment.get("subtitle_font_family", ""))
        return None

    def set_segments(self, segments: list[dict[str, Any]]) -> None:
        incoming = list(segments)
        old_ids = [str(item["id"]) for item in self._segments]
        new_ids = [str(item["id"]) for item in incoming]

        if old_ids == new_ids:
            changed = [
                index
                for index, (old, new) in enumerate(zip(self._segments, incoming))
                if old != new
            ]
            self._segments = incoming
            if changed:
                range_start = range_end = changed[0]
                for index in changed[1:]:
                    if index == range_end + 1:
                        range_end = index
                        continue
                    self.dataChanged.emit(
                        self.index(range_start, 0),
                        self.index(range_end, 0),
                        list(self._ROLE_NAMES),
                    )
                    range_start = range_end = index
                self.dataChanged.emit(
                    self.index(range_start, 0),
                    self.index(range_end, 0),
                    list(self._ROLE_NAMES),
                )
            return

        if len(new_ids) == len(old_ids) + 1:
            insert_at = next(
                (index for index, item in enumerate(new_ids) if index >= len(old_ids) or old_ids[index] != item),
                len(old_ids),
            )
            if old_ids == new_ids[:insert_at] + new_ids[insert_at + 1:]:
                self.beginInsertRows(QModelIndex(), insert_at, insert_at)
                self._segments = incoming
                self.endInsertRows()
                self.dataChanged.emit(
                    self.index(0, 0),
                    self.index(len(incoming) - 1, 0),
                    list(self._ROLE_NAMES),
                )
                return

        if len(old_ids) == len(new_ids) + 1:
            remove_at = next(
                (index for index, item in enumerate(old_ids) if index >= len(new_ids) or new_ids[index] != item),
                len(new_ids),
            )
            if new_ids == old_ids[:remove_at] + old_ids[remove_at + 1:]:
                self.beginRemoveRows(QModelIndex(), remove_at, remove_at)
                self._segments = incoming
                self.endRemoveRows()
                if incoming:
                    self.dataChanged.emit(
                        self.index(0, 0),
                        self.index(len(incoming) - 1, 0),
                        list(self._ROLE_NAMES),
                    )
                return

        if len(old_ids) == len(new_ids) and set(old_ids) == set(new_ids):
            first_mismatch = next(
                index for index, item in enumerate(new_ids) if old_ids[index] != item
            )
            moves = [
                (old_ids.index(new_ids[first_mismatch]), first_mismatch),
                (first_mismatch, new_ids.index(old_ids[first_mismatch])),
            ]
            for source, destination in moves:
                candidate = list(old_ids)
                moved = candidate.pop(source)
                candidate.insert(destination, moved)
                if candidate != new_ids:
                    continue
                destination_child = destination + 1 if source < destination else destination
                self.beginMoveRows(
                    QModelIndex(),
                    source,
                    source,
                    QModelIndex(),
                    destination_child,
                )
                self._segments = incoming
                self.endMoveRows()
                if incoming:
                    self.dataChanged.emit(
                        self.index(0, 0),
                        self.index(len(incoming) - 1, 0),
                        list(self._ROLE_NAMES),
                    )
                return

        self.beginResetModel()
        self._segments = incoming
        self.endResetModel()


class ShortVideoClipListModel(QAbstractListModel):
    ClipDataRole = Qt.ItemDataRole.UserRole + 1

    _ROLE_NAMES = {
        ClipDataRole: b"clipData",
    }

    def __init__(
        self,
        count_resolver: Callable[[], int],
        data_resolver: Callable[[int], dict[str, Any]],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._count_resolver = count_resolver
        self._data_resolver = data_resolver
        self._count = 0

    def roleNames(self) -> dict[int, bytes]:
        return self._ROLE_NAMES

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else self._count

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if (
            role != self.ClipDataRole
            or not index.isValid()
            or not 0 <= index.row() < self._count
        ):
            return None
        return self._data_resolver(index.row())

    def refresh(self) -> None:
        incoming_count = max(0, int(self._count_resolver()))
        if incoming_count != self._count:
            self.beginResetModel()
            self._count = incoming_count
            self.endResetModel()
            return
        if self._count:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(self._count - 1, 0),
                [self.ClipDataRole],
            )


class EditBayBackend(LegacyEditBayBackend):
    projectChanged = Signal()
    projectDataChanged = Signal()
    segmentsChanged = Signal()
    historyChanged = Signal()
    selectionChanged = Signal()
    activeJobChanged = Signal()
    assPathChanged = Signal()
    audioPreviewLevelsChanged = Signal()
    audioMixerPreviewChannelsChanged = Signal()
    audioMixerPreviewGainsChanged = Signal()
    audioPreviewCacheChanged = Signal()
    audioMasterMetricsChanged = Signal()
    audioPreviewCacheCompleted = Signal(int, object)
    autosaveCompleted = Signal(int, str, str)
    applicationLogChanged = Signal()
    lastProcessDiagnosticChanged = Signal()
    updateInfoChanged = Signal()
    updateBusyChanged = Signal()
    updateErrorChanged = Signal()
    updateCheckFinished = Signal(object, str)
    updateDownloadProgressEvent = Signal(int, int, float)
    updateDownloadFinished = Signal(str, str)
    updateDownloadProgressChanged = Signal()
    updatePackageReadyChanged = Signal()
    shortVideoChanged = Signal()
    shortVideoClipDataChanged = Signal()
    codexStateChanged = Signal()
    codexMessageChanged = Signal()
    codexProposalChanged = Signal()
    audioMixProposalChanged = Signal()
    codexChatChanged = Signal()
    aiChatChanged = Signal()
    codexCallbackRequested = Signal(object)
    highlightCandidatesChanged = Signal()
    highlightAnalysisChanged = Signal()
    progressDetailsChanged = Signal()
    editorModeChanged = Signal()
    editorCapabilitiesChanged = Signal()
    editorPlayheadChanged = Signal()
    cutTimelineChanged = Signal()
    actionCapabilitiesChanged = Signal()
    workspaceChanged = Signal()
    workspacePlayerStateChanged = Signal()
    sequenceChanged = Signal()

    @Property(QObject, constant=True)
    def workspace(self) -> WorkspaceFacade:
        return self._workspace_facade

    @Property(QObject, constant=True)
    def subtitles(self) -> SubtitleFacade:
        return self._subtitles_facade

    @Property(QObject, constant=True)
    def shortVideo(self) -> ShortVideoFacade:
        return self._shortVideo_facade

    @Property(QObject, constant=True)
    def audio(self) -> AudioFacade:
        return self._audio_facade

    @Property(QObject, constant=True)
    def sequence(self) -> SequenceFacade:
        return self._sequence_facade

    @Property(QObject, constant=True)
    def workflow(self) -> WorkflowFacade:
        return self._workflow_facade

    @Property(QObject, constant=True)
    def ai(self) -> AIChatFacade:
        return self._ai_facade

    @Property(QObject, constant=True)
    def updates(self) -> UpdateFacade:
        return self._updates_facade

    @property
    def _project(self) -> dict[str, Any] | None:
        controller = getattr(self, "_project_editor_controller", None)
        if controller is not None:
            return controller.project
        return getattr(self, "_project_value", None)

    @_project.setter
    def _project(self, value: dict[str, Any] | None) -> None:
        controller = getattr(self, "_project_editor_controller", None)
        if controller is None:
            self._project_value = value
        else:
            controller.project = value
        if hasattr(self, "_codex_audio_mix_session"):
            self._codex_audio_mix_session.stop()
        controller = getattr(self, "_audio_preview_controller", None)
        if controller is not None:
            controller.set_project(value)

    @property
    def _project_path(self) -> str:
        controller = getattr(self, "_project_editor_controller", None)
        return controller.project_path if controller is not None else getattr(self, "_project_path_value", "")

    @_project_path.setter
    def _project_path(self, value: str | Path) -> None:
        controller = getattr(self, "_project_editor_controller", None)
        if controller is None:
            self._project_path_value = str(value)
        else:
            controller.project_path = value

    @property
    def _project_dirty(self) -> bool:
        controller = getattr(self, "_project_editor_controller", None)
        return controller.project_dirty if controller is not None else bool(getattr(self, "_project_dirty_value", False))

    @_project_dirty.setter
    def _project_dirty(self, value: bool) -> None:
        controller = getattr(self, "_project_editor_controller", None)
        if controller is None:
            self._project_dirty_value = bool(value)
        else:
            controller.project_dirty = value

    @property
    def _project_revision(self) -> int:
        controller = getattr(self, "_project_editor_controller", None)
        return controller.project_revision if controller is not None else int(getattr(self, "_project_revision_value", 0))

    @_project_revision.setter
    def _project_revision(self, value: int) -> None:
        controller = getattr(self, "_project_editor_controller", None)
        if controller is None:
            self._project_revision_value = int(value)
        else:
            controller.project_revision = value

    @property
    def _undo_stack(self) -> list[dict[str, Any]]:
        controller = getattr(self, "_project_editor_controller", None)
        return controller.undo_stack if controller is not None else getattr(self, "_undo_stack_value", [])

    @_undo_stack.setter
    def _undo_stack(self, value: list[dict[str, Any]]) -> None:
        controller = getattr(self, "_project_editor_controller", None)
        if controller is None:
            self._undo_stack_value = value
        else:
            controller.undo_stack.clear()
            controller.undo_stack.extend(value)

    @property
    def _redo_stack(self) -> list[dict[str, Any]]:
        controller = getattr(self, "_project_editor_controller", None)
        return controller.redo_stack if controller is not None else getattr(self, "_redo_stack_value", [])

    @_redo_stack.setter
    def _redo_stack(self, value: list[dict[str, Any]]) -> None:
        controller = getattr(self, "_project_editor_controller", None)
        if controller is None:
            self._redo_stack_value = value
        else:
            controller.redo_stack.clear()
            controller.redo_stack.extend(value)

    @property
    def _selected_segment_index(self) -> int:
        controller = getattr(self, "_project_editor_controller", None)
        return controller.selected_segment_index if controller is not None else int(getattr(self, "_selected_segment_index_value", -1))

    @_selected_segment_index.setter
    def _selected_segment_index(self, value: int) -> None:
        controller = getattr(self, "_project_editor_controller", None)
        if controller is None:
            self._selected_segment_index_value = int(value)
        else:
            controller.selected_segment_index = value

    @property
    def _autosave_future(self) -> Future[Path] | None:
        return self._project_editor_controller.autosave_future

    @_autosave_future.setter
    def _autosave_future(self, value: Future[Path] | None) -> None:
        self._project_editor_controller.autosave_future = value

    @property
    def _autosave_revision(self) -> int:
        return self._project_editor_controller.autosave_revision

    @_autosave_revision.setter
    def _autosave_revision(self, value: int) -> None:
        self._project_editor_controller.autosave_revision = value

    @property
    def _autosave_path(self) -> str:
        return self._project_editor_controller.autosave_path

    @_autosave_path.setter
    def _autosave_path(self, value: str | Path) -> None:
        self._project_editor_controller.autosave_path = value

    @property
    def _autosave_pending(self) -> bool:
        return self._project_editor_controller.autosave_pending

    @_autosave_pending.setter
    def _autosave_pending(self, value: bool) -> None:
        self._project_editor_controller.autosave_pending = value

    @property
    def _ignored_autosaves(self) -> set[tuple[int, str]]:
        return self._project_editor_controller.ignored_autosaves

    @property
    def _autosave_executor(self) -> ThreadPoolExecutor:
        return self._project_editor_controller.autosave_executor

    @property
    def audio_preview_cache_root(self) -> Path:
        controller = getattr(self, "_audio_preview_controller", None)
        if controller is not None:
            return controller.cache_root
        return getattr(self, "_audio_preview_cache_root", Path())

    @audio_preview_cache_root.setter
    def audio_preview_cache_root(self, value: str | Path) -> None:
        root = Path(value)
        self._audio_preview_cache_root = root
        controller = getattr(self, "_audio_preview_controller", None)
        if controller is not None:
            controller.cache_root = root

    @property
    def _audio_preview_cache_paths(self) -> dict[str, str]:
        return self._audio_preview_controller.cache_paths

    @_audio_preview_cache_paths.setter
    def _audio_preview_cache_paths(self, value: Mapping[str, str]) -> None:
        self._audio_preview_controller.set_cache_paths(value)

    @property
    def _audio_preview_cache_future(self) -> Future[AudioPreviewCacheResult] | None:
        return self._audio_preview_controller.cache_future

    @_audio_preview_cache_future.setter
    def _audio_preview_cache_future(self, value: Future[AudioPreviewCacheResult] | None) -> None:
        self._audio_preview_controller.cache_future = value

    @property
    def _audio_preview_cache_request(self) -> int:
        return self._audio_preview_controller.cache_request

    @_audio_preview_cache_request.setter
    def _audio_preview_cache_request(self, value: int) -> None:
        self._audio_preview_controller.cache_request = value

    @property
    def _audio_preview_generation(self) -> int:
        return self._audio_preview_controller.generation

    @property
    def _audio_preview_preparing(self) -> bool:
        return self._audio_preview_controller.preparing

    @_audio_preview_preparing.setter
    def _audio_preview_preparing(self, value: bool) -> None:
        self._audio_preview_controller.preparing = value

    @property
    def _audio_preview_outputs(self) -> dict[str, QAudioBufferOutput]:
        return self._audio_preview_controller.outputs

    @property
    def _audio_master_mixer(self) -> RealtimeAudioMixer:
        return self._audio_preview_controller.mixer

    @property
    def _audio_preview_gains(self) -> dict[str, float]:
        return self._audio_preview_controller.gains

    @property
    def _audio_preview_levels(self) -> dict[str, float]:
        return self._audio_preview_controller.levels

    @property
    def _audio_preview_pending_levels(self) -> dict[str, float]:
        return self._audio_preview_controller.pending_levels

    @property
    def _audio_preview_level_timer(self) -> QTimer:
        return self._audio_preview_controller.level_timer

    @property
    def _audio_master_level(self) -> float:
        return self._audio_preview_controller.master_level

    @_audio_master_level.setter
    def _audio_master_level(self, value: float) -> None:
        self._audio_preview_controller.master_level = value

    @property
    def _audio_limiter_reduction_db(self) -> float:
        return self._audio_preview_controller.limiter_reduction_db

    @_audio_limiter_reduction_db.setter
    def _audio_limiter_reduction_db(self, value: float) -> None:
        self._audio_preview_controller.limiter_reduction_db = value

    def __init__(self, argv: list[str], workspace_root: Path | None = None) -> None:
        resolved_workspace_root = (
            workspace_root or Path(__file__).resolve().parent.parent
        ).resolve()
        self._application_logger = ApplicationLogger(
            resolved_workspace_root,
            application_info=resolve_application_info(resolved_workspace_root),
        )
        self._application_logger.append(
            "アプリケーションの起動を開始しました",
            component="startup",
            stage="STARTUP",
            preserve_in_memory=True,
            pin_in_memory=True,
        )
        # Project document state is owned by ProjectEditorController.  Keep
        # the facade properties below for the existing QML/test contract.
        self._project_editor_controller: ProjectEditorController | None = None
        self._active_job = ""
        self._ass_path = ""
        self._loading_project_sources = False
        self._relinking_project_sources = False
        self._relink_source_selection: SourceSelection | None = None
        super().__init__(argv, workspace_root=resolved_workspace_root)
        self._workspace_facade = WorkspaceFacade(self)
        self._subtitles_facade = SubtitleFacade(self)
        self._shortVideo_facade = ShortVideoFacade(self)
        self._audio_facade = AudioFacade(self)
        self._sequence_facade = SequenceFacade(self)
        self._workflow_facade = WorkflowFacade(self)
        self._ai_facade = AIChatFacade(self)
        self._updates_facade = UpdateFacade(self)
        self._editor_workspace = EditorWorkspaceState()
        self._workspace_navigation = WorkspaceNavigationController()
        self._sequence_playhead_seconds = 0.0
        self._sequence_error = ""
        for signal in (
            self.dependenciesChanged, self.sourceSelectionChanged, self.speakersChanged,
            self.audioTracksChanged, self.settingsChanged, self.projectChanged,
            self.projectDataChanged, self.shortVideoChanged, self.runningChanged,
        ):
            signal.connect(self.actionCapabilitiesChanged.emit)
        # The sequence view is a facade snapshot.  ProjectEditorController
        # remains the only owner of persisted sequence mutations; these
        # notifications only tell QML to read a fresh view.
        self.projectChanged.connect(self.sequenceChanged.emit)
        self.projectDataChanged.connect(self.sequenceChanged.emit)
        self._cut_editor_available = True
        self.projectChanged.connect(self._refresh_editor_workspace)
        self.projectDataChanged.connect(self._refresh_editor_workspace)
        self.sourceSelectionChanged.connect(self._refresh_editor_workspace)
        self._processing_progress = ProcessingProgress()
        self._ffmpeg_duration_seconds = 0.0
        self._ffmpeg_duration_from_event = False
        self._processing_machine_event_seen = False
        self._application_logger.application_info = dict(self._application_info)
        self._last_process_diagnostic: ProcessDiagnosticSnapshot | None = None
        self._pending_process_error = ""
        self._process_output_tail = ""
        self._log = self._application_logger.text
        self._record_startup_diagnostics()
        self._font_choices = build_font_choices(QFontDatabase.families())
        self._subtitle_model = SubtitleListModel(self)
        self._segment_by_id: dict[str, dict[str, Any]] = {}
        self._short_video_clip_model = ShortVideoClipListModel(
            self._short_video_clip_count,
            self._short_video_clip_view_at,
            self,
        )
        self.shortVideoChanged.connect(self._refresh_short_video_clip_data)
        self._subtitle_layout_metrics: dict[str, float | int] = {
            "maxFontScale": 1.0,
            "maxLayoutRow": 0,
        }
        self._subtitle_preview_text_cache: dict[str, tuple[tuple[object, ...], str]] = {}
        self._segment_starts: list[float] = []
        self._segment_prefix_max_end: list[float] = []
        self._transcription_merge_mode = ""
        self._transcription_preserved_segments: list[dict[str, Any]] = []
        self._transcription_preserved_project: dict[str, Any] | None = None
        self._transcription_preserved_project_path = ""
        self._transcription_generated_project_path = ""
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(700)
        self.autosave_timer.timeout.connect(self._autosave_project)
        self._project_editor_controller = ProjectEditorController(
            resolved_workspace_root,
            # Resolve these names at call time so existing tests can patch
            # src.gui.save_project without changing the controller boundary.
            load_project_fn=lambda path, **kwargs: load_project(path, **kwargs),
            save_project_fn=lambda path, project, **kwargs: save_project(
                path,
                project,
                **kwargs,
            ),
            # Keep the existing module-level patch/extension point used by
            # the facade and GUI regression tests while the controller owns
            # the project edit operation.
            assign_project_layout_rows_fn=(
                lambda segments: assign_project_layout_rows(segments)
            ),
            on_project_changed=self.projectChanged.emit,
            on_project_data_changed=self.projectDataChanged.emit,
            on_segments_changed=self._on_project_segments_changed,
            on_history_changed=self.historyChanged.emit,
            on_selection_changed=self.selectionChanged.emit,
            on_autosave_completed=self.autosaveCompleted.emit,
            on_dirty=lambda: self.autosave_timer.start(),
            on_autosave_retry=lambda: QTimer.singleShot(0, self._autosave_project),
            on_history_applied=self._on_project_history_applied,
        )
        cache_root = audio_preview_directory()
        self._audio_preview_controller = AudioPreviewController(
            cache_root,
            parent=self,
            # Keep the existing module-level call surface available to tests
            # and callers while the controller owns the operation itself.
            prepare_cache=lambda project, root, protected_paths=None: prepare_audio_preview_cache(
                project,
                root,
                protected_paths=protected_paths,
            ),
            clear_cache=lambda root: clear_audio_preview_cache(root),
        )
        self.audio_preview_cache_root = cache_root
        self._audio_preview_controller.cacheChanged.connect(
            self.audioPreviewCacheChanged.emit
        )
        self._audio_preview_controller.previewChannelsChanged.connect(
            self.audioMixerPreviewChannelsChanged.emit
        )
        self._audio_preview_controller.previewGainsChanged.connect(
            self.audioMixerPreviewGainsChanged.emit
        )
        self._audio_preview_controller.levelsChanged.connect(
            self.audioPreviewLevelsChanged.emit
        )
        self._audio_preview_controller.masterMetricsChanged.connect(
            self.audioMasterMetricsChanged.emit
        )
        self._audio_preview_controller.projectDataChanged.connect(
            self.projectDataChanged.emit
        )
        self._audio_preview_controller.statusChanged.connect(self._set_status)
        self._audio_preview_controller.cacheCompleted.connect(
            self.audioPreviewCacheCompleted.emit
        )
        self._audio_preview_controller.set_project(self._project)
        self._highlight_candidates: list[dict[str, Any]] = []
        self._highlight_rejected: list[dict[str, Any]] = []
        self._highlight_status = "idle"
        self._highlight_progress = 0.0
        self._highlight_cancel = threading.Event()
        self._highlight_generation = 0
        self.autosaveCompleted.connect(self._finish_autosave)

        self._update_info: updater.UpdateInfo | None = None
        self._update_error = ""
        self._update_busy = False
        self._update_package_path: Path | None = None
        self._update_package_sha256 = ""
        self._update_package_ready = False
        self._update_download_bytes = 0
        self._update_download_total = 0
        self._update_download_speed = 0.0
        self._update_download_active = False
        self._update_download_cancel = threading.Event()
        self.updateCheckFinished.connect(self._on_update_check_finished, Qt.ConnectionType.QueuedConnection)
        self._codex_proposal: dict[str, Any] | None = None
        self._audio_mix_proposal: dict[str, Any] | None = None
        self._codex_current_time: float | None = None
        self.codexCallbackRequested.connect(
            self._run_codex_callback,
            Qt.ConnectionType.QueuedConnection,
        )
        self._codex_session = CodexSessionController(
            on_state=self._on_codex_state,
            on_message=self._on_codex_message,
            on_proposal=self._on_codex_proposal,
            callback_dispatcher=self._dispatch_codex_callback,
        )
        self._codex_audio_mix_session = CodexSessionController(
            client_factory=self._create_codex_chat_client,
            proposal_parser=AudioMixProposal.from_json,
            on_state=self._on_codex_audio_mix_state,
            on_proposal=self._on_codex_audio_mix_proposal,
            callback_dispatcher=self._dispatch_codex_callback,
            isolated_turn=True,
        )
        self._codex_actions = build_gui_action_dispatcher(self)
        self._last_codex_login_url = ""
        self._last_codex_log_state: tuple[object, ...] | None = None
        self._codex_chat = CodexChatController(
            client_factory=self._create_codex_chat_client,
            workspace_root=self.workspace_root,
            preferred_model=str(self._settings.get("codex_model", "")),
            provider_id="codex",
            provider_name="Codex",
            on_state=self._on_codex_provider_state,
            on_selected_model=self._persist_codex_model,
            callback_dispatcher=self._dispatch_codex_callback,
        )
        self._gemini_chat = CodexChatController(
            provider_factory=self._create_gemini_chat_provider,
            workspace_root=self.workspace_root,
            preferred_model=str(self._settings.get("gemini_model", "")),
            provider_id="gemini",
            provider_name="Gemini",
            initial_model_selection_supported=False,
            initial_login_available=False,
            on_state=self._on_gemini_provider_state,
            on_selected_model=self._persist_gemini_model,
            callback_dispatcher=self._dispatch_codex_callback,
        )
        self._ai_chat = AIProviderChatRouter(
            {"codex": self._codex_chat, "gemini": self._gemini_chat},
            preferred_provider=str(self._settings.get("ai_provider", "codex")),
            on_state=self._on_codex_chat_state,
            on_selected_provider=self._persist_ai_provider,
        )
        self.aboutToQuit.connect(self._ai_chat.shutdown)
        self._ai_chat.connect()
        self.updateDownloadProgressEvent.connect(self._on_update_download_progress, Qt.ConnectionType.QueuedConnection)
        self.updateDownloadFinished.connect(self._on_update_download_finished, Qt.ConnectionType.QueuedConnection)
        self._record_log(
            "バックエンドの初期化が完了しました",
            component="startup",
            stage="READY",
        )

    @Property(bool, notify=projectChanged)
    def projectLoaded(self) -> bool:
        return self._project is not None

    @Property(str, notify=projectChanged)
    def projectPath(self) -> str:
        return self._project_path

    @Property(str, notify=projectChanged)
    def projectName(self) -> str:
        return Path(self._project_path).name if self._project_path else ""

    @Property(bool, notify=projectChanged)
    def projectDirty(self) -> bool:
        return self._project_dirty

    @Property(str, notify=workspaceChanged)
    def currentWorkspace(self) -> str:
        return self._workspace_facade.currentWorkspace

    @Property("QVariantMap", notify=workspacePlayerStateChanged)
    def workspacePlayerState(self) -> dict[str, Any]:
        return self._workspace_facade.workspacePlayerState

    @Property("QVariantMap", notify=workspacePlayerStateChanged)
    def workspacePlayerStates(self) -> dict[str, dict[str, Any]]:
        return self._workspace_facade.workspacePlayerStates

    @Slot(str, int, bool, result=bool)
    def setWorkspacePlayerState(
        self,
        workspace: str,
        position_ms: int,
        playing: bool,
    ) -> bool:
        return self._workspace_facade.setWorkspacePlayerState(workspace, position_ms, playing)

    @Slot(str, result=bool)
    def switchWorkspace(self, workspace: str) -> bool:
        return self._workspace_facade.switchWorkspace(workspace)

    def _edit_mode_capabilities(self) -> EditModeCapabilities:
        return self._workspace_facade._edit_mode_capabilities()

    def _refresh_editor_workspace(self) -> None:
        return self._workspace_facade._refresh_editor_workspace()

    @Property(str, notify=editorModeChanged)
    def currentEditMode(self) -> str:
        return self._workspace_facade.currentEditMode

    @Property("QVariantMap", notify=editorCapabilitiesChanged)
    def editorModeCapabilities(self) -> dict[str, object]:
        return self._workspace_facade.editorModeCapabilities

    @Property("QVariantMap", notify=editorPlayheadChanged)
    def editorPlayhead(self) -> dict[str, object]:
        return self._workspace_facade.editorPlayhead

    def _cut_timeline_model(self) -> VideoTimeline:
        return self._workspace_facade._cut_timeline_model()

    @Property("QVariantMap", notify=cutTimelineChanged)
    def cutTimeline(self) -> dict[str, Any]:
        return self._workspace_facade.cutTimeline

    @Property(float, notify=cutTimelineChanged)
    def cutOutputDuration(self) -> float:
        return self._workspace_facade.cutOutputDuration

    @Slot(int, result=int)
    def sourceTimeToOutputMs(self, position_ms: int) -> int:
        return self._workspace_facade.sourceTimeToOutputMs(position_ms)

    @Slot(int, result=int)
    def outputTimeToSourceMs(self, position_ms: int) -> int:
        return self._workspace_facade.outputTimeToSourceMs(position_ms)

    @Slot(int, result=bool)
    def isSourceTimeCut(self, position_ms: int) -> bool:
        return self._workspace_facade.isSourceTimeCut(position_ms)

    @Slot(int, result=int)
    def nextCutPreviewSourceMs(self, position_ms: int) -> int:
        return self._workspace_facade.nextCutPreviewSourceMs(position_ms)

    @Slot(str, result=bool)
    def selectEditMode(self, mode: str) -> bool:
        return self._workspace_facade.selectEditMode(mode)

    @Slot(int, str, result=bool)
    def setEditorPlayhead(self, position_ms: int, basis: str) -> bool:
        return self._workspace_facade.setEditorPlayhead(position_ms, basis)

    def set_editor_time_mapping(self, mapping: TimeMapping | None) -> None:
        return self._workspace_facade.set_editor_time_mapping(mapping)

    def _sync_project_timeline(self) -> None:
        return self._workspace_facade._sync_project_timeline()

    def set_cut_editor_available(self, available: bool) -> None:
        return self._workspace_facade.set_cut_editor_available(available)

    def _reset_editor_timing(self) -> None:
        return self._workspace_facade._reset_editor_timing()

    @Property(bool, notify=lastProcessDiagnosticChanged)
    def hasLastProcessDiagnostic(self) -> bool:
        return self._last_process_diagnostic is not None

    @Property(str, notify=updateInfoChanged)
    def updateCurrentVersion(self) -> str:
        return self._updates_facade.updateCurrentVersion

    @Property(str, notify=updateInfoChanged)
    def updateLatestVersion(self) -> str:
        return self._updates_facade.updateLatestVersion

    @Property(str, notify=updateInfoChanged)
    def updateReleaseNotes(self) -> str:
        return self._updates_facade.updateReleaseNotes

    @Property(str, notify=updateInfoChanged)
    def updateDownloadUrl(self) -> str:
        return self._updates_facade.updateDownloadUrl

    @Property(str, notify=codexStateChanged)
    def codexState(self) -> str:
        return self._ai_facade.codexState

    @Property(str, notify=codexMessageChanged)
    def codexMessage(self) -> str:
        return self._ai_facade.codexMessage

    @Property(str, notify=codexStateChanged)
    def codexError(self) -> str:
        return self._ai_facade.codexError

    @Property("QVariantMap", notify=codexProposalChanged)
    def codexProposal(self) -> dict[str, Any]:
        return self._ai_facade.codexProposal

    @Property("QVariantMap", notify=audioMixProposalChanged)
    def audioMixProposal(self) -> dict[str, Any]:
        return self._audio_facade.audioMixProposal

    @Property(str, notify=audioMixProposalChanged)
    def audioMixProposalState(self) -> str:
        return self._audio_facade.audioMixProposalState

    @Property(str, notify=audioMixProposalChanged)
    def audioMixProposalError(self) -> str:
        return self._audio_facade.audioMixProposalError

    @Property("QStringList", constant=True)
    def codexScopes(self) -> list[str]:
        return self._ai_facade.codexScopes

    @Property(str, notify=codexChatChanged)
    def codexConnectionState(self) -> str:
        return self._ai_facade.codexConnectionState

    @Property(str, notify=codexChatChanged)
    def codexAuthState(self) -> str:
        return self._ai_facade.codexAuthState

    @Property(str, notify=codexChatChanged)
    def codexAuthLabel(self) -> str:
        return self._ai_facade.codexAuthLabel

    @Property(str, notify=codexChatChanged)
    def codexLoginUrl(self) -> str:
        return self._ai_facade.codexLoginUrl

    @Property(str, notify=codexChatChanged)
    def codexChatState(self) -> str:
        return self._ai_facade.codexChatState

    @Property(str, notify=codexChatChanged)
    def codexChatError(self) -> str:
        return self._ai_facade.codexChatError

    @Property("QVariantList", notify=codexChatChanged)
    def codexModels(self) -> list[dict[str, Any]]:
        return self._ai_facade.codexModels

    @Property(str, notify=codexChatChanged)
    def codexSelectedModel(self) -> str:
        return self._ai_facade.codexSelectedModel

    @Property(str, notify=codexChatChanged)
    def codexModelError(self) -> str:
        return self._ai_facade.codexModelError

    @Property("QVariantList", notify=codexChatChanged)
    def codexChatMessages(self) -> list[dict[str, Any]]:
        return self._ai_facade.codexChatMessages

    @Property(str, notify=aiChatChanged)
    def aiChatProviderId(self) -> str:
        return self._ai_facade.aiChatProviderId

    @Property(str, notify=aiChatChanged)
    def aiChatProviderName(self) -> str:
        return self._ai_facade.aiChatProviderName

    @Property("QVariantList", notify=aiChatChanged)
    def aiChatProviders(self) -> list[dict[str, Any]]:
        return self._ai_facade.aiChatProviders

    @Property(bool, notify=aiChatChanged)
    def aiChatModelSelectionSupported(self) -> bool:
        return self._ai_facade.aiChatModelSelectionSupported

    @Property(bool, notify=aiChatChanged)
    def aiChatLoginAvailable(self) -> bool:
        return self._ai_facade.aiChatLoginAvailable

    @Property(str, notify=aiChatChanged)
    def aiChatAuthHint(self) -> str:
        return self._ai_facade.aiChatAuthHint

    @Property(bool, notify=updateInfoChanged)
    def updateAvailable(self) -> bool:
        return self._updates_facade.updateAvailable

    @Property(bool, notify=updateBusyChanged)
    def updateBusy(self) -> bool:
        return self._updates_facade.updateBusy

    @Property(str, notify=updateErrorChanged)
    def updateError(self) -> str:
        return self._updates_facade.updateError

    @Property(int, notify=updateDownloadProgressChanged)
    def updateDownloadBytes(self) -> int:
        return self._updates_facade.updateDownloadBytes

    @Property(int, notify=updateDownloadProgressChanged)
    def updateDownloadTotal(self) -> int:
        return self._updates_facade.updateDownloadTotal

    @Property(float, notify=updateDownloadProgressChanged)
    def updateDownloadSpeed(self) -> float:
        return self._updates_facade.updateDownloadSpeed

    @Property(bool, notify=updateDownloadProgressChanged)
    def updateDownloadActive(self) -> bool:
        return self._updates_facade.updateDownloadActive

    @Property(bool, notify=updatePackageReadyChanged)
    def updatePackageReady(self) -> bool:
        return self._updates_facade.updatePackageReady

    @Property(int, notify=updateInfoChanged)
    def updatePackageSize(self) -> int:
        return self._updates_facade.updatePackageSize

    @Property("QVariantList", notify=segmentsChanged)
    def subtitleSegments(self) -> list[dict[str, Any]]:
        return self._subtitles_facade.subtitleSegments

    @Property("QVariantMap", notify=segmentsChanged)
    def subtitleLayoutMetrics(self) -> dict[str, float | int]:
        return self._subtitles_facade.subtitleLayoutMetrics

    @Property("QVariantList", notify=shortVideoChanged)
    def shortVideoClips(self) -> list[dict[str, Any]]:
        return self._shortVideo_facade.shortVideoClips

    @Property("QVariantMap", notify=shortVideoChanged)
    def shortVideoSettings(self) -> dict[str, Any]:
        return self._shortVideo_facade.shortVideoSettings

    @Property("QVariantList", notify=highlightCandidatesChanged)
    def highlightCandidates(self) -> list[dict[str, Any]]:
        return self._shortVideo_facade.highlightCandidates

    @Property(bool, notify=highlightCandidatesChanged)
    def highlightUndoAvailable(self) -> bool:
        return self._shortVideo_facade.highlightUndoAvailable

    @Property(str, notify=highlightAnalysisChanged)
    def highlightAnalysisState(self) -> str:
        return self._shortVideo_facade.highlightAnalysisState

    @Property(float, notify=highlightAnalysisChanged)
    def highlightAnalysisProgress(self) -> float:
        return self._shortVideo_facade.highlightAnalysisProgress

    @Property(QObject, constant=True)
    def subtitleModel(self) -> QObject:
        return self._subtitles_facade.subtitleModel

    @Property(QObject, constant=True)
    def shortVideoClipModel(self) -> QObject:
        return self._shortVideo_facade.shortVideoClipModel

    @Property(int, notify=shortVideoClipDataChanged)
    def shortVideoClipCount(self) -> int:
        return self._shortVideo_facade.shortVideoClipCount

    @Slot(int, result="QVariantMap")
    def shortVideoClipAt(self, index: int) -> dict[str, Any]:
        return self._shortVideo_facade.shortVideoClipAt(index)

    @Property("QVariantList", constant=True)
    def fontChoices(self) -> list[dict[str, str]]:
        return self._subtitles_facade.fontChoices

    @Property(int, notify=segmentsChanged)
    def segmentCount(self) -> int:
        return self._subtitles_facade.segmentCount

    def _sync_subtitle_model(self) -> None:
        return self._subtitles_facade._sync_subtitle_model()

    def _on_project_segments_changed(self) -> None:
        return self._subtitles_facade._on_project_segments_changed()

    def _on_project_history_applied(
        self,
        entry: dict[str, Any],
        _state: str,
    ) -> None:
        return self._subtitles_facade._on_project_history_applied(entry, _state)

    @staticmethod
    def _subtitle_preview_signature(segment: dict[str, Any]) -> tuple[object, ...]:
        return SubtitleFacade._subtitle_preview_signature(segment)

    def _preview_text_for_segment(self, segment: dict[str, Any]) -> str:
        return self._subtitles_facade._preview_text_for_segment(segment)

    def _segment_view(self, segment: dict[str, Any], source_index: int | None = None) -> dict[str, Any]:
        return self._subtitles_facade._segment_view(segment, source_index)

    def _short_video_section(self) -> dict[str, Any]:
        return self._shortVideo_facade._short_video_section()

    def _find_segment_by_id(self, segment_id: str) -> dict[str, Any] | None:
        return self._subtitles_facade._find_segment_by_id(segment_id)

    def _short_video_clip_count(self) -> int:
        return self._shortVideo_facade._short_video_clip_count()

    def _short_video_clip_view_at(self, index: int) -> dict[str, Any]:
        return self._shortVideo_facade._short_video_clip_view_at(index)

    def _refresh_short_video_clip_data(self) -> None:
        return self._shortVideo_facade._refresh_short_video_clip_data()

    def _build_short_video_clip_view(self, clip: dict[str, Any], index: int) -> dict[str, Any]:
        return self._shortVideo_facade._build_short_video_clip_view(clip, index)

    @Slot()
    def initializeShortVideoClips(self) -> None:
        return self._shortVideo_facade.initializeShortVideoClips()

    @Slot(str, result=bool)
    def addShortVideoClip(self, segment_id: str) -> bool:
        return self._shortVideo_facade.addShortVideoClip(segment_id)

    @Slot(float, float, result=bool)
    def addShortVideoClipByRange(self, start: float, end: float) -> bool:
        return self._shortVideo_facade.addShortVideoClipByRange(start, end)

    @Slot(int, result=bool)
    def removeShortVideoClip(self, index: int) -> bool:
        return self._shortVideo_facade.removeShortVideoClip(index)

    @Slot(int, int, result=bool)
    def moveShortVideoClip(self, from_index: int, to_index: int) -> bool:
        return self._shortVideo_facade.moveShortVideoClip(from_index, to_index)

    @Slot(int, "QVariantMap", result=bool)
    def updateShortVideoClip(self, index: int, fields: dict[str, Any]) -> bool:
        return self._shortVideo_facade.updateShortVideoClip(index, fields)

    @Slot(str, result=bool)
    def setShortVideoGlobalFit(self, fit: str) -> bool:
        return self._shortVideo_facade.setShortVideoGlobalFit(fit)

    @Slot(str, result=bool)
    def setShortVideoGlobalBackgroundColor(self, color: str) -> bool:
        return self._shortVideo_facade.setShortVideoGlobalBackgroundColor(color)

    @Slot(str, float, result=bool)
    def setShortVideoTransition(self, transition_type: str, duration: float) -> bool:
        return self._shortVideo_facade.setShortVideoTransition(transition_type, duration)

    @Slot("QVariantMap", result=bool)
    def setShortVideoBgm(self, fields: dict[str, Any]) -> bool:
        return self._shortVideo_facade.setShortVideoBgm(fields)

    @Slot(int, int, int, result=bool)
    def setShortVideoOutput(self, width: int, height: int, fps: int) -> bool:
        return self._shortVideo_facade.setShortVideoOutput(width, height, fps)

    @Slot(float, result=bool)
    def setShortVideoSubtitleScale(self, percent: float) -> bool:
        return self._shortVideo_facade.setShortVideoSubtitleScale(percent)

    @Slot(result=bool)
    def startHighlightAnalysis(self) -> bool:
        return self._shortVideo_facade.startHighlightAnalysis()

    @Slot(result=bool)
    def cancelHighlightAnalysis(self) -> bool:
        return self._shortVideo_facade.cancelHighlightAnalysis()

    @Slot(result=bool)
    def retryHighlightAnalysis(self) -> bool:
        return self._shortVideo_facade.retryHighlightAnalysis()

    @Slot(int, result=bool)
    def addHighlightCandidate(self, index: int) -> bool:
        return self._shortVideo_facade.addHighlightCandidate(index)

    @Slot(int, result=bool)
    def rejectHighlightCandidate(self, index: int) -> bool:
        return self._shortVideo_facade.rejectHighlightCandidate(index)

    @Slot(result=bool)
    def undoHighlightRejection(self) -> bool:
        return self._shortVideo_facade.undoHighlightRejection()

    def _is_current_highlight_run(self, generation: int) -> bool:
        return self._shortVideo_facade._is_current_highlight_run(generation)

    def _update_highlight_progress(self, generation: int, value: float) -> None:
        return self._shortVideo_facade._update_highlight_progress(generation, value)

    @Slot(int, result="QVariantMap")
    def segmentAt(self, index: int) -> dict[str, Any]:
        return self._subtitles_facade.segmentAt(index)

    @Slot(int, str, result=str)
    def formatSubtitlePreview(self, index: int, text: str) -> str:
        return self._subtitles_facade.formatSubtitlePreview(index, text)

    @Slot(float, result="QVariantList")
    def activeSubtitleSegments(self, seconds: float) -> list[dict[str, Any]]:
        return self._subtitles_facade.activeSubtitleSegments(seconds)

    @Slot(float, float, result="QVariantList")
    def visibleSubtitleSegments(self, start: float, end: float) -> list[dict[str, Any]]:
        return self._subtitles_facade.visibleSubtitleSegments(start, end)

    @Property("QVariantList", notify=projectDataChanged)
    def projectSpeakers(self) -> list[dict[str, Any]]:
        return self._subtitles_facade.projectSpeakers

    @Property("QVariantList", notify=projectDataChanged)
    def subtitleWaveforms(self) -> list[dict[str, Any]]:
        return self._subtitles_facade.subtitleWaveforms

    @Property(bool, notify=audioPreviewCacheChanged)
    def audioPreviewPreparing(self) -> bool:
        return self._audio_facade.audioPreviewPreparing

    @Property(int, notify=audioPreviewCacheChanged)
    def audioPreviewGeneration(self) -> int:
        return self._audio_facade.audioPreviewGeneration

    @Property(str, notify=audioPreviewCacheChanged)
    def audioPreviewCacheSummary(self) -> str:
        return self._audio_facade.audioPreviewCacheSummary

    @Property(str, notify=audioMixerPreviewChannelsChanged)
    def audioPreviewClockUrl(self) -> str:
        return self._audio_facade.audioPreviewClockUrl

    def _reset_audio_preview_cache(self) -> None:
        return self._audio_facade._reset_audio_preview_cache()

    @Slot()
    def prepareAudioMixerPreview(self) -> None:
        return self._audio_facade.prepareAudioMixerPreview()

    @Slot(int, object)
    def _apply_audio_preview_cache(
        self,
        request_id: int,
        result: AudioPreviewCacheResult,
    ) -> None:
        return self._audio_facade._apply_audio_preview_cache(request_id, result)

    @Slot()
    def clearAudioPreviewCache(self) -> None:
        return self._audio_facade.clearAudioPreviewCache()

    @Property("QVariantList", notify=projectDataChanged)
    def audioMixerChannels(self) -> list[dict[str, Any]]:
        return self._audio_facade.audioMixerChannels

    @Property(bool, notify=projectDataChanged)
    def audioMixerAvailable(self) -> bool:
        return self._audio_facade.audioMixerAvailable

    def _enabled_audio_mixer_channel_ids(self) -> set[str]:
        return self._audio_facade._enabled_audio_mixer_channel_ids()

    @Property(bool, notify=projectDataChanged)
    def audioMixerPreviewComplete(self) -> bool:
        return self._audio_facade.audioMixerPreviewComplete

    @Property(bool, notify=projectDataChanged)
    def audioMixerIntentionalSilence(self) -> bool:
        return self._audio_facade.audioMixerIntentionalSilence

    @Property("QVariantList", notify=projectDataChanged)
    def audioMixerSequenceChannels(self) -> list[dict[str, Any]]:
        return self._audio_facade.audioMixerSequenceChannels

    def _audio_mixer_channel_view(self, channel: dict[str, Any]) -> dict[str, Any]:
        return self._audio_facade._audio_mixer_channel_view(channel)

    def _audio_mixer_preview_state(
        self,
    ) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], dict[str, float]]:
        return self._audio_facade._audio_mixer_preview_state()

    def _notify_audio_mixer_preview(self, *, structure_changed: bool) -> None:
        return self._audio_facade._notify_audio_mixer_preview(structure_changed=structure_changed)

    @Property("QVariantList", notify=audioMixerPreviewChannelsChanged)
    def audioMixerPreviewChannels(self) -> list[dict[str, Any]]:
        return self._audio_facade.audioMixerPreviewChannels

    @Property("QVariantMap", notify=audioMixerPreviewGainsChanged)
    def audioMixerPreviewGains(self) -> dict[str, float]:
        return self._audio_facade.audioMixerPreviewGains

    def _audio_preview_output(self, channel_id: str) -> QAudioBufferOutput:
        return self._audio_facade._audio_preview_output(channel_id)

    @staticmethod
    def _audio_buffer_peak(buffer: QAudioBuffer) -> float:
        return AudioFacade._audio_buffer_peak(buffer)

    def _receive_audio_preview_buffer(self, channel_id: str, buffer: QAudioBuffer) -> None:
        return self._audio_facade._receive_audio_preview_buffer(channel_id, buffer)

    def _publish_audio_preview_levels(self) -> None:
        return self._audio_facade._publish_audio_preview_levels()

    @Property("QVariantMap", notify=audioPreviewLevelsChanged)
    def audioPreviewLevels(self) -> dict[str, float]:
        return self._audio_facade.audioPreviewLevels

    @Slot(float, float)
    def _update_audio_master_metrics(self, level: float, reduction_db: float) -> None:
        return self._audio_facade._update_audio_master_metrics(level, reduction_db)

    @Property(float, notify=audioMasterMetricsChanged)
    def audioMasterLevel(self) -> float:
        return self._audio_facade.audioMasterLevel

    @Property(float, notify=audioMasterMetricsChanged)
    def audioLimiterReductionDb(self) -> float:
        return self._audio_facade.audioLimiterReductionDb

    @Slot(int)
    def startAudioMixerPreview(self, position_milliseconds: int) -> None:
        return self._audio_facade.startAudioMixerPreview(position_milliseconds)

    @Slot()
    def pauseAudioMixerPreview(self) -> None:
        return self._audio_facade.pauseAudioMixerPreview()

    @Slot(int, bool)
    def seekAudioMixerPreview(self, position_milliseconds: int, playing: bool) -> None:
        return self._audio_facade.seekAudioMixerPreview(position_milliseconds, playing)

    @Slot()
    def stopAudioMixerPreview(self) -> None:
        return self._audio_facade.stopAudioMixerPreview()

    @Property(float, notify=segmentsChanged)
    def projectDuration(self) -> float:
        if self._project is None:
            return 0.0
        video_duration = float(self._project.get("video", {}).get("duration_seconds", 0.0))
        segment_duration = max((float(item["end"]) for item in self._project.get("segments", [])), default=0.0)
        return max(video_duration, segment_duration)

    def _sequence_model_for_facade(self) -> VideoSequence | None:
        return self._sequence_facade._sequence_model_for_facade()

    def _sequence_view_payload(self) -> dict[str, Any]:
        return self._sequence_facade._sequence_view_payload()

    def _sequence_failure(self, message: str) -> bool:
        return self._sequence_facade._sequence_failure(message)

    def _apply_sequence_mutation(
        self,
        mutation: Callable[[VideoSequence], VideoSequence],
        success_message: str,
    ) -> bool:
        return self._sequence_facade._apply_sequence_mutation(mutation, success_message)

    @Property("QVariantMap", notify=sequenceChanged)
    def sequenceView(self) -> dict[str, Any]:
        return self._sequence_facade.sequenceView

    @Property("QVariantList", notify=sequenceChanged)
    def mediaBinAssets(self) -> list[dict[str, Any]]:
        return self._sequence_facade.mediaBinAssets

    @Property("QVariantList", notify=sequenceChanged)
    def sequenceClips(self) -> list[dict[str, Any]]:
        return self._sequence_facade.sequenceClips

    @Property(float, notify=sequenceChanged)
    def sequenceOutputDuration(self) -> float:
        return self._sequence_facade.sequenceOutputDuration

    @Property("QVariantMap", notify=sequenceChanged)
    def sequencePlayhead(self) -> dict[str, Any]:
        return self._sequence_facade.sequencePlayhead

    @Property(str, notify=sequenceChanged)
    def sequenceError(self) -> str:
        return self._sequence_facade.sequenceError

    @Slot(str, result=bool)
    def addSequenceAsset(self, path: str) -> bool:
        return self._sequence_facade.addSequenceAsset(path)

    @Slot("QVariantList", result=int)
    def addSequenceAssets(self, paths: list[Any]) -> int:
        return self._sequence_facade.addSequenceAssets(paths)

    @Slot(result=str)
    def browseSequenceAsset(self) -> str:
        return self._sequence_facade.browseSequenceAsset()

    @Slot(str, result=bool)
    def addSequenceClip(self, asset_id: str) -> bool:
        return self._sequence_facade.addSequenceClip(asset_id)

    @Slot(str, int, result=bool)
    def insertSequenceClip(self, asset_id: str, index: int) -> bool:
        return self._sequence_facade.insertSequenceClip(asset_id, index)

    @Slot(str, int, result=bool)
    def moveSequenceClip(self, clip_id: str, index: int) -> bool:
        return self._sequence_facade.moveSequenceClip(clip_id, index)

    @Slot(str, result=bool)
    def removeSequenceClip(self, clip_id: str) -> bool:
        return self._sequence_facade.removeSequenceClip(clip_id)

    @Slot(str, float, float, result=bool)
    def trimSequenceClip(self, clip_id: str, source_start: float, source_end: float) -> bool:
        return self._sequence_facade.trimSequenceClip(clip_id, source_start, source_end)

    @Slot(str, str, float, result=bool)
    def setSequenceTransition(self, clip_id: str, transition_type: str, duration: float) -> bool:
        return self._sequence_facade.setSequenceTransition(clip_id, transition_type, duration)

    @Slot(str, bool, float, float, bool, result=bool)
    def setSequenceClipAudio(
        self,
        clip_id: str,
        audio_linked: bool,
        volume: float,
        audio_offset_seconds: float,
        muted: bool,
    ) -> bool:
        return self._sequence_facade.setSequenceClipAudio(clip_id, audio_linked, volume, audio_offset_seconds, muted)

    @Slot(int, result=bool)
    def setSequencePlayhead(self, output_milliseconds: int) -> bool:
        return self._sequence_facade.setSequencePlayhead(output_milliseconds)

    @Property(bool, notify=historyChanged)
    def canUndo(self) -> bool:
        return self._subtitles_facade.canUndo

    @Property(bool, notify=historyChanged)
    def canRedo(self) -> bool:
        return self._subtitles_facade.canRedo

    @Property(int, notify=selectionChanged)
    def selectedSegmentIndex(self) -> int:
        return self._subtitles_facade.selectedSegmentIndex

    @Property(str, notify=activeJobChanged)
    def activeJob(self) -> str:
        return self._workflow_facade.activeJob

    @Property("QVariantList", notify=progressDetailsChanged)
    def progressSteps(self) -> list[dict[str, Any]]:
        return self._workflow_facade.progressSteps

    @Property(int, notify=progressDetailsChanged)
    def progressPercent(self) -> int:
        return self._workflow_facade.progressPercent

    @Property(str, notify=progressDetailsChanged)
    def progressCurrentStep(self) -> str:
        return self._workflow_facade.progressCurrentStep

    @Property(str, notify=progressDetailsChanged)
    def progressCurrentStepDisplay(self) -> str:
        return self._workflow_facade.progressCurrentStepDisplay

    @Property(str, notify=progressDetailsChanged)
    def progressState(self) -> str:
        return self._workflow_facade.progressState

    @Property(bool, notify=progressDetailsChanged)
    def progressVisible(self) -> bool:
        return self._workflow_facade.progressVisible

    @Property(str, notify=assPathChanged)
    def assPath(self) -> str:
        return self._workflow_facade.assPath

    def _apply_project_speaker_color(self, index: int, color: str) -> bool:
        return self._subtitles_facade._apply_project_speaker_color(index, color)

    def _source_speaker_color_updated(self, speaker: dict[str, str]) -> None:
        return self._subtitles_facade._source_speaker_color_updated(speaker)

    @Slot(int, str)
    def updateProjectSpeakerColor(self, index: int, color: str) -> None:
        return self._subtitles_facade.updateProjectSpeakerColor(index, color)

    def _source_selection_updated(self, update: Any) -> None:
        previous = update.previous
        selection = update.current
        if previous is None or selection is None:
            return
        if self._loading_project_sources or self._project is None:
            return
        media_changed = update.media_changed
        if not self._relinking_project_sources and media_changed and not self._project_source_selection_matches(selection):
            self._clear_project()
        elif previous.output_dir != selection.output_dir:
            self._project["output_dir"] = selection.output_dir
            self._mark_project_dirty()
            self.projectDataChanged.emit()

    def _normalized_source_path(self, value: str) -> str:
        if not value:
            return ""
        return str(Path(value).resolve()).casefold()

    def _project_source_selection_matches(self, selection: Any) -> bool:
        if self._project is None:
            return False
        project_video = self._normalized_source_path(str(self._project.get("video", {}).get("path", "")))
        selected_video = self._normalized_source_path(selection.video)
        project_audio = {
            self._normalized_source_path(str(item.get("path", "")))
            for item in self._project.get("audio_sources", [])
            if item.get("path")
        }
        selected_audio = {self._normalized_source_path(path) for path in selection.audio_files}
        return (
            selected_video == project_video
            and selected_audio == project_audio
        )

    @Slot()
    def beginSourceRelink(self) -> None:
        self._relink_source_selection = self._source_selection
        self._relinking_project_sources = True

    @Slot()
    def finishSourceRelink(self) -> None:
        if not self._relinking_project_sources:
            return
        try:
            previous = self._relink_source_selection
            if self._project is not None and previous is not None and (
                previous.video != self._source_selection.video
                or previous.audio_files != self._source_selection.audio_files
            ) and not self._project_source_selection_matches(self._source_selection):
                self._clear_project()
        finally:
            self._relinking_project_sources = False
            self._relink_source_selection = None

    @Slot()
    def relinkProjectSources(self) -> None:
        if self._project is None:
            return

        old_project = deepcopy(self._project)
        previous_sources = [dict(item) for item in self._project.get("audio_sources", [])]
        previous_speakers = [dict(item) for item in self._project.get("speakers", [])]

        source_entries = build_speaker_entries_from_files(
            self._source_selection.audio_files,
            self.color_config_path,
        )
        project_video = self._project.get("video", {})
        selected_video = str(Path(self._source_selection.video).resolve()) if self._source_selection.video else ""
        selected_output = str(Path(self._source_selection.output_dir).resolve()) if self._source_selection.output_dir else ""

        if not selected_video:
            self._set_status("Relink requires a complete source selection", "CHECK")
            return

        try:
            project_sequence = VideoSequence.from_json(
                self._project.get("sequence"),
                legacy_video=project_video if isinstance(project_video, dict) else {},
            )
        except VideoSequenceError as error:
            self._set_status(f"Project sequence is invalid: {error}", "CHECK")
            return
        project_video_path = self._normalized_source_path(
            str(project_video.get("path", "")) if isinstance(project_video, dict) else ""
        )
        if (
            not project_sequence.is_legacy_single_video()
            and project_video_path != self._normalized_source_path(selected_video)
        ):
            self._set_status(
                "複数clipのsequenceは単一動画のrelinkでは変更できません",
                "CHECK",
            )
            return

        def _match(items: list[dict[str, Any]], **conditions: str) -> dict[str, Any] | None:
            for index, item in enumerate(items):
                for key, value in conditions.items():
                    item_value = str(item.get(key, "")).strip().casefold()
                    if value and item_value != str(value).strip().casefold():
                        break
                else:
                    return items.pop(index)
            return None

        unmatched_speakers = previous_speakers.copy()
        unmatched_audio_sources = previous_sources.copy()
        new_audio_sources: list[dict[str, Any]] = []
        new_speakers: list[dict[str, Any]] = []

        for source in source_entries:
            previous_source = (
                _match(unmatched_audio_sources, path=source["path"])
                or _match(unmatched_audio_sources, file_name=source["file_name"])
                or _match(unmatched_audio_sources, file_name=str(Path(source["path"]).name))
            )
            source_payload: dict[str, Any] = {**previous_source} if previous_source else {}
            source_payload["path"] = source["path"]
            source_payload.setdefault("file_name", source["file_name"])
            source_payload.setdefault("track_key", source["track_key"])
            new_audio_sources.append(source_payload)

            previous_speaker = (
                _match(unmatched_speakers, path=source["path"])
                or _match(unmatched_speakers, file_name=source["file_name"])
                or _match(unmatched_speakers, name=source["name"])
            )
            if previous_speaker is None:
                speaker_payload = {
                    "name": source["name"],
                    "style": style_name_for_speaker(source["name"]),
                    "file_name": source["file_name"],
                    "track_key": source["track_key"],
                    "color": source["color"],
                    "path": source["path"],
                }
            else:
                speaker_payload = {
                    **previous_speaker,
                    "name": source["name"],
                    "file_name": source["file_name"],
                    "track_key": previous_speaker.get("track_key", source["track_key"]),
                    "path": source["path"],
                }
                speaker_payload.setdefault("style", style_name_for_speaker(source["name"]))
            new_speakers.append(speaker_payload)

        self._project["video"] = {
            **(project_video if isinstance(project_video, dict) else {}),
            "path": selected_video,
        }
        self._project["output_dir"] = selected_output
        self._project["audio_sources"] = new_audio_sources
        self._project["speakers"] = new_speakers

        if "video" in self._project and isinstance(self._project["video"], dict):
            self._project["video"]["duration_seconds"] = float(
                self._project["video"].get("duration_seconds", 0.0)
            )

        if project_sequence.is_legacy_single_video():
            try:
                self._project["sequence"] = project_sequence.sync_legacy_video(
                    self._project["video"]
                ).to_json()
            except (KeyError, TypeError, VideoSequenceError) as error:
                self._set_status(f"Project sequence could not be synchronized: {error}", "CHECK")
                self._project = old_project
                return

        reconcile_audio_mix(self._project, self._mixer_video_tracks())

        if self._project != old_project:
            self._mark_project_dirty()
            self._reset_audio_preview_cache()
            self._sync_subtitle_model()
            self.projectDataChanged.emit()
            self.segmentsChanged.emit()
            self.selectionChanged.emit()
            self._set_status("Project sources relinked", "EDIT")

    def _default_project_path(self) -> Path | None:
        if self._project is not None and self._project_path:
            return Path(self._project_path)
        selection = self._source_selection
        if not selection.video:
            return None
        return derive_project_path(selection.video, Path(selection.video).parent)

    @Property(str, notify=actionCapabilitiesChanged)
    def projectSavePath(self) -> str:
        path = self._default_project_path()
        return str(path) if path else ""

    @Property(str, notify=actionCapabilitiesChanged)
    def videoOutputDirectory(self) -> str:
        if self._project is not None:
            return str(self._project.get("output_dir", ""))
        return self._source_selection.output_dir

    @Slot(result=bool)
    def transcriptionProjectExists(self) -> bool:
        path = self._default_project_path()
        return path is not None and path.is_file()

    @Slot(result=bool)
    def createEmptyProject(self) -> bool:
        return self._create_empty_project(self._default_project_path())

    def _create_empty_project(self, project_path: Path | None) -> bool:
        if self._running:
            self._set_status("処理中は編集プロジェクトを変更できません", "BUSY")
            return False
        selection = self._source_selection
        if not Path(selection.video).is_file():
            self._set_status("編集する動画を指定してください", "CHECK")
            return False
        if project_path is None:
            self._set_status("編集プロジェクトの保存先を決定できません", "ERROR")
            return False
        if project_path.is_file():
            self._set_status(
                "既存プロジェクトは上書きしません。プロジェクトを開くか、別のプロジェクト保存先を指定してください",
                "CHECK",
            )
            return False

        try:
            duration_seconds = probe_media_duration(selection.video)
        except (OSError, subprocess.CalledProcessError, ValueError):
            duration_seconds = 0.0
        project = create_project(
            video_path=selection.video,
            output_dir=selection.output_dir,
            segments=[],
            audio_sources=deepcopy(self._speakers),
            speakers=deepcopy(self._speakers),
            duration_seconds=duration_seconds,
            transcription={"status": "not_started", "context_base_dir": str(project_path.parent.resolve())},
        )
        reconcile_audio_mix(
            project,
            self._mixer_video_tracks(),
        )
        try:
            self._project_editor_controller.save_new_project(project_path, project)
        except (OSError, SubtitleProjectError, TypeError, ValueError) as error:
            self._set_status(f"空の編集プロジェクトを保存できません: {error}", "ERROR")
            return False
        loaded = self._load_project_path(project_path, update_sources=False)
        if loaded:
            self._set_status("空の編集プロジェクトを作成しました。字幕を手動追加できます", "EDIT")
        return loaded

    def _clear_project(self) -> None:
        self.autosave_timer.stop()
        if self._project_dirty:
            self.saveProject()
        if hasattr(self, "_codex_audio_mix_session"):
            was_audio_proposal_running = self._codex_audio_mix_session.running
            self._codex_audio_mix_session.stop()
            if was_audio_proposal_running:
                self._codex_chat.fail_proposal("", cancelled=True)
        self._audio_mix_proposal = None
        if hasattr(self, "audioMixProposalChanged"):
            self.audioMixProposalChanged.emit()
        self._reset_transcription_integration_state()
        self._project_editor_controller.clear(emit=False)
        if hasattr(self, "_audio_preview_controller"):
            self._audio_preview_controller.set_project(None)
        self._reset_editor_timing()
        self.cutTimelineChanged.emit()
        self._reset_audio_preview_cache()
        self._audio_preview_gains.clear()
        self._audio_preview_pending_levels.clear()
        if self._audio_preview_levels:
            self._audio_preview_levels.clear()
            self.audioPreviewLevelsChanged.emit()
        self._audio_preview_level_timer.stop()
        self._sync_subtitle_model()
        self.projectChanged.emit()
        self.projectDataChanged.emit()
        self.segmentsChanged.emit()
        self.historyChanged.emit()
        self.selectionChanged.emit()

    def _try_load_default_project(self) -> bool:
        if self._loading_project_sources or self._relinking_project_sources:
            return False
        path = self._default_project_path()
        if path is not None and path.is_file():
            return self._load_project_path(path, update_sources=False)
        if path is not None and self._project_path and Path(self._project_path).resolve() != path.resolve():
            self._clear_project()
        return False

    @Slot()
    def resetSources(self) -> None:
        if self._running:
            return
        super().resetSources()
        if not self._loading_project_sources and self._project is not None:
            self._clear_project()

    @Slot(str)
    def setVideoFile(self, path: str) -> None:
        super().setVideoFile(path)
        if not self._running and self._project is None:
            self._try_load_default_project()

    @Slot(str)
    def setOutputDirectory(self, path: str) -> None:
        if not path and not self._running:
            self._set_source_selection(replace(self._source_selection, output_dir=""))
        else:
            super().setOutputDirectory(path)

    @Slot(result=str)
    def browseShortModeBgm(self) -> str:
        return self._shortVideo_facade.browseShortModeBgm()

    @Slot()
    def browseProjectFile(self) -> None:
        if self._running:
            self._set_status("処理中は編集プロジェクトを変更できません", "BUSY")
            return
        default_path = self._default_project_path()
        start_dir = str(default_path.parent) if default_path else str(self.workspace_root)
        path, _ = QFileDialog.getOpenFileName(
            None,
            "字幕編集プロジェクトを開く",
            start_dir,
            "Subtitle projects (*.subtitle-project.json);;JSON files (*.json)",
        )
        if path:
            self.loadProject(path)

    @Slot()
    def browseProjectSaveAs(self) -> None:
        if self._running or not self._source_selection.video and self._project is None:
            return
        default_path = self._default_project_path()
        path, _ = QFileDialog.getSaveFileName(
            None, "編集プロジェクトの保存先", str(default_path or self.workspace_root),
            "Subtitle projects (*.subtitle-project.json);;JSON files (*.json)",
        )
        if path:
            self.saveProjectAs(path)

    @Slot(str, result=bool)
    def saveProjectAs(self, path: str) -> bool:
        if self._running or not path:
            return False
        self.autosave_timer.stop()
        target = self._local_path(path)
        if target.suffix.lower() != ".json":
            target = target.with_name(target.name + ".subtitle-project.json")
            if target.exists():
                self._set_status(
                    "拡張子を補った保存先には既存プロジェクトがあります。拡張子付きで選択するか、別の名前を指定してください",
                    "CHECK",
                )
                return False
        if self._project is None:
            return self._create_empty_project(target)
        try:
            self._project_editor_controller.save_as(target, emit=False)
        except (OSError, SubtitleProjectError, TypeError, ValueError) as error:
            self._set_status(f"プロジェクトを保存できません: {error}", "ERROR")
            return False
        self.projectChanged.emit()
        self._set_status("別の場所に編集プロジェクトを保存しました", "SAVED")
        return True

    def _mixer_video_tracks(self) -> list[dict[str, str]]:
        return self._audio_facade._mixer_video_tracks()

    def _fallback_video_tracks(self) -> list[dict[str, str]]:
        return self._audio_facade._fallback_video_tracks()

    @Slot(int, "QVariantMap")
    def updateAudioMixChannel(self, index: int, changes: dict[str, Any]) -> None:
        return self._audio_facade.updateAudioMixChannel(index, changes)

    def start_codex_audio_mix_proposal(
        self,
        *,
        intent: str,
        revision: int,
        context: Mapping[str, Any] | None = None,
    ) -> bool:
        return self._audio_facade.start_codex_audio_mix_proposal(intent=intent, revision=revision, context=context)

    @Slot(str, result=bool)
    def proposeAudioMix(self, intent: str) -> bool:
        return self._audio_facade.proposeAudioMix(intent)

    @Slot()
    def stopCodexAudioMixProposal(self) -> None:
        return self._audio_facade.stopCodexAudioMixProposal()

    @Slot("QVariantList", bool, result=bool)
    def applyAudioMixProposal(
        self,
        selected_operation_ids: list[Any] | None = None,
        allow_silence: bool = False,
    ) -> bool:
        return self._audio_facade.applyAudioMixProposal(selected_operation_ids, allow_silence)

    @Slot()
    def discardAudioMixProposal(self) -> None:
        return self._audio_facade.discardAudioMixProposal()

    @Slot()
    def resetAudioMixer(self) -> None:
        return self._audio_facade.resetAudioMixer()

    @Slot(str)
    def loadProject(self, path: str) -> None:
        if self._running:
            self._set_status("処理中は編集プロジェクトを変更できません", "BUSY")
            return
        if self._project_dirty:
            if not self._project_path:
                self._set_status("先にプロジェクト保存先を指定してください", "ERROR")
                return
            if not self.saveProject():
                return
        candidate = self._local_path(path)
        self._load_project_path(candidate, update_sources=True)

    @Slot(str, "QVariantMap", result=bool)
    def loadProjectWithSelectedSources(self, path: str, source_selection: dict[str, Any]) -> bool:
        """Load an existing project while keeping the sources chosen for the next transcription."""
        if self._running:
            self._set_status("処理中は編集プロジェクトを変更できません", "BUSY")
            return False
        if self._project_dirty:
            if not self._project_path or not self.saveProject():
                return False

        candidate = self._local_path(path)
        selected_sources = SourceSelection(
            video=str(source_selection.get("video", "")),
            output_dir=str(source_selection.get("output_dir", "")),
            audio_files=tuple(str(item) for item in source_selection.get("audio_files", [])),
        )
        if not self._load_project_path(candidate, update_sources=True):
            return False

        self.beginSourceRelink()
        try:
            self._set_source_selection(selected_sources)
            self.relinkProjectSources()
        finally:
            self.finishSourceRelink()
        return self._project is not None and self._project_source_selection_matches(selected_sources)

    @Slot("QVariantMap", str)
    def transcribeProject(self, settings: dict[str, Any], mode: str) -> None:
        return self._workflow_facade.transcribeProject(settings, mode)

    def _merge_preserved_transcription_segments(self) -> bool:
        return self._workflow_facade._merge_preserved_transcription_segments()

    def _restore_preserved_transcription_project(self) -> None:
        return self._workflow_facade._restore_preserved_transcription_project()

    def _cleanup_transcription_project_artifact(self) -> None:
        return self._workflow_facade._cleanup_transcription_project_artifact()

    def _reset_transcription_integration_state(self) -> None:
        return self._workflow_facade._reset_transcription_integration_state()

    def _apply_project_subtitle_settings(self, project: dict[str, Any]) -> None:
        subtitle = project.get("subtitle_settings", {})
        updates: dict[str, int | float | str] = {}
        if isinstance(subtitle, dict):
            for project_key, setting_key, converter in (
                ("font_size", "subtitle_font_size", int),
                ("outline_color", "subtitle_outline_color", normalize_rgb_color),
                ("outline_thickness", "subtitle_outline_thickness", int),
                ("volume_scale_percent", "subtitle_volume_scale_percent", float),
                ("max_gap_seconds", "subtitle_max_gap_seconds", float),
                ("end_padding_seconds", "subtitle_end_padding_seconds", float),
                ("min_duration_seconds", "subtitle_min_duration_seconds", float),
            ):
                if project_key not in subtitle:
                    continue
                try:
                    value = converter(subtitle[project_key])
                except (TypeError, ValueError, OverflowError):
                    continue
                if isinstance(value, float) and not math.isfinite(value):
                    continue
                updates[setting_key] = value

        render_settings = project.get("render_settings", {})
        if isinstance(render_settings, dict):
            for project_key, setting_key, converter in (
                ("video_codec", "video_codec", str),
                ("audio_normalize", "audio_normalize", bool),
                ("audio_target_lufs", "audio_target_lufs", float),
                ("cut_no_speech", "cut_no_speech", bool),
                ("no_speech_min_seconds", "no_speech_min_seconds", float),
                ("speech_padding_seconds", "speech_padding_seconds", float),
                ("speech_threshold_db", "speech_threshold_db", str),
                ("speech_min_clip_seconds", "speech_min_clip_seconds", float),
                ("nvenc_cq", "nvenc_cq", int),
                ("x264_crf", "x264_crf", int),
            ):
                if project_key not in render_settings:
                    continue
                value = render_settings[project_key]
                if converter is bool:
                    if not isinstance(value, bool):
                        continue
                elif converter is str:
                    if not isinstance(value, str):
                        continue
                    value = value
                else:
                    try:
                        value = converter(value)
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if isinstance(value, float) and not math.isfinite(value):
                        continue
                updates[setting_key] = value

        self._settings.update(updates)
        self.settingsChanged.emit()

    def _load_project_path(self, path: Path, *, update_sources: bool) -> bool:
        self.autosave_timer.stop()
        try:
            project = self._project_editor_controller.load(path)
        except (OSError, json.JSONDecodeError, SubtitleProjectError, TypeError, ValueError) as error:
            self._set_status(f"プロジェクトを開けません: {error}", "ERROR")
            return False
        if hasattr(self, "_codex_audio_mix_session"):
            was_audio_proposal_running = self._codex_audio_mix_session.running
            self._codex_audio_mix_session.stop()
            if was_audio_proposal_running:
                self._codex_chat.fail_proposal("", cancelled=True)
        self._audio_mix_proposal = None
        if hasattr(self, "audioMixProposalChanged"):
            self.audioMixProposalChanged.emit()
        transcription = project.setdefault("transcription", {})
        transcription.setdefault("context_base_dir", str(Path(
            transcription.get("work_dir") or project.get("output_dir") or path.parent
        ).resolve()))
        self._apply_project_subtitle_settings(project)
        self._audio_preview_controller.set_project(project)
        self._reset_audio_preview_cache()
        self._reset_editor_timing()
        self._sync_project_timeline()
        self._loading_project_sources = True
        try:
            selection = replace(self._source_selection, output_dir=str(project.get("output_dir", "")))
            if update_sources:
                video = Path(str(project.get("video", {}).get("path", "")))
                audio_files = [str(item.get("path", "")) for item in project.get("audio_sources", [])]
                resolved_audio_files = [str(Path(item).resolve()) for item in audio_files if Path(item).is_file()]
                selection = replace(
                    selection,
                    video=str(video.resolve()) if video.is_file() else "",
                    audio_files=tuple(resolved_audio_files),
                )
            self._set_source_selection(selection)
        finally:
            self._loading_project_sources = False
        reconcile_audio_mix(self._project, self._mixer_video_tracks())
        self._sync_subtitle_model()
        self.projectChanged.emit()
        self.projectDataChanged.emit()
        self.segmentsChanged.emit()
        self.historyChanged.emit()
        self.selectionChanged.emit()
        self._set_status(f"編集プロジェクトを開きました（字幕 {len(project['segments'])} 件）", "EDIT")
        return True

    def _record_history(
        self,
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
        reflow_layout: bool = True,
    ) -> None:
        return self._subtitles_facade._record_history(before, after, reflow_layout)

    def _record_timeline_history(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> None:
        return self._subtitles_facade._record_timeline_history(before, after)

    def _push_history(self, entry: dict[str, Any]) -> None:
        return self._subtitles_facade._push_history(entry)

    def _mark_project_dirty(self) -> None:
        return self._subtitles_facade._mark_project_dirty()

    def _replace_segments(
        self,
        segments: list[dict[str, Any]],
        selected_id: str | None = None,
        *,
        reflow_layout: bool = True,
    ) -> None:
        return self._subtitles_facade._replace_segments(segments, selected_id, reflow_layout=reflow_layout)

    def _commit_segment_change(
        self,
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
        selected_id: str | None = None,
        *,
        reflow_layout: bool = True,
    ) -> None:
        return self._subtitles_facade._commit_segment_change(before, after, selected_id, reflow_layout=reflow_layout)

    def _apply_history_entry(self, entry: dict[str, Any], state: str) -> None:
        return self._subtitles_facade._apply_history_entry(entry, state)

    def _replace_timeline(self, payload: dict[str, Any]) -> None:
        return self._workspace_facade._replace_timeline(payload)

    def _commit_timeline(self, timeline: VideoTimeline, status: str) -> bool:
        return self._workspace_facade._commit_timeline(timeline, status)

    @Slot(float, float, result=bool)
    def addCut(self, source_start: float, source_end: float) -> bool:
        return self._workspace_facade.addCut(source_start, source_end)

    @Slot(str, float, float, result=bool)
    def updateCutRange(self, cut_id: str, source_start: float, source_end: float) -> bool:
        return self._workspace_facade.updateCutRange(cut_id, source_start, source_end)

    @Slot(str, result=bool)
    def restoreCut(self, cut_id: str) -> bool:
        return self._workspace_facade.restoreCut(cut_id)

    @Slot(float, float, result=bool)
    def restoreRange(self, source_start: float, source_end: float) -> bool:
        return self._workspace_facade.restoreRange(source_start, source_end)

    @Slot(result=bool)
    def clearCuts(self) -> bool:
        return self._workspace_facade.clearCuts()

    @Slot(int)
    def selectSegment(self, index: int) -> None:
        return self._subtitles_facade.selectSegment(index)

    @Slot(float, result=int)
    def segmentIndexAtTime(self, seconds: float) -> int:
        return self._subtitles_facade.segmentIndexAtTime(seconds)

    @Slot(float)
    def selectSegmentAtTime(self, seconds: float) -> None:
        return self._subtitles_facade.selectSegmentAtTime(seconds)

    def _edit_number(self, value: Any, label: str) -> float | None:
        return self._subtitles_facade._edit_number(value, label)

    @Slot(int, "QVariantMap")
    def updateSegment(self, index: int, changes: dict[str, Any]) -> None:
        return self._subtitles_facade.updateSegment(index, changes)

    def _snap_time(self, value: float, moving_index: int, grid_seconds: float) -> float:
        return self._subtitles_facade._snap_time(value, moving_index, grid_seconds)

    @Slot(int, float, float, float)
    def moveSegment(self, index: int, start: float, end: float, snap_seconds: float) -> None:
        return self._subtitles_facade.moveSegment(index, start, end, snap_seconds)

    @Slot(int, float, float)
    def resizeSegmentStart(self, index: int, start: float, snap_seconds: float) -> None:
        return self._subtitles_facade.resizeSegmentStart(index, start, snap_seconds)

    @Slot(int, float, float)
    def resizeSegmentEnd(self, index: int, end: float, snap_seconds: float) -> None:
        return self._subtitles_facade.resizeSegmentEnd(index, end, snap_seconds)

    @Slot(float)
    def addSegment(self, at_seconds: float) -> None:
        return self._subtitles_facade.addSegment(at_seconds)

    @Slot()
    def deleteSelectedSegment(self) -> None:
        return self._subtitles_facade.deleteSelectedSegment()

    @Slot(float)
    def splitSelectedSegment(self, at_seconds: float) -> None:
        return self._subtitles_facade.splitSelectedSegment(at_seconds)

    @Slot()
    def undoSubtitleEdit(self) -> None:
        return self._subtitles_facade.undoSubtitleEdit()

    @Slot()
    def undoCutEdit(self) -> None:
        return self._subtitles_facade.undoCutEdit()

    @Slot()
    def undoEdit(self) -> None:
        return self._subtitles_facade.undoEdit()

    @Slot()
    def redoSubtitleEdit(self) -> None:
        return self._subtitles_facade.redoSubtitleEdit()

    @Slot()
    def redoCutEdit(self) -> None:
        return self._subtitles_facade.redoCutEdit()

    @Slot()
    def redoEdit(self) -> None:
        return self._subtitles_facade.redoEdit()

    @Slot(result=bool)
    def saveProject(self) -> bool:
        if self._project is None or not self._project_path:
            self._set_status("保存する字幕編集プロジェクトがありません", "CHECK")
            return False
        self.autosave_timer.stop()
        try:
            self._project_editor_controller.save(emit=False)
        except (OSError, SubtitleProjectError, TypeError, ValueError) as error:
            self._set_status(f"プロジェクトを保存できません: {error}", "ERROR")
            return False
        self._sync_subtitle_model()
        self.projectChanged.emit()
        self._set_status("字幕編集を保存しました", "SAVED")
        return True

    def _autosave_project(self) -> None:
        self._project_editor_controller.autosave()

    @Slot(int, str, str)
    def _finish_autosave(self, revision: int, path: str, error: str) -> None:
        ignored = (revision, path) in self._ignored_autosaves
        self._project_editor_controller.finish_autosave(revision, path, error)
        if error and not ignored:
            self._set_status(f"保存に失敗しました: {error}", "ERROR")

    def _wait_for_autosave(self) -> None:
        self._project_editor_controller.wait_for_autosave()

    def _shutdown_executor(self) -> None:
        if hasattr(self, "autosave_timer"):
            self.autosave_timer.stop()
        if getattr(self, "_project_dirty", False) and getattr(self, "_project_path", ""):
            self.saveProject()
        if hasattr(self, "_project_editor_controller"):
            self._project_editor_controller.shutdown()
        controller = getattr(self, "_audio_preview_controller", None)
        if controller is not None:
            controller.shutdown()
        super()._shutdown_executor()

    def _update_project_settings(self, settings: dict[str, Any]) -> None:
        if self._project is None:
            return
        subtitle = self._project.get("subtitle_settings", {})
        self._project["subtitle_settings"] = {
            **subtitle,
            "font_size": int(settings.get("subtitle_font_size", subtitle.get("font_size", 50))),
            "outline_color": normalize_rgb_color(settings.get("subtitle_outline_color", subtitle.get("outline_color", "#000000"))),
            "outline_thickness": max(0, min(20, int(settings.get("subtitle_outline_thickness", subtitle.get("outline_thickness", 3))))),
            "volume_scale_percent": float(settings.get("subtitle_volume_scale_percent", subtitle.get("volume_scale_percent", 20.0))),
            "max_gap_seconds": float(settings.get("subtitle_max_gap_seconds", subtitle.get("max_gap_seconds", 0.32))),
            "end_padding_seconds": float(settings.get("subtitle_end_padding_seconds", subtitle.get("end_padding_seconds", 0.08))),
            "min_duration_seconds": float(settings.get("subtitle_min_duration_seconds", subtitle.get("min_duration_seconds", 0.35))),
        }
        self._mark_project_dirty()

    @Slot("QVariantMap")
    def buildSubtitlePreview(self, settings: dict[str, Any]) -> None:
        return self._subtitles_facade.buildSubtitlePreview(settings)

    def _start_command(self, command: list[str], job: str, status: str) -> None:
        return self._workflow_facade._start_command(command, job, status)

    def _has_audio_source(self, audio_files: list[str], audio_tracks: list[dict[str, Any]] | None = None) -> bool:
        return self._workflow_facade._has_audio_source(audio_files, audio_tracks)

    def _default_video_audio_track(self, audio_tracks: list[dict[str, Any]] | None = None) -> str:
        return self._workflow_facade._default_video_audio_track(audio_tracks)

    def _transcription_capability(self, device: str) -> ActionCapability:
        return self._workflow_facade._transcription_capability(device)

    @Property("QVariantMap", notify=actionCapabilitiesChanged)
    def actionCapabilities(self) -> dict[str, Any]:
        return self._workflow_facade.actionCapabilities

    @Slot(str, result="QVariantMap")
    def actionCapabilitiesForDevice(self, device: str) -> dict[str, Any]:
        return self._workflow_facade.actionCapabilitiesForDevice(device)

    @Slot("QVariantMap", bool)
    def startTranscription(
        self,
        settings: dict[str, Any],
        overwrite_project: bool = False,
        project_path: str | None = None,
    ) -> None:
        return self._workflow_facade.startTranscription(settings, overwrite_project, project_path)

    @Slot("QVariantMap")
    def startProcessing(self, settings: dict[str, Any]) -> None:
        return self._workflow_facade.startProcessing(settings)

    @Slot("QVariantMap")
    def renderVideo(self, settings: dict[str, Any]) -> None:
        return self._workflow_facade.renderVideo(settings)

    def _start_render(self, settings: dict[str, Any], *, short: bool) -> None:
        return self._workflow_facade._start_render(settings, short=short)

    @Slot()
    def checkForUpdates(self) -> None:
        return self._updates_facade.checkForUpdates()

    def _check_for_updates_worker(self) -> None:
        return self._updates_facade._check_for_updates_worker()

    def _on_update_check_finished(self, info: Any, error: str) -> None:
        return self._updates_facade._on_update_check_finished(info, error)

    @Slot()
    def dismissUpdateInfo(self) -> None:
        return self._updates_facade.dismissUpdateInfo()

    @Slot()
    def downloadUpdate(self) -> None:
        return self._updates_facade.downloadUpdate()

    def _download_update_worker(self) -> None:
        return self._updates_facade._download_update_worker()

    def _on_update_download_progress(self, downloaded: int, total: int, speed: float) -> None:
        return self._updates_facade._on_update_download_progress(downloaded, total, speed)

    def _on_update_download_finished(self, package_path: str, error: str) -> None:
        return self._updates_facade._on_update_download_finished(package_path, error)

    @Slot()
    def cancelUpdateDownload(self) -> None:
        return self._updates_facade.cancelUpdateDownload()

    @Slot()
    def applyDownloadedUpdate(self) -> None:
        return self._updates_facade.applyDownloadedUpdate()

    @Slot()
    def applyUpdate(self) -> None:
        return self._updates_facade.applyUpdate()

    @Slot()
    def cancelProcessing(self) -> None:
        return self._workflow_facade.cancelProcessing()

    @Slot()
    def restartApplication(self) -> None:
        return self._updates_facade.restartApplication()

    @Slot()
    def renderShortVideo(self) -> None:
        return self._workflow_facade.renderShortVideo()

    @Slot()
    def reconnectCodexChat(self) -> None:
        return self._ai_facade.reconnectCodexChat()

    @Slot()
    def startCodexLogin(self) -> None:
        return self._ai_facade.startCodexLogin()

    @Slot()
    def reloginCodex(self) -> None:
        return self._ai_facade.reloginCodex()

    @Slot()
    def logoutCodex(self) -> None:
        return self._ai_facade.logoutCodex()

    @Slot()
    def openCodexLoginPage(self) -> None:
        return self._ai_facade.openCodexLoginPage()

    @Slot(str)
    def selectCodexModel(self, model: str) -> None:
        return self._ai_facade.selectCodexModel(model)

    @Slot(str, result=bool)
    def selectAIProvider(self, provider_id: str) -> bool:
        return self._ai_facade.selectAIProvider(provider_id)

    @Slot()
    def reconnectAIChat(self) -> None:
        return self._ai_facade.reconnectAIChat()

    @Slot()
    def startAIProviderLogin(self) -> None:
        return self._ai_facade.startAIProviderLogin()

    @Slot()
    def reloginAIProvider(self) -> None:
        return self._ai_facade.reloginAIProvider()

    @Slot()
    def logoutAIProvider(self) -> None:
        return self._ai_facade.logoutAIProvider()

    @Slot()
    def openAIProviderLoginPage(self) -> None:
        return self._ai_facade.openAIProviderLoginPage()

    @Slot(str)
    @Slot(str, str, float, float)
    def sendCodexChatMessage(
        self,
        message: str,
        requested_scope: str = "auto",
        range_start: float = 0.0,
        range_end: float = 0.0,
    ) -> None:
        return self._ai_facade.sendCodexChatMessage(message, requested_scope, range_start, range_end)

    @staticmethod
    def _is_audio_mix_chat_request(message: str) -> bool:
        # Route natural-language sound adjustments to the typed audio proposal.
        return AIChatFacade._is_audio_mix_chat_request(message)

    def _start_audio_mix_chat_proposal(self, message: str) -> None:
        return self._ai_facade._start_audio_mix_chat_proposal(message)

    @Slot()
    def stopCodexChat(self) -> None:
        return self._ai_facade.stopCodexChat()

    @Slot()
    def startNewCodexChat(self) -> None:
        return self._ai_facade.startNewCodexChat()

    def _queue_codex_system_log(self, message: object, *, severity: str = "INFO") -> None:
        return self._ai_facade._queue_codex_system_log(message, severity=severity)

    def _create_codex_chat_client(self, cwd: str | Path | None = None) -> CodexAppServerClient:
        return self._ai_facade._create_codex_chat_client(cwd)

    def _create_gemini_chat_provider(self) -> GeminiAcpProvider:
        return self._ai_facade._create_gemini_chat_provider()

    @Slot(str, str, float, float)
    def startCodexEdit(
        self,
        prompt: str,
        scope: str,
        range_start: float = 0.0,
        range_end: float = 0.0,
    ) -> None:
        return self._ai_facade.startCodexEdit(prompt, scope, range_start, range_end)

    @Slot(float)
    def setCodexCurrentTime(self, seconds: float) -> None:
        return self._ai_facade.setCodexCurrentTime(seconds)

    @Slot()
    def stopCodexEdit(self) -> None:
        return self._ai_facade.stopCodexEdit()

    @Slot("QVariantList")
    def applyCodexProposal(self, selected_operation_ids: list[Any] | None = None) -> None:
        return self._ai_facade.applyCodexProposal(selected_operation_ids)

    @Slot()
    def discardCodexProposal(self) -> None:
        return self._ai_facade.discardCodexProposal()

    def dispatch_codex_action(
        self,
        payload: Mapping[str, Any],
        *,
        trusted_scope: ActionScope,
    ) -> ActionResult:
        return self._ai_facade.dispatch_codex_action(payload, trusted_scope=trusted_scope)

    def codex_render_output_exists(self, *, short: bool) -> bool:
        return self._ai_facade.codex_render_output_exists(short=short)

    def _on_codex_state(self, _snapshot: CodexSessionSnapshot) -> None:
        return self._ai_facade._on_codex_state(_snapshot)

    def _on_codex_message(self, _message: str) -> None:
        return self._ai_facade._on_codex_message(_message)

    def _on_codex_proposal(self, proposal: Mapping[str, Any]) -> None:
        return self._ai_facade._on_codex_proposal(proposal)

    def _on_codex_audio_mix_state(self, _snapshot: CodexSessionSnapshot) -> None:
        return self._ai_facade._on_codex_audio_mix_state(_snapshot)

    def _on_codex_audio_mix_proposal(self, proposal: Mapping[str, Any]) -> None:
        return self._ai_facade._on_codex_audio_mix_proposal(proposal)

    def _on_codex_provider_state(self, snapshot: CodexChatSnapshot) -> None:
        return self._ai_facade._on_codex_provider_state(snapshot)

    def _on_gemini_provider_state(self, snapshot: CodexChatSnapshot) -> None:
        return self._ai_facade._on_gemini_provider_state(snapshot)

    def _persist_ai_provider(self, provider_id: str) -> None:
        return self._ai_facade._persist_ai_provider(provider_id)

    def _on_codex_chat_state(self, snapshot: CodexChatSnapshot) -> None:
        return self._ai_facade._on_codex_chat_state(snapshot)

    def _persist_codex_model(self, model: str) -> None:
        return self._ai_facade._persist_codex_model(model)

    def _persist_gemini_model(self, model: str) -> None:
        return self._ai_facade._persist_gemini_model(model)

    def _dispatch_codex_callback(self, callback: Callable[[], None]) -> None:
        return self._ai_facade._dispatch_codex_callback(callback)

    @Slot(object)
    def _run_codex_callback(self, callback: object) -> None:
        return self._ai_facade._run_codex_callback(callback)

    def _process_started(self) -> None:
        return self._workflow_facade._process_started()

    def _record_dependency_snapshot(self, *, stage: str) -> None:
        status = self._dependencies.to_dict()
        fields = ", ".join(
            f"{key}={str(status[key]).lower() if isinstance(status[key], bool) else status[key]}"
            for key in ("ffmpeg", "ffprobe", "whisperx", "cuda", "nvenc", "ready")
        )
        missing = ",".join(str(item) for item in status.get("missing", ())) or "none"
        self._record_log(
            f"依存関係: {fields}, missing={missing}",
            severity="INFO" if bool(status.get("ready")) else "WARNING",
            component="runtime",
            stage=stage,
        )

    def _record_startup_diagnostics(self) -> None:
        info = self._application_info
        self._record_log(
            "アプリケーション: "
            f"version={info.get('version', 'unknown')}, "
            f"distribution={info.get('distribution', 'unknown')}",
            component="startup",
            stage="STARTUP",
        )
        self._record_log(
            "パス: "
            f"executable={info.get('executablePath', sys.executable)}, "
            f"workspace={self.workspace_root}, "
            f"config={self.gui_config_path}, "
            f"session_log={self._application_logger.log_path}",
            component="startup",
            stage="STARTUP",
        )
        runtime = runtime_diagnostic_info()
        self._record_log(
            "実行環境: " + ", ".join(f"{key}={value}" for key, value in runtime.items()),
            component="runtime",
            stage="STARTUP",
        )
        self._record_dependency_snapshot(stage="STARTUP")
        config_source = "user" if self.gui_config_path.is_file() else "default"
        self._record_log(
            "設定: "
            f"source={config_source}, "
            f"device={self._settings.get('device', 'unknown')}, "
            f"model={self._settings.get('model', 'unknown')}, "
            f"compute_type={self._settings.get('compute_type', 'unknown')}, "
            f"video_codec={self._settings.get('video_codec', 'unknown')}, "
            f"codex_model={self._settings.get('codex_model') or 'auto'}",
            component="config",
            stage="STARTUP",
        )
        if self._application_logger.write_error:
            self._record_log(
                "セッションログファイルへ書き込めません: "
                f"{self._application_logger.write_error}",
                severity="WARNING",
                component="startup",
                stage="STARTUP",
            )

    @Slot()
    def refreshDependencies(self) -> None:
        super().refreshDependencies()
        self._record_dependency_snapshot(stage="DEPENDENCY_CHECK")

    def _set_status(self, status: str, stage: str) -> None:
        previous = (getattr(self, "_status", ""), getattr(self, "_stage", ""))
        super()._set_status(status, stage)
        if not hasattr(self, "_application_logger") or previous == (status, stage):
            return
        severity = (
            "ERROR"
            if stage == "ERROR"
            else "WARNING" if stage in {"SETUP", "CHECK", "CANCELLED"} else "INFO"
        )
        self._record_log(
            status,
            severity=severity,
            component="gui",
            stage=stage,
        )

    def _record_log(
        self,
        message: object,
        *,
        severity: str = "INFO",
        component: str = "gui",
        job: str = "",
        stage: str = "",
        process_id: int | None = None,
        exit_code: int | None = None,
        preserve_in_memory: bool | None = None,
        pin_in_memory: bool | None = None,
    ) -> None:
        if preserve_in_memory is None:
            preserve_in_memory = component in {
                "startup",
                "runtime",
                "config",
                "qml",
                "codex",
                "gui",
            } or severity.upper() in {"WARNING", "ERROR"}
        if pin_in_memory is None:
            pin_in_memory = component == "startup" or stage == "STARTUP"
        self._application_logger.append(
            message,
            severity=severity,
            component=component,
            job=job,
            stage=stage,
            process_id=process_id,
            exit_code=exit_code,
            preserve_in_memory=preserve_in_memory,
            pin_in_memory=pin_in_memory,
        )
        self._log = self._application_logger.text
        self.logChanged.emit()
        self.applicationLogChanged.emit()

    @Property(str, notify=applicationLogChanged)
    def logFilePath(self) -> str:
        return str(self._application_logger.log_path)

    def _related_process_log_tail(self) -> str:
        if self._active_job == "transcribe" and self.projectSavePath:
            output_directory = str(project_work_directory(self.projectSavePath))
        else:
            transcription = (self._project or {}).get("transcription", {})
            output_directory = str(transcription.get("work_dir") or self.videoOutputDirectory).strip()
        if not output_directory:
            return ""
        transcript_directory = Path(output_directory) / "transcripts"
        if not transcript_directory.is_dir():
            return ""
        try:
            candidates = sorted(
                transcript_directory.glob("*.whisperx.log"),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )[:3]
        except OSError:
            return ""

        sections: list[str] = []
        for path in candidates:
            try:
                with path.open("rb") as handle:
                    handle.seek(0, os.SEEK_END)
                    size = handle.tell()
                    handle.seek(max(0, size - 8_000), os.SEEK_SET)
                    tail = handle.read().decode("utf-8", errors="replace").strip()
            except OSError:
                continue
            if tail:
                sections.append(f"WhisperX log ({path.name}):\n{tail}")
        return "\n\n".join(sections)

    def _capture_process_diagnostic(
        self,
        *,
        job: str,
        outcome: str,
        exit_code: int | None,
    ) -> None:
        self._last_process_diagnostic = ProcessDiagnosticSnapshot(
            occurred_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            job=job,
            component=job or "process",
            stage=self.stage,
            status=self.status,
            outcome=outcome,
            exit_code=exit_code,
            process_error=self._pending_process_error,
            log_text=self._application_logger.text,
            related_log_tail=self._related_process_log_tail(),
            runtime=runtime_diagnostic_info(),
        )
        self.lastProcessDiagnosticChanged.emit()

    @Slot()
    def copyLogsToClipboard(self) -> None:
        self.clipboard().setText(
            self._application_logger.diagnostic_text(
                status=self.status,
                stage=self.stage,
                runtime=runtime_diagnostic_info(),
            )
        )

    @Slot()
    def copyErrorLogsToClipboard(self) -> None:
        if self._last_process_diagnostic is not None:
            self.clipboard().setText(
                self._application_logger.diagnostic_text(
                    snapshot=self._last_process_diagnostic,
                )
            )
            return
        self.clipboard().setText(
            self._application_logger.diagnostic_text(
                status=self.status,
                stage=self.stage,
                runtime=runtime_diagnostic_info(),
            )
        )

    @Slot()
    def openLogFolder(self) -> None:
        if not QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self._application_logger.log_directory))
        ):
            self._set_status("ログ保存先を開けませんでした", "ERROR")

    def _read_process_output(self, output: str | None = None) -> None:
        return self._workflow_facade._read_process_output(output)

    def _finish_processing_progress(self, outcome: str) -> None:
        return self._workflow_facade._finish_processing_progress(outcome)

    def _process_error(self, error: QProcess.ProcessError) -> None:
        return self._workflow_facade._process_error(error)

    def _update_stage(self, output: str) -> None:
        return self._workflow_facade._update_stage(output)

    def _process_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        return self._workflow_facade._process_finished(exit_code, _exit_status)


def main() -> None:
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    app = EditBayBackend(sys.argv)
    app.setApplicationName(APP_TITLE)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", app)
    qml_path = Path(__file__).resolve().parent / "ui" / "Main.qml"
    app._record_log(
        f"QML UIの読み込みを開始します: {qml_path}",
        component="qml",
        stage="STARTUP",
    )

    def record_qml_warnings(warnings: list[object]) -> None:
        for warning in warnings:
            app._record_log(
                warning,
                severity="WARNING",
                component="qml",
                stage="QML",
            )

    engine.warnings.connect(record_qml_warnings)
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    if not engine.rootObjects():
        app._record_log(
            f"QML UIを読み込めませんでした: {qml_path}",
            severity="ERROR",
            component="qml",
            stage="ERROR",
        )
        raise SystemExit(f"Could not load GUI: {qml_path}")
    app._record_log(
        "QML UIの読み込みが完了しました",
        component="qml",
        stage="READY",
    )
    smoke_result_path = os.environ.get("SUBTITLE_EDIT_BAY_STARTUP_SMOKE_RESULT", "").strip()
    if smoke_result_path:
        smoke_result = {
            **resolve_application_info(),
            "qmlLoaded": True,
            "entrypoint": "SubtitleEditBayLauncher.exe",
        }
        Path(smoke_result_path).write_text(
            json.dumps(smoke_result, ensure_ascii=False), encoding="utf-8"
        )
        QTimer.singleShot(0, app.quit)
    app.aboutToQuit.connect(
        lambda: app._record_log(
            "アプリケーションを終了します",
            component="startup",
            stage="SHUTDOWN",
        )
    )
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
