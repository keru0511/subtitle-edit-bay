from __future__ import annotations

from bisect import bisect_left, bisect_right
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
from uuid import uuid4

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

from .platform_updates import installer_download_name
from .platform_paths import audio_preview_directory
from .process_utils import detached_subprocess_kwargs

from .audio_mixer import (
    DEFAULT_AUDIO_TRACK,
    active_audio_mix_channels,
    reconcile_audio_mix,
    reset_audio_mix,
    AudioMixError,
    update_audio_mix_channel,
)
from .audio_mix_proposal import (
    AUDIO_MIX_PROPOSAL_OUTPUT_SCHEMA,
    AudioMixProposal,
    AudioMixProposalError,
    apply_audio_mix_proposal,
    build_audio_mix_context,
    build_audio_mix_proposal,
    build_audio_mix_proposal_prompt,
)
from .audio_preview_cache import (
    AudioPreviewCacheResult,
    clear_audio_preview_cache,
    prepare_audio_preview_cache,
)
from .gui_codex_state import (
    CODEX_OUTPUT_SCHEMA,
    CODEX_SCOPES,
    CodexSessionController,
    CodexSessionError,
    CodexSessionSnapshot,
    build_codex_context,
)
from .codex_app_server_client import CodexAppServerClient
from .codex_actions import ActionResult, ActionScope, build_gui_action_dispatcher
from .codex_chat_routing import route_subtitle_chat_request
from .codex_runtime import detect_codex
from .gui_codex_chat_state import (
    CodexChatController,
    CodexChatError,
    CodexChatSnapshot,
)
from .gui_ai_chat_state import AIProviderChatRouter
from .gemini_acp_provider import GeminiAcpProvider
from .gui_audio_preview_controller import AudioPreviewController
from .gui_project_editor_controller import ProjectEditorController
from .application_logging import ApplicationLogger, ProcessDiagnosticSnapshot
from .application_info import resolve_application_info
from .realtime_audio_mixer import RealtimeAudioMixer
from .color_config import normalize_rgb_color, save_speaker_color
from .gui_base import APP_TITLE, LegacyEditBayBackend
from .gui_source_state import SourceSelection, build_speaker_entries_from_files
from .gui_workspace_controller import WorkspaceNavigationController
from .editor_workspace import (
    EditModeCapabilities,
    EditorWorkspaceState,
    TimeMapping,
    build_edit_mode_capabilities,
)
from .gui_state import build_gui_transcribe_command
from .workflow_actions import (
    ActionCapability,
    prepare_render_request,
    render_capability,
    render_output_path,
    transcription_capability,
)
from .media_probe import probe_media_duration
from .subtitle_project import (
    MIN_SEGMENT_DURATION_SECONDS,
    SubtitleProjectError,
    assign_project_layout_rows,
    create_project,
    derive_project_path,
    load_project,
    normalize_segment,
    project_work_directory,
    save_project,
)
from .processing_progress import ProcessingProgress, parse_ffmpeg_timestamp, parse_progress_events
from .short_video_schema import VALID_FIT_MODES, VALID_TRANSITION_TYPES
from .subtitle_line_count import segment_editor_text, segment_preview_text
from .subtitle_workflow import build_project_ass
from .render_ass import style_name_for_speaker
from .runtime_dependencies import runtime_diagnostic_info
from .video_timeline import VideoTimeline, VideoTimelineError, timeline_from_project
from .video_sequence import VideoSequence, VideoSequenceError
from . import update_manager, updater


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
        """Return the backend-owned workspace identity for QML."""

        return self._workspace_navigation.current_workspace

    @Property("QVariantMap", notify=workspacePlayerStateChanged)
    def workspacePlayerState(self) -> dict[str, Any]:
        """Return the active workspace player state at the navigation boundary."""

        return self._workspace_navigation.current_player.as_dict()

    @Property("QVariantMap", notify=workspacePlayerStateChanged)
    def workspacePlayerStates(self) -> dict[str, dict[str, Any]]:
        """Expose isolated transport snapshots for diagnostics and QML."""

        return self._workspace_navigation.player_states()

    @Slot(str, int, bool, result=bool)
    def setWorkspacePlayerState(
        self,
        workspace: str,
        position_ms: int,
        playing: bool,
    ) -> bool:
        changed = self._workspace_navigation.update_player_state(
            workspace,
            position_ms,
            playing=playing,
        )
        if changed:
            self.workspacePlayerStateChanged.emit()
        return changed

    @Slot(str, result=bool)
    def switchWorkspace(self, workspace: str) -> bool:
        result = self._workspace_navigation.switch_workspace(
            workspace,
            running=self._running,
            active_job=self._active_job,
        )
        if not result.accepted:
            self._set_status(result.reason, "BUSY" if self._running or self._active_job else "CHECK")
            return False
        if result.changed:
            self.workspaceChanged.emit()
            self.workspacePlayerStateChanged.emit()
        return True

    def _edit_mode_capabilities(self) -> EditModeCapabilities:
        return build_edit_mode_capabilities(
            project_loaded=self.projectLoaded,
            preview_available=bool(self.previewUrl),
            audio_available=self.audioMixerAvailable,
            cut_available=self._cut_editor_available,
        )

    def _refresh_editor_workspace(self) -> None:
        mode_changed = self._editor_workspace.ensure_available_mode(self._edit_mode_capabilities())
        self.editorCapabilitiesChanged.emit()
        if mode_changed:
            self.editorModeChanged.emit()

    @Property(str, notify=editorModeChanged)
    def currentEditMode(self) -> str:
        return self._editor_workspace.current_mode

    @Property("QVariantMap", notify=editorCapabilitiesChanged)
    def editorModeCapabilities(self) -> dict[str, object]:
        return self._edit_mode_capabilities().as_dict()

    @Property("QVariantMap", notify=editorPlayheadChanged)
    def editorPlayhead(self) -> dict[str, object]:
        return self._editor_workspace.playhead

    def _cut_timeline_model(self) -> VideoTimeline:
        if self._project is None:
            return VideoTimeline.from_json(None, source_duration=0.0)
        return timeline_from_project(self._project)

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
        source_position = self._cut_timeline_model().next_playable_source_seconds(
            position_ms / 1000.0
        )
        return int(round(source_position * 1000))

    @Slot(str, result=bool)
    def selectEditMode(self, mode: str) -> bool:
        changed = self._editor_workspace.select_mode(mode, self._edit_mode_capabilities())
        if changed:
            self.editorModeChanged.emit()
        return changed

    @Slot(int, str, result=bool)
    def setEditorPlayhead(self, position_ms: int, basis: str) -> bool:
        changed = self._editor_workspace.set_playhead(position_ms, basis)
        if changed:
            self.editorPlayheadChanged.emit()
        return changed

    def set_editor_time_mapping(self, mapping: TimeMapping | None) -> None:
        """Install the source/output time mapper supplied by the cut editor."""

        self._editor_workspace.set_mapping(mapping)
        self.editorPlayheadChanged.emit()

    def _sync_project_timeline(self) -> None:
        self.set_editor_time_mapping(
            self._cut_timeline_model() if self._project is not None else None
        )
        self.cutTimelineChanged.emit()

    def set_cut_editor_available(self, available: bool) -> None:
        """Enable the cut mode when the non-destructive cut editor is connected."""

        available = bool(available)
        if self._cut_editor_available == available:
            return
        self._cut_editor_available = available
        self._refresh_editor_workspace()

    def _reset_editor_timing(self) -> None:
        self._editor_workspace.set_mapping(None)
        self._editor_workspace.reset_playhead()
        self.editorPlayheadChanged.emit()

    @Property(bool, notify=lastProcessDiagnosticChanged)
    def hasLastProcessDiagnostic(self) -> bool:
        return self._last_process_diagnostic is not None

    @Property(str, notify=updateInfoChanged)
    def updateCurrentVersion(self) -> str:
        return self._update_info.current_version if self._update_info else ""

    @Property(str, notify=updateInfoChanged)
    def updateLatestVersion(self) -> str:
        return self._update_info.latest_version if self._update_info else ""

    @Property(str, notify=updateInfoChanged)
    def updateReleaseNotes(self) -> str:
        return self._update_info.release_notes if self._update_info else ""

    @Property(str, notify=updateInfoChanged)
    def updateDownloadUrl(self) -> str:
        return self._update_info.download_url if self._update_info else ""

    @Property(str, notify=codexStateChanged)
    def codexState(self) -> str:
        return self._codex_session.snapshot.state

    @Property(str, notify=codexMessageChanged)
    def codexMessage(self) -> str:
        return self._codex_session.snapshot.message

    @Property(str, notify=codexStateChanged)
    def codexError(self) -> str:
        return self._codex_session.snapshot.error

    @Property("QVariantMap", notify=codexProposalChanged)
    def codexProposal(self) -> dict[str, Any]:
        return dict(self._codex_proposal or {})

    @Property("QVariantMap", notify=audioMixProposalChanged)
    def audioMixProposal(self) -> dict[str, Any]:
        return deepcopy(self._audio_mix_proposal or {})

    @Property(str, notify=audioMixProposalChanged)
    def audioMixProposalState(self) -> str:
        return self._codex_audio_mix_session.snapshot.state

    @Property(str, notify=audioMixProposalChanged)
    def audioMixProposalError(self) -> str:
        return self._codex_audio_mix_session.snapshot.error

    @Property("QStringList", constant=True)
    def codexScopes(self) -> list[str]:
        return list(CODEX_SCOPES)

    @Property(str, notify=codexChatChanged)
    def codexConnectionState(self) -> str:
        return self._ai_chat.snapshot.connection_state

    @Property(str, notify=codexChatChanged)
    def codexAuthState(self) -> str:
        return self._ai_chat.snapshot.auth_state

    @Property(str, notify=codexChatChanged)
    def codexAuthLabel(self) -> str:
        return self._ai_chat.snapshot.auth_label

    @Property(str, notify=codexChatChanged)
    def codexLoginUrl(self) -> str:
        return self._ai_chat.snapshot.login_url

    @Property(str, notify=codexChatChanged)
    def codexChatState(self) -> str:
        return self._ai_chat.snapshot.chat_state

    @Property(str, notify=codexChatChanged)
    def codexChatError(self) -> str:
        return self._ai_chat.snapshot.error

    @Property("QVariantList", notify=codexChatChanged)
    def codexModels(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._ai_chat.snapshot.models]

    @Property(str, notify=codexChatChanged)
    def codexSelectedModel(self) -> str:
        return self._ai_chat.snapshot.selected_model

    @Property(str, notify=codexChatChanged)
    def codexModelError(self) -> str:
        return self._ai_chat.snapshot.model_error

    @Property("QVariantList", notify=codexChatChanged)
    def codexChatMessages(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._ai_chat.snapshot.messages]

    @Property(str, notify=aiChatChanged)
    def aiChatProviderId(self) -> str:
        return self._ai_chat.active_provider_id

    @Property(str, notify=aiChatChanged)
    def aiChatProviderName(self) -> str:
        return self._ai_chat.active_provider_name

    @Property("QVariantList", notify=aiChatChanged)
    def aiChatProviders(self) -> list[dict[str, Any]]:
        return self._ai_chat.available_providers()

    @Property(bool, notify=aiChatChanged)
    def aiChatModelSelectionSupported(self) -> bool:
        return self._ai_chat.snapshot.model_selection_supported

    @Property(bool, notify=aiChatChanged)
    def aiChatLoginAvailable(self) -> bool:
        return self._ai_chat.snapshot.login_available

    @Property(str, notify=aiChatChanged)
    def aiChatAuthHint(self) -> str:
        snapshot = self._ai_chat.snapshot
        if (
            snapshot.provider_id == "gemini"
            and snapshot.auth_state != "authenticated"
            and not snapshot.login_available
        ):
            return "Gemini CLIでログインしてください"
        return ""

    @Property(bool, notify=updateInfoChanged)
    def updateAvailable(self) -> bool:
        return self._update_info.available if self._update_info else False

    @Property(bool, notify=updateBusyChanged)
    def updateBusy(self) -> bool:
        return self._update_busy

    @Property(str, notify=updateErrorChanged)
    def updateError(self) -> str:
        return self._update_error

    @Property(int, notify=updateDownloadProgressChanged)
    def updateDownloadBytes(self) -> int:
        return self._update_download_bytes

    @Property(int, notify=updateDownloadProgressChanged)
    def updateDownloadTotal(self) -> int:
        return self._update_download_total

    @Property(float, notify=updateDownloadProgressChanged)
    def updateDownloadSpeed(self) -> float:
        return self._update_download_speed

    @Property(bool, notify=updateDownloadProgressChanged)
    def updateDownloadActive(self) -> bool:
        return self._update_download_active

    @Property(bool, notify=updatePackageReadyChanged)
    def updatePackageReady(self) -> bool:
        return self._update_package_ready

    @Property(int, notify=updateInfoChanged)
    def updatePackageSize(self) -> int:
        return int(self._update_info.package_size) if self._update_info else 0

    @Property("QVariantList", notify=segmentsChanged)
    def subtitleSegments(self) -> list[dict[str, Any]]:
        if self._project is None:
            return []
        return deepcopy(self._project.get("segments", []))

    @Property("QVariantMap", notify=segmentsChanged)
    def subtitleLayoutMetrics(self) -> dict[str, float | int]:
        """Return the small aggregate QML needs without copying every segment."""
        return dict(self._subtitle_layout_metrics)

    @Property("QVariantList", notify=shortVideoChanged)
    def shortVideoClips(self) -> list[dict[str, Any]]:
        """Return all clips for callers outside QML.

        QML uses ``shortVideoClipModel`` and ``shortVideoClipAt`` so delegates and
        the preview only materialize the rows they currently need.
        """
        return [
            self._short_video_clip_view_at(index)
            for index in range(self._short_video_clip_count())
        ]

    @Property("QVariantMap", notify=shortVideoChanged)
    def shortVideoSettings(self) -> dict[str, Any]:
        if self._project is None:
            return {}
        section = self._short_video_section()
        return {
            "enabled": bool(section.get("enabled", False)),
            "time_basis": str(section.get("time_basis", "source")),
            "output": deepcopy(section.get("output", {})),
            "global_fit": str(section.get("global_fit", "cover")),
            "global_background_color": str(section.get("global_background_color", "000000")),
            "subtitle_scale_percent": float(section.get("subtitle_scale_percent", 150.0)),
            "transition": deepcopy(section.get("transition", {})),
            "bgm": deepcopy(section.get("bgm", {})),
        }

    @Property("QVariantList", notify=highlightCandidatesChanged)
    def highlightCandidates(self) -> list[dict[str, Any]]:
        return deepcopy(self._highlight_candidates)

    @Property(bool, notify=highlightCandidatesChanged)
    def highlightUndoAvailable(self) -> bool:
        return bool(self._highlight_rejected)

    @Property(str, notify=highlightAnalysisChanged)
    def highlightAnalysisState(self) -> str:
        return self._highlight_status

    @Property(float, notify=highlightAnalysisChanged)
    def highlightAnalysisProgress(self) -> float:
        return self._highlight_progress

    @Property(QObject, constant=True)
    def subtitleModel(self) -> QObject:
        return self._subtitle_model

    @Property(QObject, constant=True)
    def shortVideoClipModel(self) -> QObject:
        return self._short_video_clip_model

    @Property(int, notify=shortVideoClipDataChanged)
    def shortVideoClipCount(self) -> int:
        return self._short_video_clip_count()

    @Slot(int, result="QVariantMap")
    def shortVideoClipAt(self, index: int) -> dict[str, Any]:
        return self._short_video_clip_view_at(index)

    @Property("QVariantList", constant=True)
    def fontChoices(self) -> list[dict[str, str]]:
        return deepcopy(self._font_choices)

    @Property(int, notify=segmentsChanged)
    def segmentCount(self) -> int:
        return len(self._project.get("segments", [])) if self._project else 0

    def _sync_subtitle_model(self) -> None:
        segments = self._project.get("segments", []) if self._project else []
        self._segment_by_id = {str(segment["id"]): segment for segment in segments}
        self._subtitle_model.set_segments(segments)
        self._segment_starts = [float(item["start"]) for item in segments]
        prefix: list[float] = []
        max_end = 0.0
        max_font_scale = 1.0
        max_layout_row = 0
        for segment in segments:
            max_end = max(max_end, float(segment["end"]))
            prefix.append(max_end)
            max_font_scale = max(
                max_font_scale,
                max(0.1, float(segment.get("subtitle_font_scale", 1.0))),
            )
            max_layout_row = max(max_layout_row, int(segment.get("layout_row", 0)))
        self._segment_prefix_max_end = prefix
        self._subtitle_layout_metrics = {
            "maxFontScale": max_font_scale,
            "maxLayoutRow": max_layout_row,
        }
        segment_ids = {str(segment["id"]) for segment in segments}
        self._subtitle_preview_text_cache = {
            segment_id: cached
            for segment_id, cached in self._subtitle_preview_text_cache.items()
            if segment_id in segment_ids
        }
        self._refresh_short_video_clip_data()

    def _on_project_segments_changed(self) -> None:
        """Publish controller segment changes through the existing QML facade."""

        self._sync_subtitle_model()
        self.segmentsChanged.emit()

    def _on_project_history_applied(
        self,
        entry: dict[str, Any],
        _state: str,
    ) -> None:
        """Refresh side effects that are intentionally owned by the facade."""

        if entry.get("kind") == "audio_mix":
            self._notify_audio_mixer_preview(structure_changed=True)
        elif entry.get("kind") == "timeline":
            self._sync_project_timeline()

    @staticmethod
    def _subtitle_preview_signature(segment: dict[str, Any]) -> tuple[object, ...]:
        return (
            str(segment.get("text", "")),
            float(segment["start"]),
            float(segment["end"]),
            int(segment.get("max_width", 24)),
            str(segment.get("subtitle_line_count", segment.get("line_count_override", "auto"))),
            bool(segment.get("manual_text", False)),
        )

    def _preview_text_for_segment(self, segment: dict[str, Any]) -> str:
        segment_id = str(segment["id"])
        signature = self._subtitle_preview_signature(segment)
        cached = self._subtitle_preview_text_cache.get(segment_id)
        if cached is not None and cached[0] == signature:
            return cached[1]
        preview_text = segment_preview_text(segment)
        self._subtitle_preview_text_cache[segment_id] = (signature, preview_text)
        return preview_text

    def _segment_view(self, segment: dict[str, Any], source_index: int | None = None) -> dict[str, Any]:
        view = {
            "id": str(segment["id"]),
            "start": float(segment["start"]),
            "end": float(segment["end"]),
            "text": str(segment.get("text", "")),
            "preview_text": self._preview_text_for_segment(segment),
            "speaker": str(segment.get("speaker", "")),
            "layout_row": int(segment.get("layout_row", 0)),
            "subtitle_font_scale": float(segment.get("subtitle_font_scale", 1.0)),
            "subtitle_font_family": str(segment.get("subtitle_font_family", "")),
        }
        if source_index is not None:
            view["sourceIndex"] = source_index
        return view

    def _short_video_section(self) -> dict[str, Any]:
        if self._project is None:
            return {}
        section = self._project.setdefault(
            "short_video",
            {
                "enabled": False,
                "time_basis": "source",
                "output": {"width": 1080, "height": 1920, "fps": 30},
                "global_fit": "cover",
                "global_background_color": "000000",
                "subtitle_scale_percent": 150.0,
                "transition": {"type": "crossfade", "duration": 0.5},
                "bgm": {"path": "", "in": 0.0, "out": 0.0, "start": 0.0, "volume": 0.3},
                "clips": [],
            },
        )
        if not isinstance(section, dict):
            section = self._project["short_video"] = {
                "enabled": False,
                "time_basis": "source",
                "output": {"width": 1080, "height": 1920, "fps": 30},
                "global_fit": "cover",
                "global_background_color": "000000",
                "subtitle_scale_percent": 150.0,
                "transition": {"type": "crossfade", "duration": 0.5},
                "bgm": {"path": "", "in": 0.0, "out": 0.0, "start": 0.0, "volume": 0.3},
                "clips": [],
            }
        return section

    def _find_segment_by_id(self, segment_id: str) -> dict[str, Any] | None:
        return self._segment_by_id.get(str(segment_id))

    def _short_video_clip_count(self) -> int:
        if self._project is None:
            return 0
        section = self._project.get("short_video", {})
        if not isinstance(section, dict):
            return 0
        clips = section.get("clips", [])
        return len(clips) if isinstance(clips, list) else 0

    def _short_video_clip_view_at(self, index: int) -> dict[str, Any]:
        if self._project is None:
            return {}
        section = self._project.get("short_video", {})
        if not isinstance(section, dict):
            return {}
        clips = section.get("clips", [])
        if not isinstance(clips, list) or not 0 <= index < len(clips):
            return {}
        clip = clips[index]
        if not isinstance(clip, dict):
            return {}
        return self._build_short_video_clip_view(clip, index)

    def _refresh_short_video_clip_data(self) -> None:
        self._short_video_clip_model.refresh()
        self.shortVideoClipDataChanged.emit()

    def _build_short_video_clip_view(self, clip: dict[str, Any], index: int) -> dict[str, Any]:
        segment_id = str(clip.get("segment_id", ""))
        segment = self._find_segment_by_id(segment_id) or {}
        section = self._short_video_section()
        global_fit = str(section.get("global_fit", "cover"))
        global_background_color = str(section.get("global_background_color", "000000"))
        fit = str(clip.get("fit", global_fit))
        background_color = str(clip.get("background_color", global_background_color))
        start = float(clip.get("start", segment.get("start", 0.0)))
        end = float(clip.get("end", segment.get("end", 0.0)))
        return {
            "index": index,
            "segment_id": segment_id,
            "start": start,
            "end": end,
            "fit": fit,
            "background_color": background_color,
            "text": str(segment.get("text", clip.get("text", ""))),
            "speaker": str(segment.get("speaker", clip.get("speaker", ""))),
            "preview_text": self._preview_text_for_segment(segment) if segment else str(clip.get("text", "")),
        }

    @Slot()
    def initializeShortVideoClips(self) -> None:
        if self._project is None:
            return
        section = self._short_video_section()
        if section.get("clips"):
            return
        clips: list[dict[str, Any]] = []
        for segment in sorted(
            self._project.get("segments", []),
            key=lambda item: (float(item.get("start", 0.0)), float(item.get("end", 0.0)), str(item.get("id", ""))),
        ):
            clips.append(
                {
                    "segment_id": str(segment.get("id", "")),
                    "start": float(segment.get("start", 0.0)),
                    "end": float(segment.get("end", 0.0)),
                }
            )
        section["enabled"] = True
        section["clips"] = clips
        self._mark_project_dirty()
        self.projectDataChanged.emit()
        self.shortVideoChanged.emit()

    @Slot(str, result=bool)
    def addShortVideoClip(self, segment_id: str) -> bool:
        if self._project is None or self._running:
            return False
        segment = self._find_segment_by_id(segment_id)
        if segment is None:
            return False
        section = self._short_video_section()
        clips = list(section.get("clips", []))
        clips.append(
            {
                "segment_id": segment_id,
                "start": float(segment.get("start", 0.0)),
                "end": float(segment.get("end", 0.0)),
            }
        )
        section["clips"] = clips
        self._mark_project_dirty()
        self.projectDataChanged.emit()
        self.shortVideoChanged.emit()
        return True

    @Slot(float, float, result=bool)
    def addShortVideoClipByRange(self, start: float, end: float) -> bool:
        if self._project is None or self._running:
            return False
        try:
            start = float(start)
            end = float(end)
        except (TypeError, ValueError, OverflowError):
            return False
        if not math.isfinite(start) or not math.isfinite(end):
            return False
        duration = max(0.0, float(self.projectDuration))
        if start < 0.0 or start >= end or (duration > 0.0 and end > duration):
            return False
        section = self._short_video_section()
        clips = list(section.get("clips", []))
        clips.append({"segment_id": "", "start": round(start, 3), "end": round(end, 3)})
        section["enabled"] = True
        section["clips"] = clips
        self._mark_project_dirty()
        self.projectDataChanged.emit()
        self.shortVideoChanged.emit()
        return True

    @Slot(int, result=bool)
    def removeShortVideoClip(self, index: int) -> bool:
        if self._project is None or self._running:
            return False
        section = self._short_video_section()
        clips = list(section.get("clips", []))
        if not 0 <= index < len(clips):
            return False
        clips.pop(index)
        section["clips"] = clips
        self._mark_project_dirty()
        self.projectDataChanged.emit()
        self.shortVideoChanged.emit()
        return True

    @Slot(int, int, result=bool)
    def moveShortVideoClip(self, from_index: int, to_index: int) -> bool:
        if self._project is None or self._running:
            return False
        section = self._short_video_section()
        clips = list(section.get("clips", []))
        if not (0 <= from_index < len(clips)):
            return False
        if to_index < 0:
            to_index = 0
        if to_index > len(clips):
            to_index = len(clips)
        if from_index == to_index:
            return True
        clip = clips.pop(from_index)
        if to_index > from_index:
            to_index -= 1
        clips.insert(to_index, clip)
        section["clips"] = clips
        self._mark_project_dirty()
        self.projectDataChanged.emit()
        self.shortVideoChanged.emit()
        return True

    @Slot(int, "QVariantMap", result=bool)
    def updateShortVideoClip(self, index: int, fields: dict[str, Any]) -> bool:
        if self._project is None or self._running:
            return False
        if not isinstance(fields, dict) or not fields:
            return False
        section = self._short_video_section()
        clips = list(section.get("clips", []))
        if not 0 <= index < len(clips):
            return False
        clip = dict(clips[index])
        trim_requested = "start" in fields or "end" in fields
        if not trim_requested and not any(
            key in fields for key in ("fit", "background_color")
        ):
            return False

        if trim_requested:
            segment = self._find_segment_by_id(str(clip.get("segment_id", "")))
            range_clip = not str(clip.get("segment_id", "")).strip()
            if segment is None and not range_clip:
                return False
            try:
                if segment is None:
                    segment_start = 0.0
                    segment_end = float(self.projectDuration)
                    if segment_end <= 0.0:
                        segment_end = max(float(clip.get("end", 0.0)), 0.0)
                else:
                    segment_start = float(segment.get("start", 0.0))
                    segment_end = float(segment.get("end", segment_start))
                start = float(fields.get("start", clip.get("start", segment_start)))
                end = float(fields.get("end", clip.get("end", segment_end)))
                if not all(
                    math.isfinite(value)
                    for value in (segment_start, segment_end, start, end)
                ):
                    return False
            except (TypeError, ValueError):
                return False
            video_duration = self.projectDuration
            upper_bound = (
                min(segment_end, video_duration)
                if video_duration > 0.0
                else segment_end
            )
            lower_bound = max(0.0, segment_start)
            if (
                upper_bound <= lower_bound
                or start < lower_bound
                or end > upper_bound
                or start >= end
            ):
                return False
            clip["start"] = start
            clip["end"] = end
        if "fit" in fields:
            fit = str(fields["fit"]).lower()
            if fit not in VALID_FIT_MODES:
                return False
            clip["fit"] = fit
        if "background_color" in fields:
            try:
                clip["background_color"] = normalize_rgb_color(fields["background_color"])
            except (TypeError, ValueError, OverflowError):
                return False
        clips[index] = clip
        section["clips"] = clips
        self._mark_project_dirty()
        self.projectDataChanged.emit()
        self.shortVideoChanged.emit()
        return True

    @Slot(str, result=bool)
    def setShortVideoGlobalFit(self, fit: str) -> bool:
        if self._project is None or self._running:
            return False
        fit = str(fit).lower()
        if fit not in VALID_FIT_MODES:
            return False
        section = self._short_video_section()
        section["global_fit"] = fit
        self._mark_project_dirty()
        self.projectDataChanged.emit()
        self.shortVideoChanged.emit()
        return True

    @Slot(str, result=bool)
    def setShortVideoGlobalBackgroundColor(self, color: str) -> bool:
        if self._project is None or self._running:
            return False
        try:
            normalized = normalize_rgb_color(color)
        except (TypeError, ValueError, OverflowError):
            return False
        section = self._short_video_section()
        section["global_background_color"] = normalized
        self._mark_project_dirty()
        self.projectDataChanged.emit()
        self.shortVideoChanged.emit()
        return True

    @Slot(str, float, result=bool)
    def setShortVideoTransition(self, transition_type: str, duration: float) -> bool:
        if self._project is None or self._running:
            return False
        transition_type = str(transition_type).lower()
        if transition_type not in VALID_TRANSITION_TYPES:
            return False
        section = self._short_video_section()
        section["transition"] = {"type": transition_type, "duration": max(0.0, round(float(duration), 3))}
        self._mark_project_dirty()
        self.projectDataChanged.emit()
        self.shortVideoChanged.emit()
        return True

    @Slot("QVariantMap", result=bool)
    def setShortVideoBgm(self, fields: dict[str, Any]) -> bool:
        if self._project is None or self._running:
            return False
        section = self._short_video_section()
        bgm = dict(section.get("bgm", {}))
        if "path" in fields:
            bgm["path"] = str(fields["path"])
        if "in" in fields:
            bgm["in"] = max(0.0, float(fields["in"]))
        if "out" in fields:
            bgm["out"] = max(bgm.get("in", 0.0), float(fields["out"]))
        if "start" in fields:
            bgm["start"] = max(0.0, float(fields["start"]))
        if "volume" in fields:
            volume = float(fields["volume"])
            bgm["volume"] = max(0.0, min(1.0, volume))
        section["bgm"] = bgm
        self._mark_project_dirty()
        self.projectDataChanged.emit()
        self.shortVideoChanged.emit()
        return True

    @Slot(int, int, int, result=bool)
    def setShortVideoOutput(self, width: int, height: int, fps: int) -> bool:
        if self._project is None or self._running:
            return False
        section = self._short_video_section()
        section["output"] = {"width": max(1, int(width)), "height": max(1, int(height)), "fps": max(1, int(fps))}
        self._mark_project_dirty()
        self.projectDataChanged.emit()
        self.shortVideoChanged.emit()
        return True

    @Slot(float, result=bool)
    def setShortVideoSubtitleScale(self, percent: float) -> bool:
        if self._project is None or self._running:
            return False
        section = self._short_video_section()
        section["subtitle_scale_percent"] = max(0.0, float(percent))
        self._mark_project_dirty()
        self.projectDataChanged.emit()
        self.shortVideoChanged.emit()
        return True

    @Slot(result=bool)
    def startHighlightAnalysis(self) -> bool:
        if self._project is None or self._running:
            return False
        if self._highlight_status in {"running", "cancelling"}:
            return False
        self._highlight_generation += 1
        generation = self._highlight_generation
        cancel_event = threading.Event()
        self._highlight_cancel = cancel_event
        had_rejected = bool(self._highlight_rejected)
        self._highlight_rejected = []
        self._highlight_status = "running"
        self._highlight_progress = 0.0
        self.highlightAnalysisChanged.emit()
        if had_rejected:
            self.highlightCandidatesChanged.emit()
        segments = deepcopy(self._project.get("segments", []))
        duration = self.projectDuration
        cache_directory = (
            Path(self._project_path).parent / ".highlight-cache"
            if self._project_path
            else None
        )

        def worker() -> None:
            try:
                from .highlight_candidates import generate_highlight_candidates

                candidates = generate_highlight_candidates(
                    segments,
                    duration_seconds=duration,
                    cancel_check=cancel_event.is_set,
                    progress_callback=lambda value: self._update_highlight_progress(
                        generation, value
                    ),
                    cache_directory=cache_directory,
                )
                if not self._is_current_highlight_run(generation):
                    return
                if cancel_event.is_set():
                    self._highlight_status = "cancelled"
                    self.highlightAnalysisChanged.emit()
                    return
                self._highlight_candidates = [item.to_json() for item in candidates]
                self._highlight_status = "completed"
                self._highlight_progress = 1.0
                self.highlightCandidatesChanged.emit()
                self.highlightAnalysisChanged.emit()
            except Exception as error:
                if not self._is_current_highlight_run(generation):
                    return
                self._highlight_status = "cancelled" if cancel_event.is_set() else "error"
                if not cancel_event.is_set():
                    self._set_status(f"見どころ候補の解析に失敗しました: {error}", "ERROR")
                self.highlightAnalysisChanged.emit()

        threading.Thread(target=worker, name="highlight-analysis", daemon=True).start()
        return True

    @Slot(result=bool)
    def cancelHighlightAnalysis(self) -> bool:
        if self._highlight_status != "running":
            return False
        self._highlight_cancel.set()
        self._highlight_status = "cancelling"
        self.highlightAnalysisChanged.emit()
        return True

    @Slot(result=bool)
    def retryHighlightAnalysis(self) -> bool:
        if self._highlight_status in {"running", "cancelling"}:
            return False
        self._highlight_candidates = []
        self.highlightCandidatesChanged.emit()
        return self.startHighlightAnalysis()

    @Slot(int, result=bool)
    def addHighlightCandidate(self, index: int) -> bool:
        if self._project is None or self._running or not 0 <= index < len(self._highlight_candidates):
            return False
        candidate = self._highlight_candidates[index]
        source_ids = [str(item) for item in candidate.get("source_segment_ids", [])]
        if not source_ids:
            return False
        section = self._short_video_section()
        clips = list(section.get("clips", []))
        candidate_start = float(candidate.get("start", 0.0))
        candidate_end = float(candidate.get("end", candidate_start))
        if any(
            str(clip.get("segment_id", "")) in source_ids
            and min(float(clip.get("end", 0.0)), candidate_end)
            > max(float(clip.get("start", 0.0)), candidate_start)
            for clip in clips
        ):
            self._set_status("同じ区間のショートクリップは追加済みです", "CHECK")
            return False
        section["enabled"] = True
        clips.append(
            {
                "segment_id": source_ids[0],
                "start": candidate_start,
                "end": candidate_end,
                "highlight_candidate_id": str(candidate.get("id", "")),
            }
        )
        section["clips"] = clips
        self._mark_project_dirty()
        self.projectDataChanged.emit()
        self.shortVideoChanged.emit()
        return True

    @Slot(int, result=bool)
    def rejectHighlightCandidate(self, index: int) -> bool:
        if not 0 <= index < len(self._highlight_candidates):
            return False
        self._highlight_rejected.append(self._highlight_candidates.pop(index))
        self.highlightCandidatesChanged.emit()
        return True

    @Slot(result=bool)
    def undoHighlightRejection(self) -> bool:
        if not self._highlight_rejected:
            return False
        self._highlight_candidates.append(self._highlight_rejected.pop())
        self.highlightCandidatesChanged.emit()
        return True

    def _is_current_highlight_run(self, generation: int) -> bool:
        return generation == self._highlight_generation

    def _update_highlight_progress(self, generation: int, value: float) -> None:
        if not self._is_current_highlight_run(generation):
            return
        self._highlight_progress = max(0.0, min(1.0, float(value)))
        self.highlightAnalysisChanged.emit()

    @Slot(int, result="QVariantMap")
    def segmentAt(self, index: int) -> dict[str, Any]:
        segments = self._project.get("segments", []) if self._project else []
        if not 0 <= index < len(segments):
            return {}
        return self._segment_view(segments[index], index)

    @Slot(int, str, result=str)
    def formatSubtitlePreview(self, index: int, text: str) -> str:
        segments = self._project.get("segments", []) if self._project else []
        if not 0 <= index < len(segments):
            return str(text)
        draft = {**segments[index], "text": str(text)}
        return segment_preview_text(draft)

    @Slot(float, result="QVariantList")
    def activeSubtitleSegments(self, seconds: float) -> list[dict[str, Any]]:
        segments = self._project.get("segments", []) if self._project else []
        if not segments:
            return []
        position = max(0.0, float(seconds))
        index = bisect_right(self._segment_starts, position) - 1
        active: list[dict[str, Any]] = []
        while index >= 0 and self._segment_prefix_max_end[index] >= position:
            segment = segments[index]
            if float(segment["end"]) >= position:
                active.append(self._segment_view(segment, index))
            index -= 1
        active.reverse()
        return active

    @Slot(float, float, result="QVariantList")
    def visibleSubtitleSegments(self, start: float, end: float) -> list[dict[str, Any]]:
        segments = self._project.get("segments", []) if self._project else []
        if not segments:
            return []
        viewport_start = max(0.0, float(start))
        viewport_end = max(viewport_start, float(end))
        first = bisect_left(self._segment_starts, viewport_start)
        while first > 0 and self._segment_prefix_max_end[first - 1] >= viewport_start:
            first -= 1
        visible: list[dict[str, Any]] = []
        for index in range(first, len(segments)):
            segment = segments[index]
            if float(segment["start"]) > viewport_end:
                break
            if float(segment["end"]) >= viewport_start:
                visible.append(self._segment_view(segment, index))
        return visible

    @Property("QVariantList", notify=projectDataChanged)
    def projectSpeakers(self) -> list[dict[str, Any]]:
        if self._project is None:
            return []
        return deepcopy(self._project.get("speakers", []))

    @Property("QVariantList", notify=projectDataChanged)
    def subtitleWaveforms(self) -> list[dict[str, Any]]:
        if self._project is None:
            return []
        return deepcopy(self._project.get("waveforms", []))

    @Property(bool, notify=audioPreviewCacheChanged)
    def audioPreviewPreparing(self) -> bool:
        return self._audio_preview_preparing

    @Property(int, notify=audioPreviewCacheChanged)
    def audioPreviewGeneration(self) -> int:
        return self._audio_preview_generation

    @Property(str, notify=audioPreviewCacheChanged)
    def audioPreviewCacheSummary(self) -> str:
        return self._audio_preview_controller.audio_preview_cache_summary

    @Property(str, notify=audioMixerPreviewChannelsChanged)
    def audioPreviewClockUrl(self) -> str:
        return self._audio_preview_controller.audio_preview_clock_url

    def _reset_audio_preview_cache(self) -> None:
        self._audio_preview_controller.reset_cache()

    @Slot()
    def prepareAudioMixerPreview(self) -> None:
        self._audio_preview_controller.prepare_preview(
            ffmpeg_available=self._dependencies.ffmpeg,
        )

    @Slot(int, object)
    def _apply_audio_preview_cache(
        self,
        request_id: int,
        result: AudioPreviewCacheResult,
    ) -> None:
        self._audio_preview_controller.apply_audio_preview_cache(request_id, result)

    @Slot()
    def clearAudioPreviewCache(self) -> None:
        self._audio_preview_controller.clear_cache()

    @Property("QVariantList", notify=projectDataChanged)
    def audioMixerChannels(self) -> list[dict[str, Any]]:
        return self._audio_preview_controller.mixer_channels

    @Property(bool, notify=projectDataChanged)
    def audioMixerAvailable(self) -> bool:
        return bool(self.audioMixerChannels)

    def _enabled_audio_mixer_channel_ids(self) -> set[str]:
        return self._audio_preview_controller.enabled_channel_ids()

    @Property(bool, notify=projectDataChanged)
    def audioMixerPreviewComplete(self) -> bool:
        return self._audio_preview_controller.preview_complete

    @Property(bool, notify=projectDataChanged)
    def audioMixerIntentionalSilence(self) -> bool:
        return self._audio_preview_controller.intentional_silence

    @Property("QVariantList", notify=projectDataChanged)
    def audioMixerSequenceChannels(self) -> list[dict[str, Any]]:
        if self._project is None:
            return []

        active_ids = {
            str(channel.get("id", ""))
            for channel in active_audio_mix_channels(self._project.get("audio_mix", {}))
        }
        waveforms_by_path = {
            str(Path(str(waveform.get("source_path", ""))).resolve()).casefold(): waveform
            for waveform in self._project.get("waveforms", [])
            if isinstance(waveform, dict) and waveform.get("source_path")
        }
        duration = max(
            0.0,
            float(self._project.get("video", {}).get("duration_seconds", 0.0)),
        )
        colors = ("#6FA8DC", "#93C47D", "#F6B26B", "#E78284", "#81C8BE")
        sequence: list[dict[str, Any]] = []
        for index, channel in enumerate(self._project.get("audio_mix", {}).get("channels", [])):
            if not isinstance(channel, dict) or not bool(channel.get("enabled")):
                continue
            view = self._audio_mixer_channel_view(channel)
            waveform = None
            if view.get("kind") == "external" and view.get("path"):
                waveform = waveforms_by_path.get(
                    str(Path(str(view["path"])).resolve()).casefold()
                )
            offset = float(view.get("preview_offset_seconds", 0.0))
            view.update(
                {
                    "lane_id": str(view.get("id", "")),
                    "name": str(view.get("label", "入力")),
                    "color": str((waveform or {}).get("color") or colors[index % len(colors)]),
                    "offset_seconds": float((waveform or {}).get("offset_seconds", offset)),
                    "duration_seconds": float(
                        (waveform or {}).get(
                            "duration_seconds",
                            max(0.0, duration - max(0.0, offset)),
                        )
                    ),
                    "peaks": list((waveform or {}).get("peaks", [])),
                    "audible": str(view.get("id", "")) in active_ids,
                }
            )
            sequence.append(view)
        return sequence

    def _audio_mixer_channel_view(self, channel: dict[str, Any]) -> dict[str, Any]:
        return self._audio_preview_controller.channel_view(channel)

    def _audio_mixer_preview_state(
        self,
    ) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], dict[str, float]]:
        return self._audio_preview_controller.preview_state()

    def _notify_audio_mixer_preview(self, *, structure_changed: bool) -> None:
        self._audio_preview_controller.notify_preview(structure_changed=structure_changed)

    @Property("QVariantList", notify=audioMixerPreviewChannelsChanged)
    def audioMixerPreviewChannels(self) -> list[dict[str, Any]]:
        return self._audio_preview_controller.preview_channels

    @Property("QVariantMap", notify=audioMixerPreviewGainsChanged)
    def audioMixerPreviewGains(self) -> dict[str, float]:
        return self._audio_preview_controller.preview_gains

    def _audio_preview_output(self, channel_id: str) -> QAudioBufferOutput:
        return self._audio_preview_controller.preview_output(channel_id)

    @staticmethod
    def _audio_buffer_peak(buffer: QAudioBuffer) -> float:
        return AudioPreviewController.audio_buffer_peak(buffer)

    def _receive_audio_preview_buffer(self, channel_id: str, buffer: QAudioBuffer) -> None:
        self._audio_preview_controller.receive_preview_buffer(channel_id, buffer)

    def _publish_audio_preview_levels(self) -> None:
        self._audio_preview_controller.publish_levels()

    @Property("QVariantMap", notify=audioPreviewLevelsChanged)
    def audioPreviewLevels(self) -> dict[str, float]:
        return dict(self._audio_preview_levels)

    @Slot(float, float)
    def _update_audio_master_metrics(self, level: float, reduction_db: float) -> None:
        self._audio_preview_controller._update_master_metrics(level, reduction_db)

    @Property(float, notify=audioMasterMetricsChanged)
    def audioMasterLevel(self) -> float:
        return self._audio_preview_controller.master_level

    @Property(float, notify=audioMasterMetricsChanged)
    def audioLimiterReductionDb(self) -> float:
        return self._audio_preview_controller.limiter_reduction_db

    @Slot(int)
    def startAudioMixerPreview(self, position_milliseconds: int) -> None:
        self._audio_preview_controller.start_preview(position_milliseconds)

    @Slot()
    def pauseAudioMixerPreview(self) -> None:
        self._audio_preview_controller.pause_preview()

    @Slot(int, bool)
    def seekAudioMixerPreview(self, position_milliseconds: int, playing: bool) -> None:
        self._audio_preview_controller.seek_preview(position_milliseconds, playing)

    @Slot()
    def stopAudioMixerPreview(self) -> None:
        self._audio_preview_controller.stop_preview()

    @Property(float, notify=segmentsChanged)
    def projectDuration(self) -> float:
        if self._project is None:
            return 0.0
        video_duration = float(self._project.get("video", {}).get("duration_seconds", 0.0))
        segment_duration = max((float(item["end"]) for item in self._project.get("segments", [])), default=0.0)
        return max(video_duration, segment_duration)

    def _sequence_model_for_facade(self) -> VideoSequence | None:
        """Read the sequence through ProjectEditorController only.

        QML never receives the mutable project dictionary.  The controller
        validates the persisted payload and applies all mutations, while this
        facade builds a detached view for presentation.
        """

        if self._project is None or self._project_editor_controller is None:
            return None
        try:
            return self._project_editor_controller.sequence_model()
        except SubtitleProjectError:
            return None

    def _sequence_view_payload(self) -> dict[str, Any]:
        model = self._sequence_model_for_facade()
        if model is None:
            return {
                "schemaVersion": 1,
                "assets": [],
                "clips": [],
                "outputDuration": 0.0,
                "isLegacySingleVideo": False,
            }

        asset_clip_counts = {
            asset.id: sum(clip.asset_id == asset.id for clip in model.clips)
            for asset in model.assets
        }
        assets = [
            {
                "id": asset.id,
                "path": asset.path,
                "name": Path(asset.path).name,
                "duration": asset.duration_seconds,
                "clipCount": asset_clip_counts.get(asset.id, 0),
            }
            for asset in model.assets
        ]
        timeline = model.timeline if model.clips else None
        timeline_clips = timeline.clips if timeline is not None else ()
        assets_by_id = {asset.id: asset for asset in model.assets}
        clips: list[dict[str, Any]] = []
        for entry in timeline_clips:
            clip = entry.clip
            asset = assets_by_id[clip.asset_id]
            view = entry.as_view()
            view.update(
                {
                    "assetPath": asset.path,
                    "assetName": Path(asset.path).name,
                    "audioLinked": clip.audio_linked,
                    "volume": clip.volume,
                    "audioOffset": clip.audio_offset_seconds,
                    "muted": clip.muted,
                }
            )
            clips.append(view)
        return {
            "schemaVersion": model.schema_version,
            "assets": assets,
            "clips": clips,
            "outputDuration": timeline.total_duration if timeline is not None else 0.0,
            "isLegacySingleVideo": model.is_legacy_single_video(),
        }

    def _sequence_failure(self, message: str) -> bool:
        self._sequence_error = str(message)
        self.sequenceChanged.emit()
        self._set_status(self._sequence_error, "CHECK")
        return False

    def _apply_sequence_mutation(
        self,
        mutation: Callable[[VideoSequence], VideoSequence],
        success_message: str,
    ) -> bool:
        if self._running:
            return self._sequence_failure("処理中はsequenceを変更できません")
        if self._project is None or self._project_editor_controller is None:
            return self._sequence_failure("先に編集プロジェクトを開いてください")
        self._sequence_error = ""
        try:
            updated = self._project_editor_controller.apply_sequence_mutation(mutation)
        except (SubtitleProjectError, VideoSequenceError, TypeError, ValueError) as error:
            return self._sequence_failure(f"sequenceを変更できません: {error}")
        if updated is None:
            return self._sequence_failure("sequenceを変更できません")
        self._set_status(success_message, "EDIT")
        return True

    @Property("QVariantMap", notify=sequenceChanged)
    def sequenceView(self) -> dict[str, Any]:
        return deepcopy(self._sequence_view_payload())

    @Property("QVariantList", notify=sequenceChanged)
    def mediaBinAssets(self) -> list[dict[str, Any]]:
        return deepcopy(self._sequence_view_payload()["assets"])

    @Property("QVariantList", notify=sequenceChanged)
    def sequenceClips(self) -> list[dict[str, Any]]:
        return deepcopy(self._sequence_view_payload()["clips"])

    @Property(float, notify=sequenceChanged)
    def sequenceOutputDuration(self) -> float:
        return float(self._sequence_view_payload()["outputDuration"])

    @Property("QVariantMap", notify=sequenceChanged)
    def sequencePlayhead(self) -> dict[str, Any]:
        model = self._sequence_model_for_facade()
        if model is None or not model.clips:
            return {
                "outputMs": 0,
                "outputSeconds": 0.0,
                "clipId": "",
                "sourceTime": 0.0,
            }
        timeline = model.timeline
        output_seconds = min(
            max(0.0, self._sequence_playhead_seconds),
            timeline.total_duration,
        )
        # Keep output-to-source mapping in the #404 domain API.  QML only
        # renders this result and never reconstructs transition overlap.
        position = timeline.output_to_source_seconds(output_seconds)
        return {
            "outputMs": int(round(output_seconds * 1000)),
            "outputSeconds": output_seconds,
            "clipId": position.clip_id,
            "sourceTime": position.source_time,
        }

    @Property(str, notify=sequenceChanged)
    def sequenceError(self) -> str:
        return self._sequence_error

    @Slot(str, result=bool)
    def addSequenceAsset(self, path: str) -> bool:
        if self._running:
            return self._sequence_failure("処理中はsequence素材を変更できません")
        candidate = self._local_path(path)
        try:
            candidate = candidate.expanduser().resolve(strict=True)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            return self._sequence_failure(f"動画素材を確認できません: {error}")
        if not candidate.is_file():
            return self._sequence_failure("動画素材ファイルが存在しません")
        supported, reason = self._is_supported_media_file(
            candidate,
            {"video"},
            "sequence動画素材",
        )
        if not supported:
            return self._sequence_failure(reason or "動画素材として利用できません")
        model = self._sequence_model_for_facade()
        if model is None:
            return self._sequence_failure("sequenceを読み込めません")
        normalized = self._normalized_source_path(str(candidate))
        if any(self._normalized_source_path(asset.path) == normalized for asset in model.assets):
            return self._sequence_failure("同じ動画素材は既にmedia binにあります")
        try:
            duration = float(probe_media_duration(candidate))
        except (OSError, ValueError, TypeError, subprocess.CalledProcessError) as error:
            return self._sequence_failure(f"動画の長さを確認できません: {error}")
        if not math.isfinite(duration) or duration <= 0.0:
            return self._sequence_failure("動画の長さが不明なため追加できません")
        return self._apply_sequence_mutation(
            lambda sequence: sequence.add_asset(str(candidate), duration),
            f"media binへ動画を追加しました: {candidate.name}",
        )

    @Slot("QVariantList", result=int)
    def addSequenceAssets(self, paths: list[Any]) -> int:
        added = 0
        for path in paths or []:
            if self.addSequenceAsset(path):
                added += 1
        return added

    @Slot(result=str)
    def browseSequenceAsset(self) -> str:
        if self._running:
            self._sequence_failure("処理中はsequence素材を変更できません")
            return ""
        start_dir = str(self.workspace_root)
        model = self._sequence_model_for_facade()
        if model and model.assets:
            start_dir = str(Path(model.assets[-1].path).parent)
        path, _ = QFileDialog.getOpenFileName(
            None,
            "sequenceへ追加する動画を選択",
            start_dir,
            "Video files (*.avi *.m2ts *.mkv *.mov *.mp4 *.mpeg *.mpg *.ts *.webm *.wmv);;All files (*)",
        )
        if path:
            self.addSequenceAsset(path)
        return path

    @Slot(str, result=bool)
    def addSequenceClip(self, asset_id: str) -> bool:
        return self.insertSequenceClip(asset_id, len(self.sequenceClips))

    @Slot(str, int, result=bool)
    def insertSequenceClip(self, asset_id: str, index: int) -> bool:
        model = self._sequence_model_for_facade()
        if model is None:
            return self._sequence_failure("sequenceを読み込めません")
        try:
            asset = next(asset for asset in model.assets if asset.id == str(asset_id))
        except StopIteration:
            return self._sequence_failure("指定されたsequence素材が見つかりません")
        if asset.duration_seconds <= 0.0:
            return self._sequence_failure("動画の長さが不明なためclipを追加できません")
        return self._apply_sequence_mutation(
            lambda sequence: sequence.add_clip(
                asset.id,
                0.0,
                asset.duration_seconds,
                index=index,
            ),
            f"sequenceへclipを追加しました: {asset.path}",
        )

    @Slot(str, int, result=bool)
    def moveSequenceClip(self, clip_id: str, index: int) -> bool:
        return self._apply_sequence_mutation(
            lambda sequence: sequence.reorder_clip(clip_id, index),
            "sequenceの順序を変更しました",
        )

    @Slot(str, result=bool)
    def removeSequenceClip(self, clip_id: str) -> bool:
        return self._apply_sequence_mutation(
            lambda sequence: sequence.remove_clip(clip_id),
            "sequenceからclipを削除しました",
        )

    @Slot(str, float, float, result=bool)
    def trimSequenceClip(self, clip_id: str, source_start: float, source_end: float) -> bool:
        return self._apply_sequence_mutation(
            lambda sequence: sequence.trim_clip(clip_id, source_start, source_end),
            "clipの範囲を更新しました",
        )

    @Slot(str, str, float, result=bool)
    def setSequenceTransition(self, clip_id: str, transition_type: str, duration: float) -> bool:
        return self._apply_sequence_mutation(
            lambda sequence: sequence.set_transition(clip_id, transition_type, duration),
            "clipの切り替えを更新しました",
        )

    @Slot(str, bool, float, float, bool, result=bool)
    def setSequenceClipAudio(
        self,
        clip_id: str,
        audio_linked: bool,
        volume: float,
        audio_offset_seconds: float,
        muted: bool,
    ) -> bool:
        return self._apply_sequence_mutation(
            lambda sequence: sequence.set_clip_audio(
                clip_id,
                audio_linked=audio_linked,
                volume=volume,
                audio_offset_seconds=audio_offset_seconds,
                muted=muted,
            ),
            "clipの音声設定を更新しました",
        )

    @Slot(int, result=bool)
    def setSequencePlayhead(self, output_milliseconds: int) -> bool:
        model = self._sequence_model_for_facade()
        if model is None or not model.clips:
            return self._sequence_failure("sequenceに再生可能なclipがありません")
        try:
            requested = float(output_milliseconds) / 1000.0
        except (TypeError, ValueError):
            return self._sequence_failure("再生位置が不正です")
        if not math.isfinite(requested):
            return self._sequence_failure("再生位置が不正です")
        self._sequence_playhead_seconds = min(max(0.0, requested), model.output_duration)
        self._sequence_error = ""
        self.sequenceChanged.emit()
        return True

    @Property(bool, notify=historyChanged)
    def canUndo(self) -> bool:
        return bool(self._undo_stack)

    @Property(bool, notify=historyChanged)
    def canRedo(self) -> bool:
        return bool(self._redo_stack)

    @Property(int, notify=selectionChanged)
    def selectedSegmentIndex(self) -> int:
        return self._selected_segment_index

    @Property(str, notify=activeJobChanged)
    def activeJob(self) -> str:
        return self._active_job

    @Property("QVariantList", notify=progressDetailsChanged)
    def progressSteps(self) -> list[dict[str, Any]]:
        return self._processing_progress.as_list()

    @Property(int, notify=progressDetailsChanged)
    def progressPercent(self) -> int:
        return int(round(self._processing_progress.value * 100))

    @Property(str, notify=progressDetailsChanged)
    def progressCurrentStep(self) -> str:
        return self._processing_progress.current_step

    @Property(str, notify=progressDetailsChanged)
    def progressCurrentStepDisplay(self) -> str:
        for step in self._processing_progress.as_list():
            if step["id"] == self._processing_progress.current_step:
                return str(step["label"])
        return ""

    @Property(str, notify=progressDetailsChanged)
    def progressState(self) -> str:
        return self._processing_progress.status

    @Property(bool, notify=progressDetailsChanged)
    def progressVisible(self) -> bool:
        return bool(self._processing_progress.steps)

    @Property(str, notify=assPathChanged)
    def assPath(self) -> str:
        return self._ass_path

    def _apply_project_speaker_color(self, index: int, color: str) -> bool:
        if self._project is None or not 0 <= index < len(self._project.get("speakers", [])):
            return False
        current = self._project["speakers"][index]
        if str(current.get("color", "")).upper() == color:
            return False
        updated = {**current, "color": color}
        self._project["speakers"][index] = updated
        style = str(updated.get("style", ""))
        name = str(updated.get("name", ""))
        for waveform in self._project.get("waveforms", []):
            if waveform.get("style") == style or waveform.get("speaker") == name:
                waveform["color"] = color

        source_changed = False
        for source_index, source in enumerate(self._speakers):
            if (
                source.get("path") == updated.get("path")
                or source.get("file_name") == updated.get("file_name")
                or source.get("name") == name
            ):
                self._speakers[source_index] = {**source, "color": color}
                source_changed = True
        if source_changed:
            self.speakersChanged.emit()
        self.projectDataChanged.emit()
        self._mark_project_dirty()
        return True

    def _source_speaker_color_updated(self, speaker: dict[str, str]) -> None:
        if self._project is None:
            return
        index = next((
            index
            for index, project_speaker in enumerate(self._project.get("speakers", []))
            if (
                project_speaker.get("path") == speaker.get("path")
                or project_speaker.get("file_name") == speaker.get("file_name")
                or project_speaker.get("name") == speaker.get("name")
            )
        ), -1)
        if index >= 0:
            self._apply_project_speaker_color(index, str(speaker["color"]))

    @Slot(int, str)
    def updateProjectSpeakerColor(self, index: int, color: str) -> None:
        if self._running or self._project is None or not 0 <= index < len(self._project.get("speakers", [])):
            return
        speaker = self._project["speakers"][index]
        try:
            normalized = normalize_rgb_color(color)
            save_speaker_color(
                self.color_config_path,
                file_name=str(speaker.get("file_name", "")),
                speaker_name=str(speaker.get("name", "")),
                color=normalized,
            )
        except (OSError, ValueError, TypeError) as error:
            self._set_status(f"話者色を保存できません: {error}", "ERROR")
            return
        self._apply_project_speaker_color(index, normalized)
        self._set_status(f"{speaker.get('name', '話者')} の字幕色を保存しました", "SAVED")

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
        if self._running:
            return ""
        start_dir = self._source_selection.output_dir or str(self.workspace_root)
        path, _ = QFileDialog.getOpenFileName(
            None,
            "BGM ファイルを選択",
            start_dir,
            "Audio files (*.mp3 *.wav *.m4a *.aac *.ogg *.flac);;All files (*.*)",
        )
        if path:
            self.setShortVideoBgm({"path": path})
        return path

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
        tracks = [
            {"selector": str(item.get("selector", "")), "label": str(item.get("label", ""))}
            for item in self._audio_tracks
            if str(item.get("selector", "")).strip()
        ]
        return tracks

    def _fallback_video_tracks(self) -> list[dict[str, str]]:
        return [{"selector": DEFAULT_AUDIO_TRACK, "label": "既定の動画音声"}]

    @Slot(int, "QVariantMap")
    def updateAudioMixChannel(self, index: int, changes: dict[str, Any]) -> None:
        if self._project is None or self._running:
            return
        audio_mix = reconcile_audio_mix(self._project, self._mixer_video_tracks())
        channels = audio_mix["channels"]
        if not 0 <= index < len(channels):
            self._set_status("動画内または外部の音声トラックがありません", "CHECK")
            return
        channel = channels[index]
        channel_id = str(channel.get("id", ""))
        enabled_before = bool(channel.get("enabled"))
        try:
            updated_audio_mix = update_audio_mix_channel(audio_mix, channel_id, changes)
        except AudioMixError:
            self._set_status("音量ミキサーの変更内容を確認してください", "CHECK")
            return
        self._project["audio_mix"] = updated_audio_mix
        self.projectDataChanged.emit()
        self._notify_audio_mixer_preview(
            structure_changed=enabled_before
            != bool(updated_audio_mix["channels"][index].get("enabled"))
        )
        self._mark_project_dirty()
        self._set_status("音量ミキサー設定を更新しました", "EDIT")

    def start_codex_audio_mix_proposal(
        self,
        *,
        intent: str,
        revision: int,
        context: Mapping[str, Any] | None = None,
    ) -> bool:
        if self._project is None or self._running:
            self._set_status("音量ミキサーの変更案には編集プロジェクトが必要です", "CHECK")
            return False
        if revision != self._project_revision:
            self._set_status("音量ミキサーの変更案が古くなっています", "CHECK")
            return False
        if self._codex_session.running or self._codex_audio_mix_session.running:
            self._set_status("別のCodex変更案を処理中です", "BUSY")
            return False
        try:
            channels = self.audioMixerChannels
            proposal_context = dict(context) if context is not None else build_audio_mix_context(
                channels,
                preview_levels=self.audioPreviewLevels,
                master_level=self.audioMasterLevel,
                limiter_reduction_db=self.audioLimiterReductionDb,
                playhead_seconds=float(self.editorPlayhead.get("sourcePositionMs", 0)) / 1000.0,
                project_revision=revision,
            )
            if proposal_context.get("project_revision") != revision:
                raise AudioMixProposalError("audio mix context is stale")
            prompt = build_audio_mix_proposal_prompt(intent)
            self._audio_mix_proposal = None
            self.audioMixProposalChanged.emit()
            self._codex_audio_mix_session.start(
                prompt=prompt,
                context=proposal_context,
                output_schema=AUDIO_MIX_PROPOSAL_OUTPUT_SCHEMA,
                revision=revision,
            )
        except (AudioMixProposalError, CodexSessionError, ValueError) as error:
            self._set_status(f"音量ミキサーの変更案を開始できません: {error}", "ERROR")
            return False
        self._set_status("Codexへ音量ミキサーの変更案を依頼しています", "CODEX")
        return True

    @Slot(str, result=bool)
    def proposeAudioMix(self, intent: str) -> bool:
        return self.start_codex_audio_mix_proposal(intent=intent, revision=self._project_revision)

    @Slot()
    def stopCodexAudioMixProposal(self) -> None:
        was_running = self._codex_audio_mix_session.running
        self._codex_audio_mix_session.stop()
        if was_running:
            self._codex_chat.fail_proposal("", cancelled=True)
        self._set_status("音量ミキサーの変更案を停止しました", "CODEX")

    @Slot("QVariantList", bool, result=bool)
    def applyAudioMixProposal(
        self,
        selected_operation_ids: list[Any] | None = None,
        allow_silence: bool = False,
    ) -> bool:
        if self._project is None or not self._audio_mix_proposal:
            self._set_status("適用する音量ミキサーの変更案がありません", "CHECK")
            return False
        if self._codex_audio_mix_session.running:
            self._set_status("音量ミキサーの変更案を生成中です", "BUSY")
            return False
        before = deepcopy(self._project.get("audio_mix", {}))
        try:
            updated, _changed_ids = apply_audio_mix_proposal(
                before,
                self._audio_mix_proposal,
                current_revision=self._project_revision,
                selected_operation_ids=(
                    None
                    if selected_operation_ids is None
                    else {str(item) for item in selected_operation_ids}
                ),
                allow_silence=bool(allow_silence),
            )
        except (AudioMixProposalError, AudioMixError, ValueError, TypeError) as error:
            self._set_status(f"音量ミキサーの変更案を適用できません: {error}", "ERROR")
            return False
        if updated == before:
            self._set_status("音量ミキサーの変更はありません", "CHECK")
            return False
        self._project["audio_mix"] = updated
        self._push_history({"kind": "audio_mix", "before": before, "after": deepcopy(updated)})
        self.projectDataChanged.emit()
        self._notify_audio_mixer_preview(structure_changed=True)
        self._mark_project_dirty()
        self._audio_mix_proposal = None
        self.audioMixProposalChanged.emit()
        self._set_status("音量ミキサーの変更案を適用しました。内容を確認して保存してください", "EDIT")
        return True

    @Slot()
    def discardAudioMixProposal(self) -> None:
        self._audio_mix_proposal = None
        self.audioMixProposalChanged.emit()
        self._set_status("音量ミキサーの変更案を破棄しました", "EDIT")

    @Slot()
    def resetAudioMixer(self) -> None:
        if self._project is None or self._running:
            return
        audio_mix = reset_audio_mix(self._project, self._mixer_video_tracks())
        if not audio_mix["channels"]:
            self._set_status("動画内または外部の音声トラックがありません", "CHECK")
            return
        self.projectDataChanged.emit()
        self._notify_audio_mixer_preview(structure_changed=True)
        self._mark_project_dirty()
        self._set_status("音量ミキサーを既定値へ戻しました", "EDIT")

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
        if self._running:
            return
        selected_mode = str(mode or "").strip().lower()
        if selected_mode not in {"replace", "merge"}:
            self._set_status("文字起こし結果の取り込み方法を選択してください", "CHECK")
            return
        if self._project is None:
            self._reset_transcription_integration_state()
            self.startTranscription(settings, False)
            return
        if not self.saveProject():
            return
        self._transcription_merge_mode = selected_mode
        self._transcription_preserved_project = deepcopy(self._project)
        self._transcription_preserved_project_path = self._project_path
        self._transcription_preserved_segments = (
            deepcopy(self._project.get("segments", []))
            if selected_mode == "merge"
            else []
        )
        default_project_path = self._default_project_path()
        if default_project_path is None:
            self._reset_transcription_integration_state()
            self._set_status("文字起こし結果の保存先を決定できません", "ERROR")
            return
        generated_project_path = project_work_directory(default_project_path) / (
            f".{default_project_path.stem}.{uuid4().hex}.subtitle-project.json"
        )
        self.startTranscription(settings, True, str(generated_project_path))

    def _merge_preserved_transcription_segments(self) -> bool:
        if self._project is None or self._transcription_preserved_project is None:
            return False
        generated = deepcopy(self._project)
        preserved = deepcopy(self._transcription_preserved_project)
        generated_segments = deepcopy(generated.get("segments", []))
        if self._transcription_merge_mode == "merge":
            preserved_segments = deepcopy(preserved.get("segments", []))
            used_ids = {str(item.get("id", "")) for item in preserved_segments}
            merged = list(preserved_segments)
            for segment in generated_segments:
                segment_id = str(segment.get("id", ""))
                if not segment_id or segment_id in used_ids:
                    segment["id"] = f"transcribed-{uuid4().hex[:12]}"
                used_ids.add(str(segment["id"]))
                merged.append(segment)
            segments = merged
        elif self._transcription_merge_mode == "replace":
            segments = generated_segments
        else:
            return False

        preserved["segments"] = assign_project_layout_rows(
            sorted(segments, key=lambda item: (item["start"], item["end"], item["id"]))
        )
        for key in ("transcription", "transcription_context", "waveforms"):
            if key in generated:
                preserved[key] = deepcopy(generated[key])
        self._project = preserved
        preserved_project_path = self._transcription_preserved_project_path or self._project_path
        self._project_path = preserved_project_path
        self._apply_project_subtitle_settings(self._project)
        self._selected_segment_index = 0 if self._project["segments"] else -1
        self._project_editor_controller.save(preserved_project_path, emit=False)
        self._project_dirty = False
        self._sync_project_timeline()
        self._sync_subtitle_model()
        self.projectChanged.emit()
        self.projectDataChanged.emit()
        self.segmentsChanged.emit()
        self.selectionChanged.emit()
        return True

    def _restore_preserved_transcription_project(self) -> None:
        if self._transcription_preserved_project is None:
            return
        self._project = deepcopy(self._transcription_preserved_project)
        self._project_path = self._transcription_preserved_project_path
        self._apply_project_subtitle_settings(self._project)
        self._project_editor_controller.save(self._project_path, emit=False)
        self._project_dirty = False
        self._selected_segment_index = 0 if self._project.get("segments") else -1
        self._sync_subtitle_model()
        self._sync_project_timeline()
        self.projectChanged.emit()
        self.projectDataChanged.emit()
        self.segmentsChanged.emit()
        self.selectionChanged.emit()

    def _cleanup_transcription_project_artifact(self) -> None:
        if not self._transcription_generated_project_path:
            return
        try:
            Path(self._transcription_generated_project_path).unlink(missing_ok=True)
        except OSError as error:
            self._record_log(
                f"一時文字起こしプロジェクトを削除できません: {error}",
                severity="WARNING",
                component="gui",
                job="transcribe",
                stage="CLEANUP",
            )
        finally:
            self._transcription_generated_project_path = ""

    def _reset_transcription_integration_state(self) -> None:
        self._cleanup_transcription_project_artifact()
        self._transcription_merge_mode = ""
        self._transcription_preserved_segments = []
        self._transcription_preserved_project = None
        self._transcription_preserved_project_path = ""

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
        self._project_editor_controller.record_history(before, after, reflow_layout)

    def _record_timeline_history(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> None:
        self._project_editor_controller.record_timeline_history(before, after)

    def _push_history(self, entry: dict[str, Any]) -> None:
        self._project_editor_controller.push_history(entry)

    def _mark_project_dirty(self) -> None:
        self._project_editor_controller.mark_dirty()

    def _replace_segments(
        self,
        segments: list[dict[str, Any]],
        selected_id: str | None = None,
        *,
        reflow_layout: bool = True,
    ) -> None:
        self._project_editor_controller.replace_segments(
            segments,
            selected_id,
            reflow_layout=reflow_layout,
        )

    def _commit_segment_change(
        self,
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
        selected_id: str | None = None,
        *,
        reflow_layout: bool = True,
    ) -> None:
        self._project_editor_controller.commit_segment_change(
            before,
            after,
            selected_id,
            reflow_layout=reflow_layout,
        )

    def _apply_history_entry(self, entry: dict[str, Any], state: str) -> None:
        self._project_editor_controller.apply_history_entry(entry, state)

    def _replace_timeline(self, payload: dict[str, Any]) -> None:
        source_duration = self._cut_timeline_model().source_duration
        timeline = VideoTimeline.from_json(
            payload,
            source_duration=source_duration,
        )
        self._project_editor_controller.replace_timeline(timeline.to_json())
        self.set_editor_time_mapping(timeline)
        self.cutTimelineChanged.emit()

    def _commit_timeline(self, timeline: VideoTimeline, status: str) -> bool:
        if self._project is None:
            return False
        before = deepcopy(self._project.get("timeline", {}))
        after = timeline.to_json()
        if before == after:
            return False
        self._record_timeline_history(before, after)
        self._replace_timeline(after)
        self._set_status(status, "EDIT")
        return True

    @Slot(float, float, result=bool)
    def addCut(self, source_start: float, source_end: float) -> bool:
        if self._project is None or self._running:
            return False
        try:
            timeline = self._cut_timeline_model().add_cut(source_start, source_end)
        except VideoTimelineError as error:
            self._set_status(f"カット範囲を追加できません: {error}", "CHECK")
            return False
        return self._commit_timeline(timeline, "カット範囲を追加しました")

    @Slot(str, float, float, result=bool)
    def updateCutRange(self, cut_id: str, source_start: float, source_end: float) -> bool:
        if self._project is None or self._running:
            return False
        try:
            timeline = self._cut_timeline_model().update_cut(
                str(cut_id),
                source_start,
                source_end,
            )
        except VideoTimelineError as error:
            self._set_status(f"カット範囲を変更できません: {error}", "CHECK")
            return False
        return self._commit_timeline(timeline, "カット範囲を変更しました")

    @Slot(str, result=bool)
    def restoreCut(self, cut_id: str) -> bool:
        if self._project is None or self._running:
            return False
        try:
            timeline = self._cut_timeline_model().restore_cut(str(cut_id))
        except VideoTimelineError as error:
            self._set_status(f"カット範囲を復元できません: {error}", "CHECK")
            return False
        return self._commit_timeline(timeline, "カット範囲を復元しました")

    @Slot(float, float, result=bool)
    def restoreRange(self, source_start: float, source_end: float) -> bool:
        if self._project is None or self._running:
            return False
        try:
            timeline = self._cut_timeline_model().restore_range(source_start, source_end)
        except VideoTimelineError as error:
            self._set_status(f"範囲を復元できません: {error}", "CHECK")
            return False
        return self._commit_timeline(timeline, "選択範囲を復元しました")

    @Slot(result=bool)
    def clearCuts(self) -> bool:
        if self._project is None or self._running:
            return False
        return self._commit_timeline(
            self._cut_timeline_model().clear_cuts(),
            "すべてのカットを解除しました",
        )

    @Slot(int)
    def selectSegment(self, index: int) -> None:
        self._project_editor_controller.select_segment(index)

    @Slot(float, result=int)
    def segmentIndexAtTime(self, seconds: float) -> int:
        segments = self._project.get("segments", []) if self._project else []
        if not segments:
            return -1
        position = max(0.0, float(seconds))
        index = bisect_right(self._segment_starts, position) - 1
        while index >= 0 and self._segment_prefix_max_end[index] >= position:
            segment = segments[index]
            if float(segment["start"]) <= position <= float(segment["end"]):
                return index
            index -= 1
        return -1

    @Slot(float)
    def selectSegmentAtTime(self, seconds: float) -> None:
        index = self.segmentIndexAtTime(seconds)
        if index >= 0:
            self.selectSegment(index)

    def _edit_number(self, value: Any, label: str) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            self._set_status(f"{label}には数値を入力してください", "CHECK")
            return None
        if not math.isfinite(number):
            self._set_status(f"{label}には有限の数値を入力してください", "CHECK")
            return None
        return number

    @Slot(int, "QVariantMap")
    def updateSegment(self, index: int, changes: dict[str, Any]) -> None:
        if self._project is None or not 0 <= index < len(self._project["segments"]):
            return
        current = self._project["segments"][index]
        updated = deepcopy(current)
        reflow_layout = False
        if "text" in changes:
            updated["text"] = str(changes["text"]).strip()
            updated["manual_text"] = True
            updated.pop("words", None)
            reflow_layout = True
        if "start" in changes or "end" in changes:
            start_value = self._edit_number(changes.get("start", updated["start"]), "開始時刻")
            end_value = self._edit_number(changes.get("end", updated["end"]), "終了時刻")
            if start_value is None or end_value is None:
                return
            start = max(0.0, start_value)
            end = end_value
            if end < start + MIN_SEGMENT_DURATION_SECONDS:
                end = start + MIN_SEGMENT_DURATION_SECONDS
            updated["start"] = round(start, 3)
            updated["end"] = round(end, 3)
            updated["manual_timing"] = True
            updated.pop("words", None)
            reflow_layout = True
        if "speaker" in changes:
            style = str(changes["speaker"])
            updated["speaker"] = style
            updated["manual_speaker"] = True
            speaker = next((item for item in self._project.get("speakers", []) if item.get("style") == style), None)
            if speaker:
                updated["source_speaker"] = speaker.get("name", "")
                updated["source_file"] = speaker.get("file_name", "")
                updated["source_track"] = speaker.get("track_key", "")
        if "subtitle_font_scale" in changes:
            font_scale = self._edit_number(changes["subtitle_font_scale"], "文字サイズ倍率")
            if font_scale is None:
                return
            updated["subtitle_font_scale"] = max(0.1, min(4.0, font_scale))
            updated["manual_font_scale"] = True
        if "subtitle_font_family" in changes:
            font_family = str(changes["subtitle_font_family"]).strip()
            updated["subtitle_font_family"] = font_family
            updated["manual_font_family"] = bool(font_family)
        if updated == current:
            return
        selected_id = current["id"]
        self._commit_segment_change(
            [current],
            [normalize_segment(updated, index)],
            selected_id,
            reflow_layout=reflow_layout,
        )

    def _snap_time(self, value: float, moving_index: int, grid_seconds: float) -> float:
        snapped = max(0.0, value)
        if grid_seconds > 0:
            snapped = round(snapped / grid_seconds) * grid_seconds
        tolerance = max(0.04, grid_seconds * 0.65)
        if self._project is not None:
            edges = [
                float(edge)
                for index, segment in enumerate(self._project["segments"])
                if index != moving_index
                for edge in (segment["start"], segment["end"])
            ]
            if edges:
                nearest = min(edges, key=lambda edge: abs(edge - value))
                if abs(nearest - value) <= tolerance:
                    snapped = nearest
        return round(max(0.0, snapped), 3)

    @Slot(int, float, float, float)
    def moveSegment(self, index: int, start: float, end: float, snap_seconds: float) -> None:
        if self._project is None or not 0 <= index < len(self._project["segments"]):
            return
        duration = max(MIN_SEGMENT_DURATION_SECONDS, end - start)
        snapped_start = self._snap_time(start, index, max(0.0, snap_seconds))
        snapped_end = snapped_start + duration
        self.updateSegment(index, {"start": snapped_start, "end": snapped_end})

    @Slot(int, float, float)
    def resizeSegmentStart(self, index: int, start: float, snap_seconds: float) -> None:
        if self._project is None or not 0 <= index < len(self._project["segments"]):
            return
        segment = self._project["segments"][index]
        snapped = self._snap_time(start, index, max(0.0, snap_seconds))
        self.updateSegment(index, {"start": min(snapped, float(segment["end"]) - MIN_SEGMENT_DURATION_SECONDS)})

    @Slot(int, float, float)
    def resizeSegmentEnd(self, index: int, end: float, snap_seconds: float) -> None:
        if self._project is None or not 0 <= index < len(self._project["segments"]):
            return
        segment = self._project["segments"][index]
        snapped = self._snap_time(end, index, max(0.0, snap_seconds))
        self.updateSegment(index, {"end": max(snapped, float(segment["start"]) + MIN_SEGMENT_DURATION_SECONDS)})

    @Slot(float)
    def addSegment(self, at_seconds: float) -> None:
        if self._project is None:
            return
        speakers = self._project.get("speakers", [])
        speaker = speakers[0] if speakers else {"style": "Oz", "name": "", "track_key": "", "file_name": ""}
        start = max(0.0, float(at_seconds))
        segment = normalize_segment(
            {
                "id": f"subtitle-{uuid4().hex[:12]}",
                "start": start,
                "end": start + 2.0,
                "text": "新しい字幕",
                "speaker": speaker.get("style", "Oz"),
                "source_speaker": speaker.get("name", ""),
                "source_track": speaker.get("track_key", ""),
                "source_file": speaker.get("file_name", ""),
                "manual_text": True,
                "manual_timing": True,
            },
            len(self._project["segments"]),
        )
        self._commit_segment_change([], [segment], segment["id"])

    @Slot()
    def deleteSelectedSegment(self) -> None:
        if self._project is None or not 0 <= self._selected_segment_index < len(self._project["segments"]):
            return
        self._commit_segment_change([self._project["segments"][self._selected_segment_index]], [])

    @Slot(float)
    def splitSelectedSegment(self, at_seconds: float) -> None:
        if self._project is None or not 0 <= self._selected_segment_index < len(self._project["segments"]):
            return
        index = self._selected_segment_index
        segment = deepcopy(self._project["segments"][index])
        split_at = float(at_seconds)
        if not float(segment["start"]) + MIN_SEGMENT_DURATION_SECONDS < split_at < float(segment["end"]) - MIN_SEGMENT_DURATION_SECONDS:
            self._set_status("再生位置を選択字幕の途中へ移動してください", "CHECK")
            return
        text = str(segment.get("text", ""))
        midpoint = max(1, min(len(text) - 1, round(len(text) * (split_at - segment["start"]) / (segment["end"] - segment["start"])))) if len(text) > 1 else len(text)
        first = {**segment, "end": split_at, "text": text[:midpoint].strip(), "manual_text": True, "manual_timing": True}
        second = {**segment, "id": f"subtitle-{uuid4().hex[:12]}", "start": split_at, "text": text[midpoint:].strip(), "manual_text": True, "manual_timing": True}
        first.pop("words", None)
        second.pop("words", None)
        self._commit_segment_change(
            [segment],
            [normalize_segment(first, index), normalize_segment(second, index + 1)],
            second["id"],
        )

    @Slot()
    def undoSubtitleEdit(self) -> None:
        self.undoEdit()

    @Slot()
    def undoCutEdit(self) -> None:
        self.undoEdit()

    @Slot()
    def undoEdit(self) -> None:
        self._project_editor_controller.undo()

    @Slot()
    def redoSubtitleEdit(self) -> None:
        self.redoEdit()

    @Slot()
    def redoCutEdit(self) -> None:
        self.redoEdit()

    @Slot()
    def redoEdit(self) -> None:
        self._project_editor_controller.redo()

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
        if self._project is None:
            return
        self._update_project_settings(settings)
        if not self.saveProject():
            return
        try:
            output = build_project_ass(self._project_path)
        except (OSError, ValueError) as error:
            self._set_status(f"自動保存に失敗しました: {error}", "ERROR")
            return
        self._ass_path = str(output.resolve())
        self.assPathChanged.emit()
        self._set_status(f"ASSプレビューを生成しました: {output.name}", "ASS")

    def _start_command(self, command: list[str], job: str, status: str) -> None:
        self._active_job = job
        self.activeJobChanged.emit()
        skip_steps: set[str] = set()
        if job == "render" and self._project is not None:
            segments = self._project.get("segments", ())
            if not isinstance(segments, (list, tuple)) or not segments:
                skip_steps.add("subtitle")
        self._processing_progress.start(job, skip_steps=skip_steps)
        self.progressDetailsChanged.emit()
        if self._last_process_diagnostic is not None:
            self._last_process_diagnostic = None
            self.lastProcessDiagnosticChanged.emit()
        self._pending_process_error = ""
        self._process_output_tail = ""
        self._record_log(
            f"> {subprocess.list2cmdline(command)}",
            component="gui",
            job=job,
            stage="STARTING",
        )
        self._progress = self._processing_progress.value if self._processing_progress.steps else 0.02
        self.progressChanged.emit()
        self._ffmpeg_duration_seconds = 0.0
        self._ffmpeg_duration_from_event = False
        self._processing_machine_event_seen = False
        self._elapsed_seconds = 0
        self._cancel_requested = False
        self.elapsedChanged.emit()
        self._set_status(status, "STARTING")
        self._start_process(command)

    def _has_audio_source(self, audio_files: list[str], audio_tracks: list[dict[str, Any]] | None = None) -> bool:
        if audio_files:
            return True
        tracks = audio_tracks if audio_tracks is not None else self._audio_tracks
        for track in tracks:
            if str(track.get("selector", "")).strip():
                return True
        return False

    def _default_video_audio_track(self, audio_tracks: list[dict[str, Any]] | None = None) -> str:
        tracks = audio_tracks if audio_tracks is not None else self._audio_tracks
        for track in tracks:
            selector = str(track.get("selector", "")).strip()
            if selector:
                return selector
        return ""

    def _transcription_capability(self, device: str) -> ActionCapability:
        return transcription_capability(
            self._dependencies,
            device=device,
            has_video=Path(self._source_selection.video).is_file(),
            has_audio=self._has_audio_source([speaker["path"] for speaker in self._speakers]),
            project_path=self.projectSavePath,
            running=self._running,
        )

    @Property("QVariantMap", notify=actionCapabilitiesChanged)
    def actionCapabilities(self) -> dict[str, Any]:
        return self.actionCapabilitiesForDevice(str(self._settings.get("device", "cuda")))

    @Slot(str, result="QVariantMap")
    def actionCapabilitiesForDevice(self, device: str) -> dict[str, Any]:
        transcribe = self._transcription_capability(device)
        normal = render_capability(
            self._dependencies, self._project, self._project_path, running=self._running,
        )
        short = render_capability(
            self._dependencies, self._project, self._project_path, short=True, running=self._running,
        )
        needs_output = {}
        for artifact, is_short in (("normal", False), ("short", True)):
            needs_output[artifact] = not self.videoOutputDirectory and render_capability(
                self._dependencies, self._project, self._project_path, short=is_short,
                running=self._running, require_output=False,
            ).enabled
        return {
            "canTranscribe": transcribe.enabled,
            "transcriptionReason": transcribe.reason,
            "canRenderNormal": normal.enabled,
            "normalRenderReason": normal.reason,
            "canRenderShort": short.enabled,
            "shortRenderReason": short.reason,
            "normalRenderNeedsOutput": needs_output["normal"],
            "shortRenderNeedsOutput": needs_output["short"],
            "canUseTranscriptionCuda": self._dependencies.cuda,
            "canUseNvenc": self._dependencies.nvenc,
        }

    @Slot("QVariantMap", bool)
    def startTranscription(
        self,
        settings: dict[str, Any],
        overwrite_project: bool = False,
        project_path: str | None = None,
    ) -> None:
        if self._running:
            return
        if project_path is None:
            self._reset_transcription_integration_state()

        def reject_start(message: str, stage: str) -> None:
            self._set_status(message, stage)
            if project_path is not None:
                self._reset_transcription_integration_state()

        if not self._dependencies.ready:
            self.refreshDependencies()
        audio_tracks = list(self._audio_tracks)
        device = str(settings.get("device") or self._settings.get("device"))
        capability = self._transcription_capability(device)
        if not capability.enabled:
            setup_missing = not self._dependencies.ready or (device == "cuda" and not self._dependencies.cuda)
            reject_start(capability.reason, "SETUP" if setup_missing else "CHECK")
            return
        selection = self._source_selection
        audio_files = [speaker["path"] for speaker in self._speakers]
        video_audio_track = ""
        if not audio_files:
            video_audio_track = str(settings.get("reference_track") or self._default_video_audio_track(audio_tracks))
        reference_audio = settings.get("reference_audio")
        if not reference_audio and audio_files:
            reference_audio = audio_files[0]
        reference_track = str(settings.get("reference_track") or "")
        adjustment = float(settings.get("alignment_offset_adjustment") or 0.0)
        self.saveSettings(settings)
        self._transcription_generated_project_path = (
            str(Path(project_path).resolve()) if project_path else ""
        )
        command = build_gui_transcribe_command(
            self.gui_config_path,
            video=selection.video,
            audio_files=audio_files,
            output_dir=str(project_work_directory(self.projectSavePath)),
            render_output_dir=self.videoOutputDirectory,
            context_base_dir=str((self._project or {}).get("transcription", {}).get("context_base_dir") or Path(self.projectSavePath).parent),
            reference_audio=reference_audio,
            reference_track=reference_track,
            video_audio_track=video_audio_track,
            alignment_offset_adjustment=adjustment,
            overwrite_project=overwrite_project,
            project_path=project_path or self.projectSavePath,
        )
        self._start_command(command, "transcribe", "文字起こしを開始しています")

    @Slot("QVariantMap")
    def startProcessing(self, settings: dict[str, Any]) -> None:
        self.startTranscription(settings)

    @Slot("QVariantMap")
    def renderVideo(self, settings: dict[str, Any]) -> None:
        self._start_render(settings, short=False)

    def _start_render(self, settings: dict[str, Any], *, short: bool) -> None:
        if self._running or self._project is None:
            return
        self.refreshDependencies()
        preflight = render_capability(
            self._dependencies, self._project, self._project_path, short=short, require_output=False,
        )
        if not preflight.enabled:
            self._set_status(preflight.reason, "CHECK")
            return
        if not self.videoOutputDirectory:
            self.browseOutputDirectory()
            if not self.videoOutputDirectory:
                self._set_status("書き出しを中止しました。プロジェクトはそのまま編集できます", "CHECK")
                return
        try:
            request = prepare_render_request(
                self._dependencies, self._project, self._project_path,
                self.gui_config_path, short=short,
            )
        except ValueError as error:
            self._set_status(str(error), "CHECK")
            return
        effective_settings = dict(settings)
        effective_settings["video_codec"] = request.video_codec
        self.saveSettings(effective_settings)
        self._update_project_settings(effective_settings)
        if not self.saveProject():
            return
        mode = "GPU" if request.video_codec == "h264_nvenc" else "CPU"
        artifact = "ショート動画" if short else "動画"
        self._start_command(request.command, request.job, f"{mode}を自動選択して{artifact}を書き出しています")

    @Slot()
    def checkForUpdates(self) -> None:
        if self._update_busy:
            return
        self._update_busy = True
        self.updateBusyChanged.emit()
        self._update_error = ""
        self.updateErrorChanged.emit()
        self._set_status("最新リリースを確認しています", "UPDATE")
        threading.Thread(target=self._check_for_updates_worker, daemon=True).start()

    def _check_for_updates_worker(self) -> None:
        try:
            info = updater.fetch_latest_release(self.workspace_root)
            self.updateCheckFinished.emit(info, "")
        except updater.UpdaterError as error:
            self.updateCheckFinished.emit(None, str(error))
        except Exception as error:
            self.updateCheckFinished.emit(None, f"更新確認に失敗しました: {error}")

    def _on_update_check_finished(self, info: Any, error: str) -> None:
        self._update_info = info
        self._update_error = error
        self._update_busy = False
        self._update_package_path = None
        self._update_package_sha256 = ""
        self._update_package_ready = False
        self.updateInfoChanged.emit()
        self.updateErrorChanged.emit()
        self.updatePackageReadyChanged.emit()
        self.updateBusyChanged.emit()
        if error:
            self._set_status(error, "ERROR")
        elif info and info.available:
            self._set_status(f"新しいバージョン {info.latest_version} が利用可能です", "READY")
        elif info:
            self._set_status(f"最新バージョンです ({info.current_version})", "READY")

    @Slot()
    def dismissUpdateInfo(self) -> None:
        if self._running and self._active_job == "update":
            self._set_status("更新中は更新画面を閉じられません", "UPDATE")
            return
        if self._update_download_active:
            self._set_status("ダウンロード中は更新画面を閉じられません", "UPDATE")
            return
        self._update_info = None
        self._update_error = ""
        self._update_package_path = None
        self._update_package_sha256 = ""
        self._update_package_ready = False
        self.updateInfoChanged.emit()
        self.updateErrorChanged.emit()
        self.updatePackageReadyChanged.emit()

    @Slot()
    def downloadUpdate(self) -> None:
        if self._update_busy or self._update_download_active:
            return
        if not self._update_info or not self._update_info.available:
            self._set_status("更新可能なバージョンがありません", "CHECK")
            return
        if getattr(self._update_info, "package_type", "archive") != "installer":
            self._set_status("この配布形態は従来の更新方法を使用します", "UPDATE")
            return
        self._update_busy = True
        self._update_download_active = True
        self._update_download_cancel = threading.Event()
        self._update_download_bytes = 0
        self._update_download_total = int(self._update_info.package_size or 0)
        self._update_download_speed = 0.0
        self._update_error = ""
        self.updateBusyChanged.emit()
        self.updateErrorChanged.emit()
        self.updateDownloadProgressChanged.emit()
        self._set_status("更新パッケージをダウンロードしています", "UPDATE")
        threading.Thread(target=self._download_update_worker, daemon=True).start()

    def _download_update_worker(self) -> None:
        info = self._update_info
        if info is None:
            self.updateDownloadFinished.emit("", "更新情報がありません")
            return
        try:
            expected_sha256 = update_manager.resolve_expected_sha256(info)
            destination = update_manager.update_download_directory(self.workspace_root) / (
                installer_download_name(info.latest_version)
            )
            package_path = update_manager.download_package(
                info,
                destination,
                cancel_event=self._update_download_cancel,
                progress_callback=lambda downloaded, total, speed: self.updateDownloadProgressEvent.emit(downloaded, total, speed),
            )
            self._update_package_sha256 = expected_sha256
            self.updateDownloadFinished.emit(str(package_path), "")
        except update_manager.UpdateDownloadCancelled as error:
            self.updateDownloadFinished.emit("", str(error))
        except update_manager.UpdatePackageError as error:
            self.updateDownloadFinished.emit("", str(error))
        except Exception as error:
            self.updateDownloadFinished.emit("", f"更新パッケージの取得に失敗しました: {error}")

    def _on_update_download_progress(self, downloaded: int, total: int, speed: float) -> None:
        self._update_download_bytes = downloaded
        self._update_download_total = total
        self._update_download_speed = speed
        self.updateDownloadProgressChanged.emit()

    def _on_update_download_finished(self, package_path: str, error: str) -> None:
        self._update_download_active = False
        self._update_busy = False
        if error:
            self._update_error = error
            self._update_package_path = None
            self._update_package_ready = False
            self._set_status(error, "ERROR" if "キャンセル" not in error else "CANCELLED")
        else:
            self._update_error = ""
            self._update_package_path = Path(package_path)
            self._update_package_ready = True
            self._set_status("更新パッケージを検証しました。再起動して更新できます", "READY")
        self.updateBusyChanged.emit()
        self.updateErrorChanged.emit()
        self.updateDownloadProgressChanged.emit()
        self.updatePackageReadyChanged.emit()

    @Slot()
    def cancelUpdateDownload(self) -> None:
        if self._update_download_active:
            self._update_download_cancel.set()
            self._set_status("更新パッケージのダウンロードをキャンセルしています", "UPDATE")

    @Slot()
    def applyDownloadedUpdate(self) -> None:
        if not self._update_package_ready or not self._update_package_path or not self._update_info:
            self._set_status("検証済みの更新パッケージがありません", "CHECK")
            return
        if self._running:
            self._set_status("処理中は更新を開始できません", "BUSY")
            return
        if self._project_dirty:
            self._set_status("未保存の変更があります。更新前に保存してください", "CHECK")
            return
        if self._project is not None and not self.saveProject():
            self._set_status("プロジェクトを保存できませんでした", "ERROR")
            return
        self.saveSettings(self._settings)
        try:
            expected_sha256 = self._update_package_sha256 or update_manager.resolve_expected_sha256(self._update_info)
            result_path = update_manager.update_download_directory(self.workspace_root) / "last-update-result.json"
            command = update_manager.build_installer_helper_command(
                self.workspace_root,
                self._update_package_path,
                expected_version=self._update_info.latest_version,
                expected_sha256=expected_sha256,
                result_path=result_path,
            )
            popen_kwargs: dict[str, Any] = {
                "cwd": str(self.workspace_root),
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                **detached_subprocess_kwargs(),
            }
            subprocess.Popen(command, **popen_kwargs)
        except (OSError, update_manager.UpdatePackageError) as error:
            self._set_status(f"更新helperを起動できませんでした: {error}", "ERROR")
            return
        self._set_status("GUIを終了して更新を適用します", "UPDATE")
        self.quit()

    @Slot()
    def applyUpdate(self) -> None:
        if self._running:
            self._set_status("処理中は更新を開始できません", "BUSY")
            return
        if self._project_dirty:
            self._set_status("未保存の変更があります。更新前に保存してください", "CHECK")
            return
        if not self._update_info or not self._update_info.available:
            self._set_status("更新可能なバージョンがありません", "CHECK")
            return
        if getattr(self._update_info, "package_type", "archive") == "installer":
            if self._update_package_ready:
                self.applyDownloadedUpdate()
            else:
                self.downloadUpdate()
            return
        try:
            command = updater.launch_update_script(
                self.workspace_root, self._update_info.download_url
            )
        except updater.UpdaterError as error:
            self._set_status(str(error), "ERROR")
            return
        if self._project is not None and not self.saveProject():
            self._set_status("プロジェクトを保存できませんでした", "ERROR")
            return
        self.saveSettings(self._settings)
        self._start_command(command, "update", "アプリケーションを更新しています")

    @Slot()
    def cancelProcessing(self) -> None:
        if self._running and self._active_job == "update":
            self._set_status("更新処理は途中で停止できません", "UPDATE")
            return
        super().cancelProcessing()

    @Slot()
    def restartApplication(self) -> None:
        try:
            subprocess.Popen(
                [sys.executable, "-m", "src.gui"],
                cwd=str(self.workspace_root),
                **detached_subprocess_kwargs(),
            )
        except OSError as error:
            self._set_status(f"再起動に失敗しました: {error}", "ERROR")
            return
        self.quit()

    @Slot()
    def renderShortVideo(self) -> None:
        self._start_render(self.settings, short=True)

    @Slot()
    def reconnectCodexChat(self) -> None:
        self._ai_chat.reconnect()

    @Slot()
    def startCodexLogin(self) -> None:
        self._ai_chat.login()

    @Slot()
    def reloginCodex(self) -> None:
        self._ai_chat.login(relogin=True)

    @Slot()
    def logoutCodex(self) -> None:
        if self._codex_session.running:
            self._codex_session.stop()
        if self._codex_audio_mix_session.running:
            self._codex_audio_mix_session.stop()
        self._codex_proposal = None
        self.codexProposalChanged.emit()
        self._audio_mix_proposal = None
        self.audioMixProposalChanged.emit()
        self._ai_chat.logout()

    @Slot()
    def openCodexLoginPage(self) -> None:
        login_url = self._ai_chat.snapshot.login_url
        if login_url:
            QDesktopServices.openUrl(QUrl(login_url))

    @Slot(str)
    def selectCodexModel(self, model: str) -> None:
        self._ai_chat.select_model(model)

    @Slot(str, result=bool)
    def selectAIProvider(self, provider_id: str) -> bool:
        return self._ai_chat.select_provider(provider_id)

    @Slot()
    def reconnectAIChat(self) -> None:
        self._ai_chat.reconnect()

    @Slot()
    def startAIProviderLogin(self) -> None:
        self._ai_chat.login()

    @Slot()
    def reloginAIProvider(self) -> None:
        self._ai_chat.login(relogin=True)

    @Slot()
    def logoutAIProvider(self) -> None:
        self._ai_chat.logout()

    @Slot()
    def openAIProviderLoginPage(self) -> None:
        self.openCodexLoginPage()

    @Slot(str)
    @Slot(str, str, float, float)
    def sendCodexChatMessage(
        self,
        message: str,
        requested_scope: str = "auto",
        range_start: float = 0.0,
        range_end: float = 0.0,
    ) -> None:
        if self._is_audio_mix_chat_request(message):
            self._start_audio_mix_chat_proposal(message)
            return
        route = route_subtitle_chat_request(
            message,
            requested_scope,
            project_loaded=self._project is not None,
            has_selection=self._selected_segment_index >= 0,
            current_time=float(self.editorPlayhead.get("sourcePositionMs", 0)) / 1000.0,
            range_start=range_start,
            range_end=range_end,
        )
        if route is None:
            self._ai_chat.send_message(message)
            return
        # Typed edit proposals are a Codex action contract.  Gemini remains a
        # normal chat provider until a provider-neutral proposal protocol is
        # introduced; never route the same prompt to both providers.
        if self._ai_chat.active_provider_id != "codex":
            self._ai_chat.send_message(message)
            return
        if not self._ai_chat.begin_proposal(message):
            return
        if route.scope == "unavailable":
            self._codex_chat.fail_proposal("字幕を編集するには、先に編集プロジェクトを開いてください。")
            return
        if route.scope == "current":
            self._codex_current_time = float(self.editorPlayhead.get("sourcePositionMs", 0)) / 1000.0
        scope_id = f"chat-subtitle-{uuid4().hex}"
        payload: dict[str, Any] = {
            "schema_version": 1,
            "kind": "propose",
            "type": "propose_subtitle_edit",
            "args": {
                "intent": str(message).strip(),
                "selection_scope": route.scope,
            },
            "scope_id": scope_id,
            "project_revision": self._project_revision,
        }
        if route.scope == "time_range":
            payload["args"].update(
                {"range_start": route.range_start, "range_end": route.range_end}
            )
        result = self.dispatch_codex_action(
            payload,
            trusted_scope=ActionScope(
                id=scope_id,
                allowed_actions=frozenset({"propose_subtitle_edit"}),
                project_revision=self._project_revision,
            ),
        )
        if result.status.value != "success":
            self._ai_chat.fail_proposal(
                result.message or "字幕の変更案を開始できませんでした。"
            )

    @staticmethod
    def _is_audio_mix_chat_request(message: str) -> bool:
        # Route natural-language sound adjustments to the typed audio proposal.
        prompt = str(message).strip().casefold()
        if not prompt:
            return False

        # Explicit mixer controls remain unambiguous. For ordinary language,
        # combine an audio target with an adjustment intent instead of requiring
        # one exact phrase (for example, 「声を聞きやすくして」).
        explicit_audio_controls = (
            "音量",
            "ミキサー",
            "ミックス",
            "ミュート",
            "bgm",
            "volume",
            "mute",
            "solo",
            "audio mix",
            "audio mixer",
        )
        if any(term in prompt for term in explicit_audio_controls):
            return True

        audio_targets = (
            "声",
            "音声",
            "音楽",
            "ナレーション",
            "ボーカル",
            "voice",
            "audio",
            "sound",
            "music",
            "track",
        )
        adjustment_intents = (
            "聞きやす",
            "聞こえ",
            "明瞭",
            "クリア",
            "大き",
            "小さ",
            "上げ",
            "下げ",
            "調整",
            "整え",
            "バランス",
            "抑え",
            "目立",
            "clear",
            "adjust",
            "balance",
            "raise",
            "lower",
            "louder",
            "quieter",
        )
        return (
            any(term in prompt for term in audio_targets)
            and any(term in prompt for term in adjustment_intents)
        )

    def _start_audio_mix_chat_proposal(self, message: str) -> None:
        if self._ai_chat.active_provider_id != "codex":
            self._ai_chat.send_message(message)
            return
        if not self._ai_chat.begin_proposal(
            message,
            content_type="audio_mix_proposal",
            pending_text="音量ミキサーの変更案を作成しています…",
        ):
            return
        scope_id = f"chat-audio-{uuid4().hex}"
        payload: dict[str, Any] = {
            "schema_version": 1,
            "kind": "propose",
            "type": "propose_audio_mix",
            "args": {"intent": str(message).strip()},
            "scope_id": scope_id,
            "project_revision": self._project_revision,
        }
        result = self.dispatch_codex_action(
            payload,
            trusted_scope=ActionScope(
                id=scope_id,
                allowed_actions=frozenset({"propose_audio_mix"}),
                project_revision=self._project_revision,
            ),
        )
        if result.status.value != "success":
            self._ai_chat.fail_proposal(
                result.message or "音量ミキサーの変更案を開始できませんでした。"
            )

    @Slot()
    def stopCodexChat(self) -> None:
        stopped_proposal = False
        if self._codex_session.running:
            self._codex_session.stop()
            stopped_proposal = True
        if self._codex_audio_mix_session.running:
            self._codex_audio_mix_session.stop()
            stopped_proposal = True
        if stopped_proposal:
            self._ai_chat.fail_proposal("", cancelled=True)
        else:
            self._ai_chat.interrupt()

    @Slot()
    def startNewCodexChat(self) -> None:
        if self._codex_session.running:
            self._codex_session.stop()
        if self._codex_audio_mix_session.running:
            self._codex_audio_mix_session.stop()
        self._codex_proposal = None
        self.codexProposalChanged.emit()
        self._audio_mix_proposal = None
        self.audioMixProposalChanged.emit()
        self._ai_chat.new_chat()

    def _queue_codex_system_log(self, message: object, *, severity: str = "INFO") -> None:
        safe_message = str(message)
        self._dispatch_codex_callback(
            lambda: self._record_log(
                safe_message,
                severity=severity,
                component="codex",
                stage="CODEX",
            )
        )

    def _create_codex_chat_client(self, cwd: str | Path | None = None) -> CodexAppServerClient:
        runtime = detect_codex(self.workspace_root)
        if not runtime.available:
            self._queue_codex_system_log(
                f"Codex CLI検出失敗: {runtime.error}",
                severity="ERROR",
            )
            raise CodexChatError(runtime.error)
        self._queue_codex_system_log(
            "Codex CLI検出成功: "
            f"version={runtime.version}, distribution={runtime.distribution}, "
            f"executable={runtime.executable}"
        )

        def record_app_server(message: str) -> None:
            self._queue_codex_system_log(
                message,
                severity="ERROR" if message.startswith("ERROR:") else "INFO",
            )

        return CodexAppServerClient(
            runtime.command,
            cwd=cwd or self.workspace_root,
            log_callback=record_app_server,
        )

    def _create_gemini_chat_provider(self) -> GeminiAcpProvider:
        return GeminiAcpProvider(
            workspace_root=self.workspace_root,
            preferred_model=str(self._settings.get("gemini_model", "")),
        )

    @Slot(str, str, float, float)
    def startCodexEdit(
        self,
        prompt: str,
        scope: str,
        range_start: float = 0.0,
        range_end: float = 0.0,
    ) -> None:
        if self._project is None:
            self._set_status("先に編集プロジェクトを開いてください", "CHECK")
            return
        if self._codex_session.running or self._codex_audio_mix_session.running:
            return
        selected_ids = {
            str(self._project.get("segments", [])[self._selected_segment_index].get("id"))
        } if 0 <= self._selected_segment_index < len(self._project.get("segments", [])) else set()
        current_time = self._codex_current_time
        if current_time is None:
            if 0 <= self._selected_segment_index < len(self._project.get("segments", [])):
                current_time = float(
                    self._project["segments"][self._selected_segment_index].get(
                        "start", range_start
                    )
                )
            else:
                current_time = range_start
        try:
            context = build_codex_context(
                self._project,
                scope,
                selected_segment_ids=selected_ids,
                current_time=current_time,
                range_start=range_start,
                range_end=range_end,
            )
            self._codex_proposal = None
            self.codexProposalChanged.emit()
            self._codex_session.start(
                prompt=prompt,
                context=context,
                output_schema=CODEX_OUTPUT_SCHEMA,
                revision=self._project_revision,
            )
            self._set_status("Codexへ編集案を依頼しています", "CODEX")
        except (CodexSessionError, ValueError) as error:
            self._set_status(f"Codex編集を開始できません: {error}", "ERROR")

    @Slot(float)
    def setCodexCurrentTime(self, seconds: float) -> None:
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            return
        if math.isfinite(value) and value >= 0.0:
            self._codex_current_time = value

    @Slot()
    def stopCodexEdit(self) -> None:
        self._codex_session.stop()
        self._set_status("Codex編集を停止しました", "CODEX")

    @Slot("QVariantList")
    def applyCodexProposal(self, selected_operation_ids: list[Any] | None = None) -> None:
        if self._project is None or not self._codex_proposal:
            self._set_status("適用するCodex編集案がありません", "CHECK")
            return
        before = deepcopy(self._project.get("segments", []))
        try:
            result = self._codex_session.apply_to_project(
                self._project,
                self._codex_proposal,
                selected_operation_ids={str(item) for item in (selected_operation_ids or [])} or None,
                current_revision=self._project_revision,
            )
        except (CodexSessionError, ValueError, TypeError) as error:
            self._set_status(f"Codex編集案を適用できません: {error}", "ERROR")
            return
        self._project = result.project
        after = deepcopy(self._project.get("segments", []))
        self._record_history(before, after)
        self._sync_subtitle_model()
        self.projectDataChanged.emit()
        self.segmentsChanged.emit()
        self._mark_project_dirty()
        if result.changed_segment_ids:
            first_id = result.changed_segment_ids[0]
            self._selected_segment_index = next(
                (index for index, item in enumerate(after) if str(item.get("id")) == first_id),
                -1,
            )
            self.selectionChanged.emit()
        self._codex_proposal = None
        self.codexProposalChanged.emit()
        self._set_status("Codex編集案を適用しました。内容を確認して保存してください", "EDIT")

    @Slot()
    def discardCodexProposal(self) -> None:
        self._codex_proposal = None
        self.codexProposalChanged.emit()
        self._set_status("Codex編集案を破棄しました", "EDIT")

    def dispatch_codex_action(
        self,
        payload: Mapping[str, Any],
        *,
        trusted_scope: ActionScope,
    ) -> ActionResult:
        """Dispatch an Action with scope authorization created outside Codex output."""

        return self._codex_actions.dispatch(payload, trusted_scope=trusted_scope)

    def codex_render_output_exists(self, *, short: bool) -> bool:
        """Check overwrite policy using the same output resolver as GUI render."""

        if self._project is None or not self._project_path:
            return False
        try:
            return render_output_path(self._project_path, self._project, short=short).exists()
        except ValueError:
            return False

    def _on_codex_state(self, _snapshot: CodexSessionSnapshot) -> None:
        self.codexStateChanged.emit()
        self.codexMessageChanged.emit()
        if self._codex_session.snapshot.error:
            self._codex_chat.fail_proposal(self._codex_session.snapshot.error)
            self._set_status(self._codex_session.snapshot.error, "ERROR")

    def _on_codex_message(self, _message: str) -> None:
        self.codexMessageChanged.emit()

    def _on_codex_proposal(self, proposal: Mapping[str, Any]) -> None:
        self._codex_proposal = dict(proposal)
        self.codexProposalChanged.emit()
        self._codex_chat.complete_proposal(str(proposal.get("summary", "")))
        self._set_status("Codex編集案を確認できます", "CODEX")

    def _on_codex_audio_mix_state(self, _snapshot: CodexSessionSnapshot) -> None:
        self.audioMixProposalChanged.emit()
        snapshot = self._codex_audio_mix_session.snapshot
        if snapshot.error:
            self._codex_chat.fail_proposal(snapshot.error)
            self._set_status(snapshot.error, "ERROR")
        elif snapshot.state == "unauthenticated":
            self._codex_chat.fail_proposal("Codexへログインしてください。")
            self._set_status("Codexへログインしてください", "CHECK")

    def _on_codex_audio_mix_proposal(self, proposal: Mapping[str, Any]) -> None:
        if self._project is None:
            self._codex_chat.fail_proposal("編集プロジェクトが閉じられました。", cancelled=False)
            return
        try:
            stored = build_audio_mix_proposal(
                proposal,
                self.audioMixerChannels,
                project_revision=self._project_revision,
            )
        except (AudioMixProposalError, ValueError, TypeError) as error:
            self._codex_chat.fail_proposal(f"音量ミキサーの変更案を検証できません: {error}")
            self._set_status("音量ミキサーの変更案を検証できません", "ERROR")
            return
        self._audio_mix_proposal = stored
        self.audioMixProposalChanged.emit()
        self._codex_chat.complete_proposal(str(stored.get("summary", "")))
        self._set_status("音量ミキサーの変更案を確認できます", "CODEX")

    def _on_codex_provider_state(self, snapshot: CodexChatSnapshot) -> None:
        if hasattr(self, "_ai_chat"):
            self._ai_chat._provider_state_changed("codex", snapshot)
        else:
            self._on_codex_chat_state(snapshot)

    def _on_gemini_provider_state(self, snapshot: CodexChatSnapshot) -> None:
        if hasattr(self, "_ai_chat"):
            self._ai_chat._provider_state_changed("gemini", snapshot)

    def _persist_ai_provider(self, provider_id: str) -> None:
        if self._settings.get("ai_provider") == provider_id:
            return
        self._save_settings({"ai_provider": provider_id}, announce=False)

    def _on_codex_chat_state(self, snapshot: CodexChatSnapshot) -> None:
        self.codexChatChanged.emit()
        self.aiChatChanged.emit()
        if snapshot.provider_id == "codex" and snapshot.connection_state == "disconnected" and self._codex_session.running:
            self._codex_session.stop()
            self._codex_chat.fail_proposal("Codexとの接続が切れたため、変更案の作成を停止しました。")
        if snapshot.provider_id == "codex" and snapshot.connection_state == "disconnected" and self._codex_audio_mix_session.running:
            self._codex_audio_mix_session.stop()
            self._codex_chat.fail_proposal("Codexとの接続が切れたため、変更案の作成を停止しました。")
        log_state: tuple[object, ...] = (
            snapshot.provider_id,
            snapshot.connection_state,
            snapshot.auth_state,
            snapshot.chat_state,
            snapshot.selected_model,
            len(snapshot.models),
            snapshot.model_error,
            snapshot.error,
        )
        if log_state != self._last_codex_log_state:
            self._last_codex_log_state = log_state
            detail = (
                f"{snapshot.provider_name}状態: "
                f"connection={snapshot.connection_state}, "
                f"auth={snapshot.auth_state}, "
                f"chat={snapshot.chat_state}, "
                f"models={len(snapshot.models)}, "
                f"selected_model={snapshot.selected_model or 'none'}"
            )
            if snapshot.model_error:
                detail += f", model_error={snapshot.model_error}"
            if snapshot.error:
                detail += f", error={snapshot.error}"
            self._record_log(
                detail,
                severity="ERROR" if snapshot.error else "INFO",
                component=snapshot.provider_id,
                stage="CODEX",
            )
        if snapshot.login_url and snapshot.login_url != self._last_codex_login_url:
            self._last_codex_login_url = snapshot.login_url
            QDesktopServices.openUrl(QUrl(snapshot.login_url))

    def _persist_codex_model(self, model: str) -> None:
        if self._settings.get("codex_model") == model:
            return
        self._save_settings({"codex_model": model}, announce=False)

    def _persist_gemini_model(self, model: str) -> None:
        if self._settings.get("gemini_model") == model:
            return
        self._save_settings({"gemini_model": model}, announce=False)

    def _dispatch_codex_callback(self, callback: Callable[[], None]) -> None:
        self.codexCallbackRequested.emit(callback)

    @Slot(object)
    def _run_codex_callback(self, callback: object) -> None:
        if callable(callback):
            callback()

    def _process_started(self) -> None:
        self._running = True
        self.runningChanged.emit()
        self.elapsed_timer.start()
        if self._active_job == "transcribe":
            self._set_status("文字起こしと編集プロジェクト作成を実行しています", "TRANSCRIBE")
        elif self._active_job == "update":
            self._set_status("アプリケーションを更新しています", "UPDATE")
        elif self._active_job == "render_short":
            self._set_status("ショート動画を書き出しています", "ENCODE")
        else:
            self._set_status("編集済み字幕を動画へ焼き付けています", "ENCODE")

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
        data = (
            output
            if output is not None
            else bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        )
        if not data:
            return
        normalized = data.replace("\r", "\n")
        self._process_output_tail = (self._process_output_tail + normalized)[-50_000:]
        self._record_log(
            normalized,
            component=self._active_job or "process",
            job=self._active_job,
            stage=self.stage,
            process_id=int(self.process.processId()) or None,
        )
        self._update_stage(normalized)

    def _finish_processing_progress(self, outcome: str) -> None:
        if not self._processing_progress.steps and self._active_job:
            self._processing_progress.start(self._active_job)
        if not self._processing_progress.steps:
            # Trackerless jobs (currently the self-update process) retain the
            # legacy scalar progress path.  A successful process still needs
            # to reach 100% when its QProcess exits cleanly.
            self._processing_progress.finish(outcome)
            if outcome == "completed":
                self._progress = 1.0
                self.progressChanged.emit()
            self.progressDetailsChanged.emit()
            return
        self._processing_progress.finish(outcome)
        self._progress = self._processing_progress.value
        self.progressChanged.emit()
        self.progressDetailsChanged.emit()

    def _process_error(self, error: QProcess.ProcessError) -> None:
        message = self.process.errorString() or str(error)
        self._pending_process_error = message
        self._record_log(
            message,
            severity="ERROR",
            component=self._active_job or "qprocess",
            job=self._active_job,
            stage="ERROR",
            process_id=int(self.process.processId()) or None,
        )
        if not self._running and self.process.state() == QProcess.ProcessState.NotRunning:
            self._read_process_output()
            self._set_status(message, "ERROR")
            failed_job = self._active_job
            self._capture_process_diagnostic(
                job=failed_job,
                outcome="failed",
                exit_code=None,
            )
            if failed_job == "transcribe":
                self._reset_transcription_integration_state()
            self._finish_processing_progress("error")
            self._active_job = ""
            self.activeJobChanged.emit()

    def _update_stage(self, output: str) -> None:
        for event in parse_progress_events(output):
            try:
                target_duration = float(event.get("duration", 0.0))
            except (TypeError, ValueError):
                target_duration = 0.0
            if target_duration > 0.0:
                self._ffmpeg_duration_seconds = target_duration
                self._ffmpeg_duration_from_event = True
            if (
                event.get("step") == "encode"
                and event.get("phase") == "start"
                and not self._ffmpeg_duration_from_event
            ):
                self._ffmpeg_duration_seconds = 0.0
            if self._processing_progress.update(event):
                self._processing_machine_event_seen = True
                self._progress = self._processing_progress.value
                self.progressChanged.emit()
                self.progressDetailsChanged.emit()
        for line in output.splitlines():
            if "Duration:" in line and self._ffmpeg_duration_seconds <= 0.0:
                duration = parse_ffmpeg_timestamp(line)
                if duration and duration > 0.0:
                    self._ffmpeg_duration_seconds = duration
            if self._processing_progress.current_step != "encode":
                continue
            if "time=" not in line:
                continue
            timestamp = parse_ffmpeg_timestamp(line)
            if timestamp is None or self._ffmpeg_duration_seconds <= 0.0:
                continue
            encode_progress = min(1.0, timestamp / self._ffmpeg_duration_seconds)
            if self._processing_progress.update(
                {
                    "job": self._processing_progress.job,
                    "step": "encode",
                    "phase": "progress",
                    "progress": encode_progress,
                }
            ):
                self._progress = self._processing_progress.value
                self.progressChanged.emit()
                self.progressDetailsChanged.emit()
        markers = [
            ("Resolving alignment", "alignment", "ALIGN", "動画と話者音声を同期しています", 0.08),
            ("Starting WhisperX", "transcription", "WHISPERX", "文字起こししています", 0.22),
            ("CPU postprocess", "refine", "LAYOUT", "字幕を統合・整形しています", 0.58),
            ("Refining merged", "refine", "LAYOUT", "編集用字幕を組み立てています", 0.64),
            ("Building waveform", "waveform", "WAVEFORM", "タイムライン波形を作成しています", 0.78),
            ("Project ready", "project", "PROJECT", "編集プロジェクトを保存しています", 0.92),
            ("ASS preview ready", "subtitle", "ASS", "ASS字幕を生成しています", 0.3),
            ("Rendering edited", "encode", "ENCODE", "字幕を動画へ焼き付けています", 0.45),
            ("Rendering edited subtitles", "encode", "ENCODE", "字幕を動画へ焼き付けています", 0.45),
            ("Rendering short video", "encode", "ENCODE", "ショート動画をエンコードしています", 0.45),
            ("Render complete", "finalize", "ENCODE", "動画を書き出しました", 0.96),
            ("Short render complete", "finalize", "ENCODE", "ショート動画を書き出しました", 0.96),
        ]
        for marker, step, stage, status, progress in markers:
            if marker in output:
                tracker_updated = False
                if (
                    self._processing_progress.job
                    and not self._processing_machine_event_seen
                    and step != "waveform"
                ):
                    tracker_updated = self._processing_progress.update(
                        {
                            "job": self._processing_progress.job,
                            "step": step,
                            "phase": "progress",
                            "progress": progress,
                        }
                    )
                if tracker_updated:
                    self._progress = self._processing_progress.value
                elif (
                    not self._processing_machine_event_seen
                    and (step != "waveform" or not self._processing_progress.steps)
                ):
                    self._progress = max(self._progress, progress)
                self.progressChanged.emit()
                self.progressDetailsChanged.emit()
                self._set_status(status, stage)

    def _process_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        self._read_process_output()
        self.elapsed_timer.stop()
        self._running = False
        self.runningChanged.emit()
        completed_job = self._active_job
        failure_detail = ""
        if exit_code != 0 and completed_job != "update":
            failure_detail = next(
                (
                    line.strip()
                    for line in reversed(self._process_output_tail.splitlines())
                    if line.strip() and not line.lstrip().startswith("PROGRESS_EVENT ")
                ),
                "",
            )
        finish_stage = (
            "CANCELLED"
            if self._cancel_requested
            else "COMPLETE" if exit_code == 0 else "ERROR"
        )
        self._record_log(
            f"Process finished with exit code {exit_code}",
            severity="INFO" if exit_code == 0 or self._cancel_requested else "ERROR",
            component=completed_job or "process",
            job=completed_job,
            stage=finish_stage,
            exit_code=exit_code,
        )
        if self._cancel_requested:
            if completed_job == "transcribe":
                self._reset_transcription_integration_state()
            self._finish_processing_progress("cancelled")
            self._set_status("処理を停止しました", "CANCELLED")
        elif exit_code == 0:
            self._finish_processing_progress("completed")
            if completed_job == "transcribe":
                preserved_workspace = (
                    (self.currentEditMode, self.editorPlayhead)
                    if self._transcription_preserved_project is not None else None
                )
                generated_project_path = (
                    Path(self._transcription_generated_project_path)
                    if self._transcription_generated_project_path
                    else None
                )
                loaded = (
                    self._load_project_path(generated_project_path, update_sources=False)
                    if generated_project_path is not None and generated_project_path.is_file()
                    else self._try_load_default_project()
                )
                merged = False
                integration_error = ""
                if loaded and self._transcription_merge_mode in {"merge", "replace"}:
                    try:
                        applied = self._merge_preserved_transcription_segments()
                        merged = applied and self._transcription_merge_mode == "merge"
                    except (OSError, SubtitleProjectError, TypeError, ValueError) as error:
                        integration_error = (
                            "文字起こし結果の統合に失敗しました: "
                            f"{error}"
                        )
                        try:
                            self._restore_preserved_transcription_project()
                        except (OSError, SubtitleProjectError, TypeError, ValueError) as restore_error:
                            integration_error += (
                                "（元プロジェクトの復元にも失敗しました: "
                                f"{restore_error}）"
                            )
                if self._transcription_generated_project_path and not loaded:
                    integration_error = "文字起こし結果の一時プロジェクトを読み込めませんでした"
                self._reset_transcription_integration_state()
                if preserved_workspace is not None:
                    self.selectEditMode(preserved_workspace[0])
                    playhead = preserved_workspace[1]
                    basis = playhead["basis"]
                    self.setEditorPlayhead(playhead[f"{basis}PositionMs"], basis)
                if integration_error:
                    self._set_status(integration_error, "ERROR")
                else:
                    self._set_status(
                        "文字起こし結果を既存字幕へ追加しました。内容を確認してください"
                        if merged
                        else "文字起こし完了。字幕を確認して動画へ焼き付けられます"
                        if loaded
                        else "文字起こしが完了しました。編集プロジェクトを開いてください",
                        "EDIT" if loaded else "CHECK",
                    )
            elif completed_job == "update":
                self._set_status("更新が完了しました。アプリを再起動してください", "UPDATE")
            elif completed_job == "render_short":
                self._set_status("ショート動画の書き出しが完了しました", "COMPLETE")
            else:
                self._set_status("編集済み動画の書き出しが完了しました", "COMPLETE")
        else:
            if completed_job == "transcribe":
                self._reset_transcription_integration_state()
            self._finish_processing_progress("error")
            if completed_job == "update":
                self._set_status(f"更新に失敗しました（終了コード {exit_code}）。バックアップから復元されています", "ERROR")
            else:
                suffix = f": {failure_detail}" if failure_detail else ""
                self._set_status(
                    f"処理が終了しました（終了コード {exit_code}）{suffix}",
                    "ERROR",
                )
        if self._cancel_requested or exit_code != 0:
            self._capture_process_diagnostic(
                job=completed_job,
                outcome="cancelled" if self._cancel_requested else "failed",
                exit_code=exit_code,
            )
        self._active_job = ""
        self.activeJobChanged.emit()


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
