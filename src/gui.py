from __future__ import annotations

from dataclasses import replace
import json
import math
import os
import subprocess
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Mapping, cast

# PySide6 exposes typing.Self on Python 3.10. Initialize the optional backport
# first so PyTorch keeps its compatible Self implementation when WhisperX is
# installed. Lightweight development/test environments may omit it.
try:
    from typing_extensions import Self as _TypingSelf  # noqa: F401
except ImportError:  # pragma: no cover - release runtimes always lock it
    pass

from PySide6.QtCore import (
    QObject,
    QTimer,
    Qt,
    QUrl,
)
from PySide6.QtGui import QDesktopServices, QFontDatabase
from PySide6.QtMultimedia import QAudioBuffer
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QFileDialog

from .qt_decorators import Property, Signal, Slot
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
)
from .codex_actions import build_gui_action_dispatcher
from .gui_codex_chat_state import (
    CodexChatController,
)
from .gui_ai_chat_state import AIProviderChatRouter
from .gui_audio_preview_controller import AudioPreviewController
from .gui_project_editor_controller import ProjectEditorController
from .application_logging import ApplicationLogger, ProcessDiagnosticSnapshot
from .application_info import resolve_application_info
from .realtime_audio_mixer import RealtimeAudioMixer
from .color_config import normalize_rgb_color
from .data_boundary import (
    coerce_float,
    coerce_int,
    is_object_list,
    is_string_object_dict,
    is_string_object_dict_list,
    is_string_object_mapping,
)
from .gui_base import APP_TITLE, AlignmentResult, LegacyEditBayBackend
from .gui_source_selection_controller import SourceSelectionUpdate
from .gui_source_state import SourceSelection, build_speaker_entries_from_files
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
from .render_ass import style_name_for_speaker
from .runtime_dependencies import runtime_diagnostic_info
from .transcription_project_integration import ensure_transcription_context_base_dir
from .video_sequence import VideoSequence, VideoSequenceError
from .video_timeline import VideoTimelineView

from .gui_workspace_facade import WorkspaceFacade
from .gui_workspace_controller import WorkspacePlayerPayload
from .gui_subtitles_facade import SubtitleFacade
from .gui_short_video_facade import ShortVideoFacade
from .gui_audio_facade import AudioFacade
from .gui_sequence_facade import (
    MediaBinAssetView,
    SequenceClipView,
    SequenceDependencies,
    SequenceFacade,
    SequencePlayheadView,
    SequenceViewPayload,
)
from .gui_workflow_facade import WorkflowFacade
from .gui_ai_facade import AIChatFacade, AIServices
from .gui_updates_facade import UpdateFacade
from .gui_models import ShortVideoClipListModel, SubtitleListModel
from .gui_backend_compatibility import LegacyBackendCompatibility


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


class EditBayBackend(LegacyBackendCompatibility, LegacyEditBayBackend):
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

    def _initial_value(self, name: str) -> object:
        """初期化途中の互換属性を、未検証の値として受け取る。"""
        return cast(object, getattr(self, name, None))

    def _existing_project_editor(self) -> ProjectEditorController | None:
        value = self._initial_value("_project_editor_controller")
        return value if isinstance(value, ProjectEditorController) else None

    def _require_project_editor(self) -> ProjectEditorController:
        controller = self._existing_project_editor()
        if controller is None:
            raise RuntimeError("プロジェクト編集機能が初期化されていません")
        return controller

    def _existing_audio_preview(self) -> AudioPreviewController | None:
        value = self._initial_value("_audio_preview_controller")
        return value if isinstance(value, AudioPreviewController) else None

    @staticmethod
    def _load_project_compat(path: str | Path, *, resolve_video_duration: bool = False) -> dict[object, object]:
        return load_project(path, resolve_video_duration=resolve_video_duration)

    @staticmethod
    def _save_project_compat(
        path: str | Path,
        project: dict[object, object],
        *,
        project_is_validated: bool = False,
        update_project: bool = True,
    ) -> Path:
        return save_project(
            path,
            project,
            project_is_validated=project_is_validated,
            update_project=update_project,
        )

    @staticmethod
    def _prepare_cache_compat(
        project: Mapping[str, object],
        root: Path,
        /,
        *,
        protected_paths: list[Path],
    ) -> AudioPreviewCacheResult:
        return prepare_audio_preview_cache(project, root, protected_paths=protected_paths)

    @Property(QObject, constant=True)
    def workspace(self) -> WorkspaceFacade:
        return self._workspace_facade

    @Property(QObject, constant=True)
    def subtitles(self) -> SubtitleFacade:
        return self._subtitles_facade

    @Property(QObject, constant=True)
    def shortVideo(self) -> ShortVideoFacade:
        return self._short_video_facade

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
    def _project(self) -> dict[str, object] | None:
        controller = self._existing_project_editor()
        if controller is not None:
            return controller.project
        value = self._initial_value("_project_value")
        return value if is_string_object_dict(value) else None

    @_project.setter
    def _project(self, value: dict[str, object] | None) -> None:
        controller = self._existing_project_editor()
        if controller is None:
            self._project_value = value
        else:
            controller.project = value
        if hasattr(self, "_codex_audio_mix_session"):
            self._codex_audio_mix_session.stop()
        audio_controller = self._existing_audio_preview()
        if audio_controller is not None:
            audio_controller.set_project(value)

    @property
    def _project_path(self) -> str:
        controller = self._existing_project_editor()
        if controller is not None:
            return controller.project_path
        value = self._initial_value("_project_path_value")
        return value if isinstance(value, str) else ""

    @_project_path.setter
    def _project_path(self, value: str | Path) -> None:
        controller = self._existing_project_editor()
        if controller is None:
            self._project_path_value = str(value)
        else:
            controller.project_path = value

    @property
    def _project_dirty(self) -> bool:
        controller = self._existing_project_editor()
        return controller.project_dirty if controller is not None else bool(self._initial_value("_project_dirty_value"))

    @_project_dirty.setter
    def _project_dirty(self, value: bool) -> None:
        controller = self._existing_project_editor()
        if controller is None:
            self._project_dirty_value = bool(value)
        else:
            controller.project_dirty = value

    @property
    def _project_revision(self) -> int:
        controller = self._existing_project_editor()
        value = self._initial_value("_project_revision_value")
        return controller.project_revision if controller is not None else coerce_int(value if value is not None else 0)

    @_project_revision.setter
    def _project_revision(self, value: int) -> None:
        controller = self._existing_project_editor()
        if controller is None:
            self._project_revision_value = int(value)
        else:
            controller.project_revision = value

    @property
    def _undo_stack(self) -> list[dict[str, object]]:
        controller = self._existing_project_editor()
        if controller is not None:
            return controller.undo_stack
        value = self._initial_value("_undo_stack_value")
        return value if is_string_object_dict_list(value) else []

    @_undo_stack.setter
    def _undo_stack(self, value: list[dict[str, object]]) -> None:
        controller = self._existing_project_editor()
        if controller is None:
            self._undo_stack_value = value
        else:
            controller.undo_stack.clear()
            controller.undo_stack.extend(value)

    @property
    def _redo_stack(self) -> list[dict[str, object]]:
        controller = self._existing_project_editor()
        if controller is not None:
            return controller.redo_stack
        value = self._initial_value("_redo_stack_value")
        return value if is_string_object_dict_list(value) else []

    @_redo_stack.setter
    def _redo_stack(self, value: list[dict[str, object]]) -> None:
        controller = self._existing_project_editor()
        if controller is None:
            self._redo_stack_value = value
        else:
            controller.redo_stack.clear()
            controller.redo_stack.extend(value)

    @property
    def _selected_segment_index(self) -> int:
        controller = self._existing_project_editor()
        value = self._initial_value("_selected_segment_index_value")
        return (
            controller.selected_segment_index
            if controller is not None
            else coerce_int(value if value is not None else -1)
        )

    @_selected_segment_index.setter
    def _selected_segment_index(self, value: int) -> None:
        controller = self._existing_project_editor()
        if controller is None:
            self._selected_segment_index_value = int(value)
        else:
            controller.selected_segment_index = value

    @property
    def _autosave_future(self) -> Future[Path] | None:
        return self._require_project_editor().autosave_future

    @_autosave_future.setter
    def _autosave_future(self, value: Future[Path] | None) -> None:
        self._require_project_editor().autosave_future = value

    @property
    def _autosave_revision(self) -> int:
        return self._require_project_editor().autosave_revision

    @_autosave_revision.setter
    def _autosave_revision(self, value: int) -> None:
        self._require_project_editor().autosave_revision = value

    @property
    def _autosave_path(self) -> str:
        return self._require_project_editor().autosave_path

    @_autosave_path.setter
    def _autosave_path(self, value: str | Path) -> None:
        self._require_project_editor().autosave_path = value

    @property
    def _autosave_pending(self) -> bool:
        return self._require_project_editor().autosave_pending

    @_autosave_pending.setter
    def _autosave_pending(self, value: bool) -> None:
        self._require_project_editor().autosave_pending = value

    @property
    def _ignored_autosaves(self) -> set[tuple[int, str]]:
        return self._require_project_editor().ignored_autosaves

    @property
    def _autosave_executor(self) -> ThreadPoolExecutor:
        return self._require_project_editor().autosave_executor

    @property
    def audio_preview_cache_root(self) -> Path:
        controller = self._existing_audio_preview()
        if controller is not None:
            return controller.cache_root
        value = self._initial_value("_audio_preview_cache_root")
        return value if isinstance(value, Path) else Path()

    @audio_preview_cache_root.setter
    def audio_preview_cache_root(self, value: str | Path) -> None:
        root = Path(value)
        self._audio_preview_cache_root = root
        controller = self._existing_audio_preview()
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
    def _audio_preview_outputs(self) -> dict[str, QObject]:
        return cast(dict[str, QObject], self._audio_preview_controller.outputs)

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
        resolved_workspace_root = (workspace_root or Path(__file__).resolve().parent.parent).resolve()
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
        self._relink_alignment_result: AlignmentResult | None = None
        self._relink_project_was_dirty: bool | None = None
        self._relink_project_revision = 0
        self._relink_output_changes = 0
        super().__init__(argv, workspace_root=resolved_workspace_root)
        self._workspace_facade = WorkspaceFacade(self)
        self._subtitles_facade = SubtitleFacade(self)
        self._short_video_facade = ShortVideoFacade(self)
        self._audio_facade = AudioFacade(self)
        self._sequence_facade = SequenceFacade(
            self,
            SequenceDependencies(
                local_path=lambda value: self._local_path(value),
                validate_media_file=lambda source, streams, label: self._is_supported_media_file(
                    source, streams, label
                ),
                normalize_source_path=lambda value: self._normalized_source_path(value),
                set_status=lambda message, stage: self._set_status(message, stage),
            ),
        )
        self._workflow_facade = WorkflowFacade(self)
        self._ai_facade = AIChatFacade(self)
        self._updates_facade = UpdateFacade(self)
        for signal in (
            self.dependenciesChanged,
            self.sourceSelectionChanged,
            self.speakersChanged,
            self.audioTracksChanged,
            self.settingsChanged,
            self.projectChanged,
            self.projectDataChanged,
            self.shortVideoChanged,
            self.runningChanged,
        ):
            signal.connect(self.actionCapabilitiesChanged.emit)
        # The sequence view is a facade snapshot.  ProjectEditorController
        # remains the only owner of persisted sequence mutations; these
        # notifications only tell QML to read a fresh view.
        self.projectChanged.connect(self.sequenceChanged.emit)
        self.projectDataChanged.connect(self.sequenceChanged.emit)
        self.projectChanged.connect(self._refresh_editor_workspace)
        self.projectDataChanged.connect(self._refresh_editor_workspace)
        self.sourceSelectionChanged.connect(self._refresh_editor_workspace)
        self._application_logger.application_info = dict(self._application_info)
        self._log = self._application_logger.text
        self._record_startup_diagnostics()
        self._font_choices = build_font_choices(QFontDatabase.families())
        self._subtitle_model = SubtitleListModel(self)
        self._short_video_clip_model = ShortVideoClipListModel(
            self._short_video_clip_count,
            self._short_video_clip_view_at,
            self,
        )
        self.shortVideoChanged.connect(self._refresh_short_video_clip_data)
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(700)
        self.autosave_timer.timeout.connect(self._autosave_project)
        self.runningChanged.connect(self._sync_autosave_with_processing)
        self._project_editor_controller = ProjectEditorController(
            resolved_workspace_root,
            # Resolve these names at call time so existing tests can patch
            # src.gui.save_project without changing the controller boundary.
            load_project_fn=self._load_project_compat,
            save_project_fn=self._save_project_compat,
            # Keep the existing module-level patch/extension point used by
            # the facade and GUI regression tests while the controller owns
            # the project edit operation.
            assign_project_layout_rows_fn=(lambda segments: assign_project_layout_rows(segments)),
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
        for facade in (
            self._workspace_facade,
            self._subtitles_facade,
            self._short_video_facade,
            self._audio_facade,
            self._sequence_facade,
            self._workflow_facade,
            self._ai_facade,
            self._updates_facade,
        ):
            facade.bind_project_editor(self._project_editor_controller)
        cache_root = audio_preview_directory()
        self._audio_preview_controller = AudioPreviewController(
            cache_root,
            parent=self,
            # Keep the existing module-level call surface available to tests
            # and callers while the controller owns the operation itself.
            prepare_cache=self._prepare_cache_compat,
            clear_cache=lambda root: clear_audio_preview_cache(root),
        )
        self._audio_facade.bind_audio_preview(self._audio_preview_controller)
        self.audio_preview_cache_root = cache_root
        self._audio_preview_controller.cacheChanged.connect(self.audioPreviewCacheChanged.emit)
        self._audio_preview_controller.previewChannelsChanged.connect(self.audioMixerPreviewChannelsChanged.emit)
        self._audio_preview_controller.previewGainsChanged.connect(self.audioMixerPreviewGainsChanged.emit)
        self._audio_preview_controller.levelsChanged.connect(self.audioPreviewLevelsChanged.emit)
        self._audio_preview_controller.masterMetricsChanged.connect(self.audioMasterMetricsChanged.emit)
        self._audio_preview_controller.projectDataChanged.connect(self.projectDataChanged.emit)
        self._audio_preview_controller.statusChanged.connect(self._set_status)
        self._audio_preview_controller.cacheCompleted.connect(self.audioPreviewCacheCompleted.emit)
        self._audio_preview_controller.set_project(self._project)
        self.autosaveCompleted.connect(self._finish_autosave)

        self.updateCheckFinished.connect(self._on_update_check_finished, Qt.ConnectionType.QueuedConnection)
        self._codex_proposal: dict[str, object] | None = None
        self._audio_mix_proposal: dict[str, object] | None = None
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
        self._ai_facade.bind_services(
            AIServices(
                codex_session=self._codex_session,
                audio_mix_session=self._codex_audio_mix_session,
                codex_chat=self._codex_chat,
                chat_router=self._ai_chat,
                actions=self._codex_actions,
            )
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
    def workspacePlayerState(self) -> WorkspacePlayerPayload:
        return self._workspace_facade.workspacePlayerState

    @Property("QVariantMap", notify=workspacePlayerStateChanged)
    def workspacePlayerStates(self) -> dict[str, WorkspacePlayerPayload]:
        return self._workspace_facade.workspacePlayerStates

    @Property(str, notify=editorModeChanged)
    def currentEditMode(self) -> str:
        return self._workspace_facade.currentEditMode

    @Property("QVariantMap", notify=editorCapabilitiesChanged)
    def editorModeCapabilities(self) -> dict[str, object]:
        return self._workspace_facade.editorModeCapabilities

    @Property("QVariantMap", notify=editorPlayheadChanged)
    def editorPlayhead(self) -> dict[str, object]:
        return self._workspace_facade.editorPlayhead

    @Property("QVariantMap", notify=cutTimelineChanged)
    def cutTimeline(self) -> VideoTimelineView:
        return self._workspace_facade.cutTimeline

    @Property(float, notify=cutTimelineChanged)
    def cutOutputDuration(self) -> float:
        return self._workspace_facade.cutOutputDuration

    @Property(bool, notify=lastProcessDiagnosticChanged)
    def hasLastProcessDiagnostic(self) -> bool:
        return self.workflow._state.last_process_diagnostic is not None

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
    def codexProposal(self) -> dict[str, object]:
        return self._ai_facade.codexProposal

    @Property("QVariantMap", notify=audioMixProposalChanged)
    def audioMixProposal(self) -> dict[str, object]:
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
    def codexModels(self) -> list[dict[str, object]]:
        return self._ai_facade.codexModels

    @Property(str, notify=codexChatChanged)
    def codexSelectedModel(self) -> str:
        return self._ai_facade.codexSelectedModel

    @Property(str, notify=codexChatChanged)
    def codexModelError(self) -> str:
        return self._ai_facade.codexModelError

    @Property("QVariantList", notify=codexChatChanged)
    def codexChatMessages(self) -> list[dict[str, object]]:
        return self._ai_facade.codexChatMessages

    @Property(str, notify=aiChatChanged)
    def aiChatProviderId(self) -> str:
        return self._ai_facade.aiChatProviderId

    @Property(str, notify=aiChatChanged)
    def aiChatProviderName(self) -> str:
        return self._ai_facade.aiChatProviderName

    @Property("QVariantList", notify=aiChatChanged)
    def aiChatProviders(self) -> list[dict[str, object]]:
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
    def subtitleSegments(self) -> list[dict[str, object]]:
        return self._subtitles_facade.subtitleSegments

    @Property("QVariantMap", notify=segmentsChanged)
    def subtitleLayoutMetrics(self) -> dict[str, float | int]:
        return self._subtitles_facade.subtitleLayoutMetrics

    @Property("QVariantList", notify=shortVideoChanged)
    def shortVideoClips(self) -> list[dict[str, object]]:
        return self._short_video_facade.shortVideoClips

    @Property("QVariantMap", notify=shortVideoChanged)
    def shortVideoSettings(self) -> dict[str, object]:
        return self._short_video_facade.shortVideoSettings

    @Property("QVariantList", notify=highlightCandidatesChanged)
    def highlightCandidates(self) -> list[dict[str, object]]:
        return self._short_video_facade.highlightCandidates

    @Property(bool, notify=highlightCandidatesChanged)
    def highlightUndoAvailable(self) -> bool:
        return self._short_video_facade.highlightUndoAvailable

    @Property(str, notify=highlightAnalysisChanged)
    def highlightAnalysisState(self) -> str:
        return self._short_video_facade.highlightAnalysisState

    @Property(float, notify=highlightAnalysisChanged)
    def highlightAnalysisProgress(self) -> float:
        return self._short_video_facade.highlightAnalysisProgress

    @Property(QObject, constant=True)
    def subtitleModel(self) -> QObject:
        return self._subtitles_facade.subtitleModel

    @Property(QObject, constant=True)
    def shortVideoClipModel(self) -> QObject:
        return self._short_video_facade.shortVideoClipModel

    @Property(int, notify=shortVideoClipDataChanged)
    def shortVideoClipCount(self) -> int:
        return self._short_video_facade.shortVideoClipCount

    @Property("QVariantList", constant=True)
    def fontChoices(self) -> list[dict[str, str]]:
        return self._subtitles_facade.fontChoices

    @Property(int, notify=segmentsChanged)
    def segmentCount(self) -> int:
        return self._subtitles_facade.segmentCount

    @staticmethod
    def _subtitle_preview_signature(segment: dict[str, object]) -> tuple[object, ...]:
        return SubtitleFacade._subtitle_preview_signature(segment)

    def _short_video_section(self, *, for_edit: bool = False) -> dict[str, object]:
        return self.shortVideo._short_video_section(for_edit=for_edit)

    @Property("QVariantList", notify=projectDataChanged)
    def projectSpeakers(self) -> list[dict[str, object]]:
        return self._subtitles_facade.projectSpeakers

    @Property("QVariantList", notify=projectDataChanged)
    def subtitleWaveforms(self) -> list[dict[str, object]]:
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

    @Property("QVariantList", notify=projectDataChanged)
    def audioMixerChannels(self) -> list[dict[str, object]]:
        return self._audio_facade.audioMixerChannels

    @Property(bool, notify=projectDataChanged)
    def audioMixerAvailable(self) -> bool:
        return self._audio_facade.audioMixerAvailable

    @Property(bool, notify=projectDataChanged)
    def audioMixerPreviewComplete(self) -> bool:
        return self._audio_facade.audioMixerPreviewComplete

    @Property(bool, notify=projectDataChanged)
    def audioMixerIntentionalSilence(self) -> bool:
        return self._audio_facade.audioMixerIntentionalSilence

    @Property("QVariantList", notify=projectDataChanged)
    def audioMixerSequenceChannels(self) -> list[dict[str, object]]:
        return self._audio_facade.audioMixerSequenceChannels

    @Property("QVariantList", notify=audioMixerPreviewChannelsChanged)
    def audioMixerPreviewChannels(self) -> list[dict[str, object]]:
        return self._audio_facade.audioMixerPreviewChannels

    @Property("QVariantMap", notify=audioMixerPreviewGainsChanged)
    def audioMixerPreviewGains(self) -> dict[str, float]:
        return self._audio_facade.audioMixerPreviewGains

    @staticmethod
    def _audio_buffer_peak(buffer: QAudioBuffer) -> float:
        return AudioFacade._audio_buffer_peak(buffer)

    @Property("QVariantMap", notify=audioPreviewLevelsChanged)
    def audioPreviewLevels(self) -> dict[str, float]:
        return self._audio_facade.audioPreviewLevels

    @Property(float, notify=audioMasterMetricsChanged)
    def audioMasterLevel(self) -> float:
        return self._audio_facade.audioMasterLevel

    @Property(float, notify=audioMasterMetricsChanged)
    def audioLimiterReductionDb(self) -> float:
        return self._audio_facade.audioLimiterReductionDb

    @Property(float, notify=segmentsChanged)
    def projectDuration(self) -> float:
        if self._project is None:
            return 0.0
        video = self._project.get("video")
        segments = self._project.get("segments")
        video_duration = coerce_float(video.get("duration_seconds", 0.0)) if is_string_object_dict(video) else 0.0
        segment_duration = (
            max(
                (coerce_float(item["end"]) for item in segments),
                default=0.0,
            )
            if is_string_object_dict_list(segments)
            else 0.0
        )
        return max(video_duration, segment_duration)

    @Property("QVariantMap", notify=sequenceChanged)
    def sequenceView(self) -> SequenceViewPayload:
        return self._sequence_facade.sequenceView

    @Property("QVariantList", notify=sequenceChanged)
    def mediaBinAssets(self) -> list[MediaBinAssetView]:
        return self._sequence_facade.mediaBinAssets

    @Property("QVariantList", notify=sequenceChanged)
    def sequenceClips(self) -> list[SequenceClipView]:
        return self._sequence_facade.sequenceClips

    @Property(float, notify=sequenceChanged)
    def sequenceOutputDuration(self) -> float:
        return self._sequence_facade.sequenceOutputDuration

    @Property("QVariantMap", notify=sequenceChanged)
    def sequencePlayhead(self) -> SequencePlayheadView:
        return self._sequence_facade.sequencePlayhead

    @Property(str, notify=sequenceChanged)
    def sequenceError(self) -> str:
        return self._sequence_facade.sequenceError

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
    def progressSteps(self) -> list[dict[str, object]]:
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

    def _source_selection_updated(self, update: SourceSelectionUpdate, previous_alignment: AlignmentResult) -> None:
        previous = update.previous
        selection = update.current
        if previous is None or selection is None:
            return
        if self._loading_project_sources or self._project is None:
            return
        media_changed = update.media_changed
        if (
            not self._relinking_project_sources
            and media_changed
            and not self._project_source_selection_matches(selection)
        ):
            if not self._clear_project():
                self._restore_source_selection_after_failed_save(previous, previous_alignment)
        elif previous.output_dir != selection.output_dir:
            self._project["output_dir"] = selection.output_dir
            self._mark_project_dirty()
            self.projectDataChanged.emit()

    def _restore_source_selection_after_failed_save(
        self, selection: SourceSelection, alignment_result: AlignmentResult
    ) -> None:
        error_status = self.status
        was_loading_project_sources = self._loading_project_sources
        self._loading_project_sources = True
        try:
            self._set_source_selection(selection)
        finally:
            self._loading_project_sources = was_loading_project_sources
        self._alignment_result = dict(alignment_result)
        self.alignmentChanged.emit()
        if self._project is not None and self._project.get("output_dir") != selection.output_dir:
            self._project["output_dir"] = selection.output_dir
            self.projectDataChanged.emit()
        self._set_status(error_status, "ERROR")

    def _normalized_source_path(self, value: str) -> str:
        if not value:
            return ""
        return str(Path(value).resolve()).casefold()

    def _project_source_selection_matches(self, selection: SourceSelection) -> bool:
        if self._project is None:
            return False
        video = self._project.get("video")
        audio_sources = self._project.get("audio_sources")
        if not is_string_object_dict(video) or not is_string_object_dict_list(audio_sources):
            return False
        project_video = self._normalized_source_path(str(video.get("path", "")))
        selected_video = self._normalized_source_path(selection.video)
        project_audio = {
            self._normalized_source_path(str(item.get("path", ""))) for item in audio_sources if item.get("path")
        }
        selected_audio = {self._normalized_source_path(path) for path in selection.audio_files}
        return selected_video == project_video and selected_audio == project_audio

    @Slot()
    def beginSourceRelink(self) -> None:
        if self._running:
            return
        self._relink_source_selection = self._source_selection
        self._relink_alignment_result = dict(self._alignment_result)
        self._relinking_project_sources = True

    @Slot(result=bool)
    def completeSourceRelink(self) -> bool:
        """素材設定の完了時に選択済み素材を現在のプロジェクトへ反映する。"""
        if self._running:
            return False
        previous = self._relink_source_selection
        if not self._source_selection.video and previous is not None and (
            previous.video == self._source_selection.video
            and previous.audio_files == self._source_selection.audio_files
        ):
            return True
        if self._project is None or self._project_source_selection_matches(self._source_selection):
            return True
        self.relinkProjectSources()
        return self._project is not None and self._project_source_selection_matches(self._source_selection)

    @Slot()
    def finishSourceRelink(self) -> None:
        if self._running or not self._relinking_project_sources:
            return
        try:
            previous = self._relink_source_selection
            if (
                self._project is not None
                and previous is not None
                and (
                    previous.video != self._source_selection.video
                    or previous.audio_files != self._source_selection.audio_files
                )
                and not self._project_source_selection_matches(self._source_selection)
            ):
                # × / Escape は未適用の動画・音声変更だけを取り消す。
                # 出力先の変更は独立したプロジェクト設定として保持する。
                self._set_source_selection(
                    replace(previous, output_dir=self._source_selection.output_dir)
                )
                self._alignment_result = dict(
                    self._relink_alignment_result or self._empty_alignment_result()
                )
                self.alignmentChanged.emit()
                self._set_status("未適用の動画・音声の変更を取り消しました", "READY")
        finally:
            self._relinking_project_sources = False
            self._relink_source_selection = None
            self._relink_alignment_result = None

    @Slot()
    def relinkProjectSources(self) -> None:
        if self._running or self._project is None:
            return

        old_project = deepcopy(self._project)
        audio_sources_value = self._project.get("audio_sources")
        speakers_value = self._project.get("speakers")
        previous_sources = (
            [dict(item) for item in audio_sources_value] if is_string_object_dict_list(audio_sources_value) else []
        )
        previous_speakers = (
            [dict(item) for item in speakers_value] if is_string_object_dict_list(speakers_value) else []
        )

        source_entries = build_speaker_entries_from_files(
            self._source_selection.audio_files,
            self.color_config_path,
        )
        project_video = self._project.get("video", {})
        selected_video = str(Path(self._source_selection.video).resolve()) if self._source_selection.video else ""
        selected_output = (
            str(Path(self._source_selection.output_dir).resolve()) if self._source_selection.output_dir else ""
        )

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
        if not project_sequence.is_legacy_single_video() and project_video_path != self._normalized_source_path(
            selected_video
        ):
            self._set_status(
                "複数clipのsequenceは単一動画のrelinkでは変更できません",
                "CHECK",
            )
            return

        def _match(items: list[dict[str, object]], **conditions: str) -> dict[str, object] | None:
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
        new_audio_sources: list[dict[str, object]] = []
        new_speakers: list[dict[str, object]] = []

        for source in source_entries:
            previous_source = (
                _match(unmatched_audio_sources, path=source["path"])
                or _match(unmatched_audio_sources, file_name=source["file_name"])
                or _match(unmatched_audio_sources, file_name=str(Path(source["path"]).name))
            )
            source_payload: dict[str, object] = {**previous_source} if previous_source else {}
            source_payload["path"] = source["path"]
            source_payload.setdefault("file_name", source["file_name"])
            source_payload.setdefault("track_key", source["track_key"])
            new_audio_sources.append(source_payload)

            previous_speaker = (
                _match(unmatched_speakers, path=source["path"])
                or _match(unmatched_speakers, file_name=source["file_name"])
                or _match(unmatched_speakers, name=source["name"])
            )
            speaker_payload: dict[str, object]
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
            **(project_video if is_string_object_dict(project_video) else {}),
            "path": selected_video,
        }
        self._project["output_dir"] = selected_output
        self._project["audio_sources"] = new_audio_sources
        self._project["speakers"] = new_speakers

        if "video" in self._project and isinstance(self._project["video"], dict):
            self._project["video"]["duration_seconds"] = float(self._project["video"].get("duration_seconds", 0.0))

        if project_sequence.is_legacy_single_video():
            try:
                self._project["sequence"] = project_sequence.sync_legacy_video(self._project["video"]).to_json()
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
            if not is_string_object_dict(project):
                raise SubtitleProjectError("プロジェクトのキーが不正です")
            self._require_project_editor().save_new_project(project_path, project)
        except (OSError, SubtitleProjectError, TypeError, ValueError) as error:
            self._set_status(f"空の編集プロジェクトを保存できません: {error}", "ERROR")
            return False
        loaded = self._load_project_path(project_path, update_sources=False)
        if loaded:
            self._set_status("空の編集プロジェクトを作成しました。字幕を手動追加できます", "EDIT")
        return loaded

    def _clear_project(self) -> bool:
        if self._project_dirty and not self.saveProject():
            return False
        self.autosave_timer.stop()
        if hasattr(self, "_codex_audio_mix_session"):
            was_audio_proposal_running = self._codex_audio_mix_session.running
            self._codex_audio_mix_session.stop()
            if was_audio_proposal_running:
                self._codex_chat.fail_proposal("", cancelled=True)
        self._audio_mix_proposal = None
        if hasattr(self, "audioMixProposalChanged"):
            self.audioMixProposalChanged.emit()
        self._reset_transcription_integration_state()
        self._require_project_editor().clear(emit=False)
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
        return True

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
        if not self._loading_project_sources and self._project is not None and not self._clear_project():
            return
        super().resetSources()

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
            None,
            "編集プロジェクトの保存先",
            str(default_path or self.workspace_root),
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
            self._require_project_editor().save_as(target, emit=False)
        except (OSError, SubtitleProjectError, TypeError, ValueError) as error:
            self._set_status(f"プロジェクトを保存できません: {error}", "ERROR")
            return False
        self.projectChanged.emit()
        self._set_status("別の場所に編集プロジェクトを保存しました", "SAVED")
        return True

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
    def loadProjectWithSelectedSources(self, path: str, source_selection: dict[str, object]) -> bool:
        """Load an existing project while keeping the sources chosen for the next transcription."""
        if self._running:
            self._set_status("処理中は編集プロジェクトを変更できません", "BUSY")
            return False
        if self._project_dirty:
            if not self._project_path or not self.saveProject():
                return False

        candidate = self._local_path(path)
        audio_files = source_selection.get("audio_files", [])
        if not is_object_list(audio_files):
            self._set_status("音声ファイルの選択を確認してください", "CHECK")
            return False
        selected_sources = SourceSelection(
            video=str(source_selection.get("video", "")),
            output_dir=str(source_selection.get("output_dir", "")),
            audio_files=tuple(str(item) for item in audio_files),
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

    def _apply_project_subtitle_settings(self, project: Mapping[str, object]) -> None:
        subtitle = project.get("subtitle_settings", {})
        updates: dict[str, int | float | str] = {}
        if is_string_object_mapping(subtitle):
            for project_key, setting_key, converter in (
                ("font_size", "subtitle_font_size", coerce_int),
                ("outline_color", "subtitle_outline_color", normalize_rgb_color),
                ("outline_thickness", "subtitle_outline_thickness", coerce_int),
                ("volume_scale_percent", "subtitle_volume_scale_percent", coerce_float),
                ("max_gap_seconds", "subtitle_max_gap_seconds", coerce_float),
                ("end_padding_seconds", "subtitle_end_padding_seconds", coerce_float),
                ("min_duration_seconds", "subtitle_min_duration_seconds", coerce_float),
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
            for project_key, setting_key, render_converter in (
                ("video_codec", "video_codec", str),
                ("audio_normalize", "audio_normalize", bool),
                ("audio_target_lufs", "audio_target_lufs", coerce_float),
                ("cut_no_speech", "cut_no_speech", bool),
                ("no_speech_min_seconds", "no_speech_min_seconds", coerce_float),
                ("speech_padding_seconds", "speech_padding_seconds", coerce_float),
                ("speech_threshold_db", "speech_threshold_db", str),
                ("speech_min_clip_seconds", "speech_min_clip_seconds", coerce_float),
                ("nvenc_cq", "nvenc_cq", coerce_int),
                ("x264_crf", "x264_crf", coerce_int),
            ):
                if project_key not in render_settings:
                    continue
                value = render_settings[project_key]
                if render_converter is bool:
                    if not isinstance(value, bool):
                        continue
                elif render_converter is str:
                    if not isinstance(value, str):
                        continue
                    value = value
                else:
                    try:
                        value = render_converter(value)
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
            project = self._require_project_editor().load(path)
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
        try:
            ensure_transcription_context_base_dir(project, path)
        except TypeError:
            self._set_status("プロジェクトの文字起こし設定が不正です", "ERROR")
            return False
        self._apply_project_subtitle_settings(project)
        self._audio_preview_controller.set_project(project)
        self._reset_audio_preview_cache()
        self._reset_editor_timing()
        self._sync_project_timeline()
        self._loading_project_sources = True
        try:
            selection = replace(self._source_selection, output_dir=str(project.get("output_dir", "")))
            if update_sources:
                video_data = project.get("video")
                audio_data = project.get("audio_sources")
                if not is_string_object_dict(video_data) or not is_string_object_dict_list(audio_data):
                    self._set_status("プロジェクトの素材設定が不正です", "ERROR")
                    return False
                video = Path(str(video_data.get("path", "")))
                audio_files = [str(item.get("path", "")) for item in audio_data]
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
        segments = project.get("segments")
        if not is_string_object_dict_list(segments):
            self._set_status("プロジェクトの字幕セグメントが不正です", "ERROR")
            return False
        self._set_status(f"編集プロジェクトを開きました（字幕 {len(segments)} 件）", "EDIT")
        return True

    @Slot(result=bool)
    def saveProject(self) -> bool:
        if self._running:
            self._set_status("処理中は編集プロジェクトを保存できません", "BUSY")
            return False
        if self._project is None or not self._project_path:
            self._set_status("保存する字幕編集プロジェクトがありません", "CHECK")
            return False
        self.autosave_timer.stop()
        try:
            self._require_project_editor().save(emit=False)
        except (OSError, SubtitleProjectError, TypeError, ValueError) as error:
            self._set_status(f"プロジェクトを保存できません: {error}", "ERROR")
            return False
        self._sync_subtitle_model()
        self.projectChanged.emit()
        self._set_status("字幕編集を保存しました", "SAVED")
        return True

    def _sync_autosave_with_processing(self) -> None:
        if self._running:
            self.autosave_timer.stop()
        elif self.projectDirty:
            self.autosave_timer.start()

    def _autosave_project(self) -> None:
        if self._running:
            return
        self._require_project_editor().autosave()

    @Slot(int, str, str)
    def _finish_autosave(self, revision: int, path: str, error: str) -> None:
        ignored = (revision, path) in self._ignored_autosaves
        self._require_project_editor().finish_autosave(revision, path, error)
        if error and not ignored:
            self._set_status(f"保存に失敗しました: {error}", "ERROR")

    def _wait_for_autosave(self) -> None:
        self._require_project_editor().wait_for_autosave()

    def _shutdown_executor(self) -> None:
        if hasattr(self, "autosave_timer"):
            self.autosave_timer.stop()
        if self._project_dirty and self._project_path:
            self.saveProject()
        project_editor = self._existing_project_editor()
        if project_editor is not None:
            project_editor.shutdown()
        controller = self._existing_audio_preview()
        if controller is not None:
            controller.shutdown()
        super()._shutdown_executor()

    def _update_project_settings(self, settings: dict[str, object]) -> None:
        if self._project is None:
            return
        subtitle_value = self._project.get("subtitle_settings")
        subtitle = subtitle_value if is_string_object_mapping(subtitle_value) else {}
        self._project["subtitle_settings"] = {
            **subtitle,
            "font_size": coerce_int(settings.get("subtitle_font_size", subtitle.get("font_size", 50))),
            "outline_color": normalize_rgb_color(
                settings.get("subtitle_outline_color", subtitle.get("outline_color", "#000000"))
            ),
            "outline_thickness": max(
                0, min(20, coerce_int(settings.get("subtitle_outline_thickness", subtitle.get("outline_thickness", 3))))
            ),
            "volume_scale_percent": coerce_float(
                settings.get("subtitle_volume_scale_percent", subtitle.get("volume_scale_percent", 20.0))
            ),
            "max_gap_seconds": coerce_float(
                settings.get("subtitle_max_gap_seconds", subtitle.get("max_gap_seconds", 0.32))
            ),
            "end_padding_seconds": coerce_float(
                settings.get("subtitle_end_padding_seconds", subtitle.get("end_padding_seconds", 0.08))
            ),
            "min_duration_seconds": coerce_float(
                settings.get("subtitle_min_duration_seconds", subtitle.get("min_duration_seconds", 0.35))
            ),
        }
        self._mark_project_dirty()

    @Property("QVariantMap", notify=actionCapabilitiesChanged)
    def actionCapabilities(self) -> dict[str, object]:
        return self._workflow_facade.actionCapabilities

    @staticmethod
    def _is_audio_mix_chat_request(message: str) -> bool:
        # Route natural-language sound adjustments to the typed audio proposal.
        return AIChatFacade._is_audio_mix_chat_request(message)

    def _record_dependency_snapshot(self, *, stage: str) -> None:
        status = self._dependencies.to_dict()
        fields = ", ".join(
            f"{key}={str(status[key]).lower() if isinstance(status[key], bool) else status[key]}"
            for key in ("ffmpeg", "ffprobe", "whisperx", "cuda", "nvenc", "ready")
        )
        missing_value = status.get("missing")
        missing = ",".join(str(item) for item in missing_value) if is_object_list(missing_value) else ""
        missing = missing or "none"
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
                f"セッションログファイルへ書き込めません: {self._application_logger.write_error}",
                severity="WARNING",
                component="startup",
                stage="STARTUP",
            )

    @Slot()
    def refreshDependencies(self) -> None:
        super().refreshDependencies()
        self._record_dependency_snapshot(stage="DEPENDENCY_CHECK")

    def _set_status(self, status: str, stage: str) -> None:
        previous_status = self._initial_value("_status")
        previous_stage = self._initial_value("_stage")
        previous = (
            previous_status if isinstance(previous_status, str) else "",
            previous_stage if isinstance(previous_stage, str) else "",
        )
        super()._set_status(status, stage)
        if not hasattr(self, "_application_logger") or previous == (status, stage):
            return
        severity = "ERROR" if stage == "ERROR" else "WARNING" if stage in {"SETUP", "CHECK", "CANCELLED"} else "INFO"
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
            transcription = (self._project or {}).get("transcription")
            work_dir = transcription.get("work_dir") if is_string_object_dict(transcription) else None
            output_directory = str(work_dir or self.videoOutputDirectory).strip()
        if not output_directory:
            return ""
        transcript_directory = Path(output_directory) / "transcripts"
        if not transcript_directory.is_dir():
            return ""
        try:

            def log_mtime(path: Path) -> int:
                return path.stat().st_mtime_ns

            candidates = sorted(
                transcript_directory.glob("*.whisperx.log"),
                key=log_mtime,
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
        self.workflow._state.last_process_diagnostic = ProcessDiagnosticSnapshot(
            occurred_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            job=job,
            component=job or "process",
            stage=self.stage,
            status=self.status,
            outcome=outcome,
            exit_code=exit_code,
            process_error=self.workflow._state.pending_process_error,
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
        if self.workflow._state.last_process_diagnostic is not None:
            self.clipboard().setText(
                self._application_logger.diagnostic_text(
                    snapshot=self.workflow._state.last_process_diagnostic,
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
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._application_logger.log_directory))):
            self._set_status("ログ保存先を開けませんでした", "ERROR")


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
        Path(smoke_result_path).write_text(json.dumps(smoke_result, ensure_ascii=False), encoding="utf-8")
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
