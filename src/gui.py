from __future__ import annotations

from array import array
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

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QModelIndex,
    QObject,
    QProcess,
    QStandardPaths,
    QTimer,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QDesktopServices, QFontDatabase
from PySide6.QtMultimedia import QAudioBuffer, QAudioBufferOutput, QAudioFormat
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QFileDialog

from .audio_mixer import (
    AUDIO_MIX_MASTER_GAIN,
    DEFAULT_AUDIO_TRACK,
    MAX_VOLUME_PERCENT,
    active_audio_mix_channels,
    reconcile_audio_mix,
    reset_audio_mix,
)
from .audio_preview_cache import (
    AudioPreviewCacheResult,
    AudioPreviewCacheStats,
    audio_preview_cache_entries,
    audio_preview_cache_stats,
    clear_audio_preview_cache,
    cached_audio_preview_paths,
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
from .application_logging import ApplicationLogger, ProcessDiagnosticSnapshot
from .realtime_audio_mixer import RealtimeAudioMixer
from .color_config import normalize_rgb_color, save_speaker_color
from .gui_base import APP_TITLE, EditBayBackend as LegacyEditBayBackend
from .gui_source_state import SourceSelection, build_speaker_entries_from_files
from .gui_state import (
    build_gui_render_command,
    build_gui_short_video_command,
    build_gui_transcribe_command,
)
from .subtitle_project import (
    MIN_SEGMENT_DURATION_SECONDS,
    SubtitleProjectError,
    assign_project_layout_rows,
    derive_project_path,
    load_project,
    normalize_segment,
    save_project,
)
from .short_video_schema import VALID_FIT_MODES, VALID_TRANSITION_TYPES
from .subtitle_line_count import segment_editor_text, segment_preview_text
from .subtitle_workflow import build_project_ass
from .render_ass import style_name_for_speaker
from .runtime_dependencies import runtime_diagnostic_info
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


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    return f"{size:.1f} {units[index]}"


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
    codexStateChanged = Signal()
    codexMessageChanged = Signal()
    codexProposalChanged = Signal()
    codexCallbackRequested = Signal(object)
    highlightCandidatesChanged = Signal()
    highlightAnalysisChanged = Signal()

    def __init__(self, argv: list[str], workspace_root: Path | None = None) -> None:
        self._project: dict[str, Any] | None = None
        self._project_path = ""
        self._project_dirty = False
        self._undo_stack: list[dict[str, Any]] = []
        self._redo_stack: list[dict[str, Any]] = []
        self._selected_segment_index = -1
        self._active_job = ""
        self._ass_path = ""
        self._loading_project_sources = False
        self._relinking_project_sources = False
        self._relink_source_selection: SourceSelection | None = None
        super().__init__(argv, workspace_root=workspace_root)
        self._application_logger = ApplicationLogger(
            self.workspace_root,
            application_info=self._application_info,
        )
        self._last_process_diagnostic: ProcessDiagnosticSnapshot | None = None
        self._pending_process_error = ""
        self._log = self._application_logger.text
        self._font_choices = build_font_choices(QFontDatabase.families())
        self._subtitle_model = SubtitleListModel(self)
        self._segment_starts: list[float] = []
        self._segment_prefix_max_end: list[float] = []
        self._project_revision = 0
        self._autosave_future: Future[Path] | None = None
        self._autosave_revision = -1
        self._autosave_path = ""
        self._autosave_pending = False
        self._ignored_autosaves: set[tuple[int, str]] = set()
        self._autosave_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="project-save")
        cache_location = os.environ.get("LOCALAPPDATA") or QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.GenericCacheLocation
        )
        self.audio_preview_cache_root = (
            Path(cache_location) / "Subtitle Edit Bay" / "audio-preview"
        )
        self._audio_preview_cache_paths: dict[str, str] = {}
        self._audio_preview_cache_future: Future[AudioPreviewCacheResult] | None = None
        self._audio_preview_cache_request = 0
        self._audio_preview_preparing = False
        self._audio_preview_cache_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="audio-preview",
        )
        self._audio_preview_outputs: dict[str, QAudioBufferOutput] = {}
        self._audio_master_mixer = RealtimeAudioMixer(self)
        self._audio_master_level = 0.0
        self._audio_limiter_reduction_db = 0.0
        self._audio_master_mixer.metricsChanged.connect(self._update_audio_master_metrics)
        self._highlight_candidates: list[dict[str, Any]] = []
        self._highlight_rejected: list[dict[str, Any]] = []
        self._highlight_status = "idle"
        self._highlight_progress = 0.0
        self._highlight_cancel = threading.Event()
        self._highlight_generation = 0
        self._audio_preview_gains: dict[str, float] = {}
        self._audio_preview_levels: dict[str, float] = {}
        self._audio_preview_pending_levels: dict[str, float] = {}
        self._audio_preview_level_timer = QTimer(self)
        self._audio_preview_level_timer.setInterval(33)
        self._audio_preview_level_timer.timeout.connect(self._publish_audio_preview_levels)
        self.audioPreviewCacheCompleted.connect(self._apply_audio_preview_cache)
        self.autosaveCompleted.connect(self._finish_autosave)
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(700)
        self.autosave_timer.timeout.connect(self._autosave_project)

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
        self.updateDownloadProgressEvent.connect(self._on_update_download_progress, Qt.ConnectionType.QueuedConnection)
        self.updateDownloadFinished.connect(self._on_update_download_finished, Qt.ConnectionType.QueuedConnection)

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

    @Property("QStringList", constant=True)
    def codexScopes(self) -> list[str]:
        return list(CODEX_SCOPES)

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

    @Property("QVariantList", notify=shortVideoChanged)
    def shortVideoClips(self) -> list[dict[str, Any]]:
        if self._project is None:
            return []
        section = self._short_video_section()
        clips = section.get("clips", [])
        return [self._build_short_video_clip_view(clip, index) for index, clip in enumerate(clips)]

    @Property("QVariantMap", notify=shortVideoChanged)
    def shortVideoSettings(self) -> dict[str, Any]:
        if self._project is None:
            return {}
        section = self._short_video_section()
        return {
            "enabled": bool(section.get("enabled", False)),
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

    @Property(str, notify=highlightAnalysisChanged)
    def highlightAnalysisState(self) -> str:
        return self._highlight_status

    @Property(float, notify=highlightAnalysisChanged)
    def highlightAnalysisProgress(self) -> float:
        return self._highlight_progress

    @Property(QObject, constant=True)
    def subtitleModel(self) -> QObject:
        return self._subtitle_model

    @Property("QVariantList", constant=True)
    def fontChoices(self) -> list[dict[str, str]]:
        return deepcopy(self._font_choices)

    @Property(int, notify=segmentsChanged)
    def segmentCount(self) -> int:
        return len(self._project.get("segments", [])) if self._project else 0

    def _sync_subtitle_model(self) -> None:
        segments = self._project.get("segments", []) if self._project else []
        self._subtitle_model.set_segments(segments)
        self._segment_starts = [float(item["start"]) for item in segments]
        prefix: list[float] = []
        max_end = 0.0
        for segment in segments:
            max_end = max(max_end, float(segment["end"]))
            prefix.append(max_end)
        self._segment_prefix_max_end = prefix

    @staticmethod
    def _segment_view(segment: dict[str, Any], source_index: int | None = None) -> dict[str, Any]:
        view = {
            "id": str(segment["id"]),
            "start": float(segment["start"]),
            "end": float(segment["end"]),
            "text": str(segment.get("text", "")),
            "preview_text": segment_preview_text(segment),
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
        if self._project is None:
            return None
        for segment in self._project.get("segments", []):
            if str(segment.get("id", "")) == segment_id:
                return segment
        return None

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
            "preview_text": segment_preview_text(segment) if segment else str(clip.get("text", "")),
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
            if segment is None:
                return False
            try:
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
        self._highlight_rejected = []
        self._highlight_status = "running"
        self._highlight_progress = 0.0
        self.highlightAnalysisChanged.emit()
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

    @Property(str, notify=audioPreviewCacheChanged)
    def audioPreviewCacheSummary(self) -> str:
        stats: AudioPreviewCacheStats = audio_preview_cache_stats(self.audio_preview_cache_root)
        return f"{_format_bytes(stats.total_bytes)} / {_format_bytes(stats.max_bytes)}"

    @Property(str, notify=audioMixerPreviewChannelsChanged)
    def audioPreviewClockUrl(self) -> str:
        if self._project is None:
            return ""
        channels = self._project.get("audio_mix", {}).get("channels", [])
        ordered = sorted(
            (channel for channel in channels if isinstance(channel, dict)),
            key=lambda channel: channel.get("kind") == "external",
        )
        for channel in ordered:
            cache_path = self._audio_preview_cache_paths.get(
                str(channel.get("id", "")),
                "",
            )
            if cache_path and Path(cache_path).is_file():
                return QUrl.fromLocalFile(cache_path).toString()
        return ""

    def _reset_audio_preview_cache(self) -> None:
        self._audio_master_mixer.stop()
        self._audio_preview_cache_request += 1
        self._audio_preview_cache_paths = {}
        self._audio_preview_preparing = False
        future = self._audio_preview_cache_future
        if future is not None and not future.done():
            future.cancel()
        self._audio_preview_cache_future = None
        self.audioPreviewCacheChanged.emit()
        self._notify_audio_mixer_preview(structure_changed=True)

    @Slot()
    def prepareAudioMixerPreview(self) -> None:
        if self._project is None:
            return
        entries = audio_preview_cache_entries(
            self._project,
            self.audio_preview_cache_root,
        )
        cached_paths = cached_audio_preview_paths(entries)
        protected_paths = [Path(path) for path in cached_paths.values()]
        required_ids = {entry.channel_id for entry in entries}
        if cached_paths != self._audio_preview_cache_paths:
            self._audio_preview_cache_paths = cached_paths
            self._notify_audio_mixer_preview(structure_changed=True)
            self.projectDataChanged.emit()
        if required_ids.issubset(cached_paths):
            if self._audio_preview_preparing:
                self._audio_preview_preparing = False
                self.audioPreviewCacheChanged.emit()
            return
        if not self._dependencies.ffmpeg:
            self._audio_preview_preparing = False
            self.audioPreviewCacheChanged.emit()
            self._set_status("音声プレビューの準備にはFFmpegが必要です", "SETUP")
            return
        future = self._audio_preview_cache_future
        if future is not None and not future.done():
            return

        self._audio_preview_cache_request += 1
        request_id = self._audio_preview_cache_request
        project_snapshot = deepcopy(self._project)
        cache_root = self.audio_preview_cache_root
        self._audio_preview_preparing = True
        self.audioPreviewCacheChanged.emit()
        self.projectDataChanged.emit()
        future = self._audio_preview_cache_executor.submit(
            prepare_audio_preview_cache,
            project_snapshot,
            cache_root,
            protected_paths=protected_paths,
        )
        self._audio_preview_cache_future = future

        def report_completion(done: Future[AudioPreviewCacheResult]) -> None:
            try:
                result = done.result()
            except Exception as error:
                result = AudioPreviewCacheResult({}, (str(error),))
            self.audioPreviewCacheCompleted.emit(request_id, result)

        future.add_done_callback(report_completion)

    @Slot(int, object)
    def _apply_audio_preview_cache(
        self,
        request_id: int,
        result: AudioPreviewCacheResult,
    ) -> None:
        if request_id != self._audio_preview_cache_request or self._project is None:
            return
        self._audio_preview_cache_future = None
        self._audio_preview_cache_paths = {
            channel_id: path
            for channel_id, path in result.paths.items()
            if Path(path).is_file()
        }
        self._audio_preview_preparing = False
        self.audioPreviewCacheChanged.emit()
        self._notify_audio_mixer_preview(structure_changed=True)
        self.projectDataChanged.emit()
        if result.errors:
            self._set_status(
                "音声プレビューの準備に失敗しました: " + "; ".join(result.errors),
                "CHECK",
            )
        else:
            self._set_status("音声プレビューの準備が完了しました", "MIX")

    @Slot()
    def clearAudioPreviewCache(self) -> None:
        self.stopAudioMixerPreview()
        self._audio_preview_cache_request += 1
        self._audio_preview_cache_paths.clear()
        self._audio_preview_preparing = False
        future = self._audio_preview_cache_future
        if future is not None and not future.done():
            future.cancel()
        self._audio_preview_cache_future = None
        clear_audio_preview_cache(self.audio_preview_cache_root)
        self.audioPreviewCacheChanged.emit()
        self.projectDataChanged.emit()
        self._notify_audio_mixer_preview(structure_changed=True)
        self._set_status("音声プレビューキャッシュをクリアしました", "CHECK")

    @Property("QVariantList", notify=projectDataChanged)
    def audioMixerChannels(self) -> list[dict[str, Any]]:
        if self._project is None:
            return []
        return [
            self._audio_mixer_channel_view(channel)
            for channel in self._project.get("audio_mix", {}).get("channels", [])
        ]

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
        project = self._project or {}
        view = deepcopy(channel)
        is_external = view.get("kind") == "external"
        cache_path = self._audio_preview_cache_paths.get(str(view.get("id", "")), "")
        view["preview_url"] = (
            QUrl.fromLocalFile(cache_path).toString()
            if cache_path and Path(cache_path).is_file()
            else ""
        )
        view["preview_audio_track_index"] = 0
        view["preview_offset_seconds"] = (
            float(project.get("transcription", {}).get("offset_seconds", 0.0))
            if is_external
            else 0.0
        )
        return view

    def _audio_mixer_preview_state(
        self,
    ) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], dict[str, float]]:
        if self._project is None:
            self._audio_preview_gains = {}
            return [], {}
        audio_mix = self._project.get("audio_mix", {})
        available = [
            (channel, self._audio_mixer_channel_view(channel))
            for channel in audio_mix.get("channels", [])
            if isinstance(channel, dict) and bool(channel.get("enabled"))
        ]
        available = [item for item in available if item[1]["preview_url"]]
        active_ids = {
            str(channel.get("id", ""))
            for channel in active_audio_mix_channels(audio_mix)
        }
        gains = {
            str(view.get("id", "")): (
                min(
                    MAX_VOLUME_PERCENT / 100.0,
                    max(0.0, float(channel.get("volume_percent", 100.0)))
                    / 100.0
                    * AUDIO_MIX_MASTER_GAIN,
                )
                if str(view.get("id", "")) in active_ids
                else 0.0
            )
            for channel, view in available
        }
        self._audio_preview_gains = gains
        self._audio_master_mixer.set_channels(
            gains,
            {
                str(view.get("id", "")): float(view.get("preview_offset_seconds", 0.0))
                for _channel, view in available
            },
        )
        if any(
            channel_id not in gains or gains.get(channel_id, 0.0) <= 0.0
            for channel_id in self._audio_preview_levels
        ):
            self._audio_preview_level_timer.start()
        return available, gains

    def _notify_audio_mixer_preview(self, *, structure_changed: bool) -> None:
        self._audio_mixer_preview_state()
        if structure_changed:
            self.audioMixerPreviewChannelsChanged.emit()
        self.audioMixerPreviewGainsChanged.emit()

    @Property("QVariantList", notify=audioMixerPreviewChannelsChanged)
    def audioMixerPreviewChannels(self) -> list[dict[str, Any]]:
        available, gains = self._audio_mixer_preview_state()
        channels: list[dict[str, Any]] = []
        for _channel, view in available:
            channel_id = str(view.get("id", ""))
            view["preview_volume"] = gains.get(channel_id, 0.0)
            view["preview_buffer_output"] = self._audio_preview_output(channel_id)
            channels.append(view)
        return channels

    @Property("QVariantMap", notify=audioMixerPreviewGainsChanged)
    def audioMixerPreviewGains(self) -> dict[str, float]:
        _available, gains = self._audio_mixer_preview_state()
        return dict(gains)

    def _audio_preview_output(self, channel_id: str) -> QAudioBufferOutput:
        output = self._audio_preview_outputs.get(channel_id)
        if output is None:
            output = QAudioBufferOutput(self._audio_master_mixer.audio_format, self)
            output.audioBufferReceived.connect(
                lambda buffer, current_id=channel_id: self._receive_audio_preview_buffer(
                    current_id,
                    buffer,
                )
            )
            self._audio_preview_outputs[channel_id] = output
        return output

    @staticmethod
    def _audio_buffer_peak(buffer: QAudioBuffer) -> float:
        if not buffer.isValid() or buffer.byteCount() <= 0:
            return 0.0
        raw = bytes(buffer.constData())
        sample_format = buffer.format().sampleFormat()
        if sample_format == QAudioFormat.UInt8:
            values = (abs(value - 128) / 128.0 for value in raw)
        elif sample_format == QAudioFormat.Int16:
            samples = array("h")
            samples.frombytes(raw[: len(raw) - len(raw) % 2])
            stride = max(1, len(samples) // 4096)
            values = (abs(value) / 32768.0 for value in samples[::stride])
        elif sample_format == QAudioFormat.Int32:
            samples = array("i")
            samples.frombytes(raw[: len(raw) - len(raw) % 4])
            stride = max(1, len(samples) // 4096)
            values = (abs(value) / 2147483648.0 for value in samples[::stride])
        elif sample_format == QAudioFormat.Float:
            samples = array("f")
            samples.frombytes(raw[: len(raw) - len(raw) % 4])
            stride = max(1, len(samples) // 4096)
            values = (
                abs(float(value))
                for value in samples[::stride]
                if math.isfinite(float(value))
            )
        else:
            return 0.0
        return min(1.0, max(values, default=0.0))

    def _receive_audio_preview_buffer(self, channel_id: str, buffer: QAudioBuffer) -> None:
        decoded_peak = self._audio_master_mixer.push_buffer(channel_id, buffer)
        gain = self._audio_preview_gains.get(channel_id, 0.0)
        peak = decoded_peak * gain
        self._audio_preview_pending_levels[channel_id] = max(
            peak,
            self._audio_preview_pending_levels.get(channel_id, 0.0),
        )
        if not self._audio_preview_level_timer.isActive():
            self._audio_preview_level_timer.start()

    def _publish_audio_preview_levels(self) -> None:
        channel_ids = (
            set(self._audio_preview_levels)
            | set(self._audio_preview_pending_levels)
            | set(self._audio_preview_gains)
        )
        levels: dict[str, float] = {}
        for channel_id in channel_ids:
            target = (
                self._audio_preview_pending_levels.pop(channel_id, 0.0)
                if channel_id in self._audio_preview_gains
                else 0.0
            )
            level = max(target, self._audio_preview_levels.get(channel_id, 0.0) * 0.68)
            levels[channel_id] = 0.0 if level < 0.002 else round(min(1.0, level), 4)
        if levels != self._audio_preview_levels:
            self._audio_preview_levels = levels
            self.audioPreviewLevelsChanged.emit()
        if not self._audio_preview_pending_levels and not any(levels.values()):
            self._audio_preview_level_timer.stop()

    @Property("QVariantMap", notify=audioPreviewLevelsChanged)
    def audioPreviewLevels(self) -> dict[str, float]:
        return dict(self._audio_preview_levels)

    @Slot(float, float)
    def _update_audio_master_metrics(self, level: float, reduction_db: float) -> None:
        next_level = round(max(0.0, min(1.0, float(level))), 4)
        next_reduction = round(max(0.0, float(reduction_db)), 2)
        if (
            next_level == self._audio_master_level
            and next_reduction == self._audio_limiter_reduction_db
        ):
            return
        self._audio_master_level = next_level
        self._audio_limiter_reduction_db = next_reduction
        self.audioMasterMetricsChanged.emit()

    @Property(float, notify=audioMasterMetricsChanged)
    def audioMasterLevel(self) -> float:
        return self._audio_master_level

    @Property(float, notify=audioMasterMetricsChanged)
    def audioLimiterReductionDb(self) -> float:
        return self._audio_limiter_reduction_db

    @Slot(int)
    def startAudioMixerPreview(self, position_milliseconds: int) -> None:
        self._audio_mixer_preview_state()
        self._audio_master_mixer.play(position_milliseconds)

    @Slot()
    def pauseAudioMixerPreview(self) -> None:
        self._audio_master_mixer.stop()

    @Slot(int, bool)
    def seekAudioMixerPreview(self, position_milliseconds: int, playing: bool) -> None:
        self._audio_mixer_preview_state()
        self._audio_master_mixer.seek(position_milliseconds, playing)

    @Slot()
    def stopAudioMixerPreview(self) -> None:
        self._audio_master_mixer.stop()

    @Property(float, notify=segmentsChanged)
    def projectDuration(self) -> float:
        if self._project is None:
            return 0.0
        video_duration = float(self._project.get("video", {}).get("duration_seconds", 0.0))
        segment_duration = max((float(item["end"]) for item in self._project.get("segments", [])), default=0.0)
        return max(video_duration, segment_duration)

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

    def _set_source_selection(self, selection: Any) -> None:
        super()._set_source_selection(selection)
        if self._loading_project_sources or self._relinking_project_sources or self._project is None:
            return
        if not self._project_source_selection_matches(selection):
            self._clear_project()

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
        project_output = self._normalized_source_path(str(self._project.get("output_dir", "")))
        selected_output = self._normalized_source_path(selection.output_dir)
        return (
            selected_video == project_video
            and selected_audio == project_audio
            and selected_output == project_output
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
            if self._project is not None and self._relink_source_selection != self._source_selection:
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

        if not selected_video or not selected_output or not source_entries:
            self._set_status("Relink requires a complete source selection", "CHECK")
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

        self._project_path = str(derive_project_path(selected_video, selected_output))
        reconcile_audio_mix(self._project, self._mixer_video_tracks() or self._fallback_video_tracks())

        if self._project != old_project:
            self._mark_project_dirty()
            self._reset_audio_preview_cache()
            self._sync_subtitle_model()
            self.projectDataChanged.emit()
            self.segmentsChanged.emit()
            self.selectionChanged.emit()
            self._set_status("Project sources relinked", "EDIT")

    def _default_project_path(self) -> Path | None:
        selection = self._source_selection
        if not selection.video or not selection.output_dir:
            return None
        return derive_project_path(selection.video, selection.output_dir)

    @Slot(result=bool)
    def transcriptionProjectExists(self) -> bool:
        path = self._default_project_path()
        return path is not None and path.is_file()

    def _clear_project(self) -> None:
        if self._project_dirty:
            self.saveProject()
        self._project = None
        self._project_path = ""
        self._project_dirty = False
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._selected_segment_index = -1
        self._project_revision += 1
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
        super().resetSources()
        if not self._loading_project_sources and self._project is not None:
            self._clear_project()

    @Slot(str)
    def setVideoFile(self, path: str) -> None:
        super().setVideoFile(path)
        self._try_load_default_project()

    @Slot(str)
    def setOutputDirectory(self, path: str) -> None:
        super().setOutputDirectory(path)
        self._try_load_default_project()

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
        start_dir = self._source_selection.output_dir or str(self.workspace_root)
        path, _ = QFileDialog.getOpenFileName(
            None,
            "字幕編集プロジェクトを開く",
            start_dir,
            "Subtitle projects (*.subtitle-project.json);;JSON files (*.json)",
        )
        if path:
            self.loadProject(path)

    def _mixer_video_tracks(self) -> list[dict[str, str]] | None:
        tracks = [
            {"selector": str(item.get("selector", "")), "label": str(item.get("label", ""))}
            for item in self._audio_tracks
            if str(item.get("selector", "")).strip()
        ]
        return tracks or None

    def _fallback_video_tracks(self) -> list[dict[str, str]]:
        return [{"selector": DEFAULT_AUDIO_TRACK, "label": "既定の動画音声"}]

    @Slot(int, "QVariantMap")
    def updateAudioMixChannel(self, index: int, changes: dict[str, Any]) -> None:
        if self._project is None or self._running:
            return
        audio_mix = reconcile_audio_mix(self._project, self._mixer_video_tracks() or self._fallback_video_tracks())
        channels = audio_mix["channels"]
        if not 0 <= index < len(channels):
            self._set_status("音量ミキサーのチャンネルが見つかりません", "CHECK")
            return
        channel = channels[index]
        enabled_before = bool(channel.get("enabled"))
        for key in ("enabled", "muted", "solo"):
            if key in changes:
                channel[key] = bool(changes[key])
        if "volume_percent" in changes:
            try:
                channel["volume_percent"] = max(
                    0.0,
                    min(MAX_VOLUME_PERCENT, float(changes["volume_percent"])),
                )
            except (TypeError, ValueError):
                self._set_status("音量は0〜200%で指定してください", "CHECK")
                return
        audio_mix["customized"] = True
        self.projectDataChanged.emit()
        self._notify_audio_mixer_preview(
            structure_changed=enabled_before != bool(channel.get("enabled"))
        )
        self._mark_project_dirty()
        self._set_status("音量ミキサー設定を更新しました", "EDIT")

    @Slot()
    def resetAudioMixer(self) -> None:
        if self._project is None or self._running:
            return
        reset_audio_mix(self._project, self._mixer_video_tracks() or self._fallback_video_tracks())
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
                self._set_status("先に出力先フォルダを指定してください", "ERROR")
                return
            if not self.saveProject():
                return
        candidate = self._local_path(path)
        self._load_project_path(candidate, update_sources=True)

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
        try:
            project = load_project(path)
        except (OSError, json.JSONDecodeError, SubtitleProjectError, TypeError, ValueError) as error:
            self._set_status(f"プロジェクトを開けません: {error}", "ERROR")
            return False
        self._project = project
        self._apply_project_subtitle_settings(project)
        self._project_path = str(path.resolve())
        self._project_dirty = False
        self._project_revision += 1
        self._reset_audio_preview_cache()
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._selected_segment_index = 0 if project.get("segments") else -1
        if update_sources:
            self._loading_project_sources = True
            try:
                video = Path(str(project.get("video", {}).get("path", "")))
                output_dir = Path(str(project.get("output_dir", "")))
                audio_files = [str(item.get("path", "")) for item in project.get("audio_sources", [])]
                resolved_audio_files = [str(Path(item).resolve()) for item in audio_files if Path(item).is_file()]

                self._set_source_selection(
                    replace(
                        self._source_selection,
                        video=str(video.resolve()) if video.is_file() else "",
                        output_dir=str(output_dir.resolve()) if output_dir.is_dir() else "",
                        audio_files=tuple(resolved_audio_files),
                    )
                )
            finally:
                self._loading_project_sources = False
        reconcile_audio_mix(self._project, self._mixer_video_tracks() or self._fallback_video_tracks())
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
        if self._project is None or (not before and not after):
            return
        self._undo_stack.append(
            {
                "before": deepcopy(before),
                "after": deepcopy(after),
                "reflow_layout": reflow_layout,
            }
        )
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self.historyChanged.emit()

    def _mark_project_dirty(self) -> None:
        if self._project is None:
            return
        self._project_revision += 1
        self._project_dirty = True
        self.projectChanged.emit()
        self.autosave_timer.start()

    def _replace_segments(
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
                (index for index, item in enumerate(self._project["segments"]) if item["id"] == selected_id),
                -1,
            )
        elif self._selected_segment_index >= len(self._project["segments"]):
            self._selected_segment_index = len(self._project["segments"]) - 1
        self._sync_subtitle_model()
        self.segmentsChanged.emit()
        self.selectionChanged.emit()
        self._mark_project_dirty()

    def _commit_segment_change(
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
        segments = [item for item in self._project["segments"] if str(item["id"]) not in affected_ids]
        segments.extend(after)
        self._record_history(before, after, reflow_layout)
        self._replace_segments(segments, selected_id, reflow_layout=reflow_layout)

    def _apply_history_entry(self, entry: dict[str, Any], state: str) -> None:
        if self._project is None:
            return
        affected_ids = {
            str(item["id"])
            for item in [*entry.get("before", []), *entry.get("after", [])]
        }
        segments = [item for item in self._project["segments"] if str(item["id"]) not in affected_ids]
        segments.extend(deepcopy(entry.get(state, [])))
        self._replace_segments(
            segments,
            reflow_layout=bool(entry.get("reflow_layout", True)),
        )

    @Slot(int)
    def selectSegment(self, index: int) -> None:
        count = len(self._project.get("segments", [])) if self._project else 0
        resolved = index if 0 <= index < count else -1
        if resolved != self._selected_segment_index:
            self._selected_segment_index = resolved
            self.selectionChanged.emit()

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
        if self._project is None or not self._undo_stack:
            return
        entry = self._undo_stack.pop()
        self._redo_stack.append(entry)
        self._apply_history_entry(entry, "before")
        self.historyChanged.emit()

    @Slot()
    def redoSubtitleEdit(self) -> None:
        if self._project is None or not self._redo_stack:
            return
        entry = self._redo_stack.pop()
        self._undo_stack.append(entry)
        self._apply_history_entry(entry, "after")
        self.historyChanged.emit()

    @Slot(result=bool)
    def saveProject(self) -> bool:
        if self._project is None or not self._project_path:
            self._set_status("保存する字幕編集プロジェクトがありません", "CHECK")
            return False
        self.autosave_timer.stop()
        self._wait_for_autosave()
        try:
            save_project(self._project_path, self._project, project_is_validated=True)
        except (OSError, SubtitleProjectError, TypeError, ValueError) as error:
            self._set_status(f"プロジェクトを保存できません: {error}", "ERROR")
            return False
        self._sync_subtitle_model()
        self._project_dirty = False
        self.projectChanged.emit()
        self._set_status("字幕編集を保存しました", "SAVED")
        return True

    def _autosave_project(self) -> None:
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
            save_project,
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
            except Exception as failure:
                error = str(failure)
            self.autosaveCompleted.emit(revision, path, error)

        future.add_done_callback(report_completion)

    @Slot(int, str, str)
    def _finish_autosave(self, revision: int, path: str, error: str) -> None:
        token = (revision, path)
        if token in self._ignored_autosaves:
            self._ignored_autosaves.discard(token)
            return
        self._autosave_future = None
        pending = self._autosave_pending
        self._autosave_pending = False
        if error:
            self._set_status(f"保存に失敗しました: {error}", "ERROR")
            return
        if (
            self._project is not None
            and path == self._project_path
            and revision == self._project_revision
        ):
            self._project_dirty = False
            self.projectChanged.emit()
            return
        if pending or self._project_dirty:
            QTimer.singleShot(0, self._autosave_project)

    def _wait_for_autosave(self) -> None:
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

    def _shutdown_executor(self) -> None:
        if hasattr(self, "_audio_master_mixer"):
            self._audio_master_mixer.stop()
        if hasattr(self, "autosave_timer"):
            self.autosave_timer.stop()
        if getattr(self, "_project_dirty", False) and getattr(self, "_project_path", ""):
            self.saveProject()
        if hasattr(self, "_autosave_executor"):
            self._wait_for_autosave()
            self._autosave_executor.shutdown(wait=True, cancel_futures=False)
        if hasattr(self, "_audio_preview_cache_executor"):
            self._audio_preview_cache_executor.shutdown(wait=True, cancel_futures=True)
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
        if self._last_process_diagnostic is not None:
            self._last_process_diagnostic = None
            self.lastProcessDiagnosticChanged.emit()
        self._pending_process_error = ""
        self._application_logger.clear_memory()
        self._record_log(
            f"> {subprocess.list2cmdline(command)}",
            component="gui",
            job=job,
            stage="STARTING",
        )
        self._progress = 0.02
        self.progressChanged.emit()
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

    @Slot("QVariantMap", bool)
    def startTranscription(self, settings: dict[str, Any], overwrite_project: bool = False) -> None:
        if self._running:
            return
        audio_tracks = list(self._audio_tracks)
        if not self._dependencies.ready:
            self.refreshDependencies()
        if not self._dependencies.ready:
            missing = ", ".join(self._dependencies.missing())
            self._set_status(f"実行できません。インストールが必要です: {missing}", "SETUP")
            return
        if str(settings.get("device") or self._settings.get("device")) == "cuda" and not self._dependencies.cuda:
            self._set_status(
                "CUDA版PyTorchが利用できません。setup.batを再実行するか、処理デバイスをCPUへ変更してください",
                "SETUP",
            )
            return
        selection = self._source_selection
        audio_files = [speaker["path"] for speaker in self._speakers]
        video_audio_track = ""
        if not Path(selection.video).is_file():
            self._set_status("動画・話者音声・出力先を指定してください", "CHECK")
            return
        if not audio_files and not self._has_audio_source(audio_files, audio_tracks):
            self._set_status("動画内に音声トラックが見つかりません。外部音声を追加するか、音声付きの動画を選択してください。", "CHECK")
            return
        if not selection.output_dir:
            self._set_status("動画・話者音声・出力先を指定してください", "CHECK")
            return
        if not audio_files:
            video_audio_track = str(settings.get("reference_track") or self._default_video_audio_track(audio_tracks))
        reference_audio = settings.get("reference_audio")
        if not reference_audio and audio_files:
            reference_audio = audio_files[0]
        reference_track = str(settings.get("reference_track") or "")
        adjustment = float(settings.get("alignment_offset_adjustment") or 0.0)
        self.saveSettings(settings)
        command = build_gui_transcribe_command(
            self.gui_config_path,
            video=selection.video,
            audio_files=audio_files,
            output_dir=selection.output_dir,
            reference_audio=reference_audio,
            reference_track=reference_track,
            video_audio_track=video_audio_track,
            alignment_offset_adjustment=adjustment,
            overwrite_project=overwrite_project,
        )
        self._start_command(command, "transcribe", "文字起こしを開始しています")

    @Slot("QVariantMap")
    def startProcessing(self, settings: dict[str, Any]) -> None:
        self.startTranscription(settings)

    @Slot("QVariantMap")
    def renderVideo(self, settings: dict[str, Any]) -> None:
        if self._running or self._project is None:
            return
        self.refreshDependencies()
        effective_settings = dict(settings)
        effective_settings["video_codec"] = (
            "h264_nvenc" if self._dependencies.nvenc else "libx264"
        )
        self.saveSettings(effective_settings)
        self._update_project_settings(effective_settings)
        if not self.saveProject():
            return
        command = build_gui_render_command(self.gui_config_path, project_path=self._project_path)
        mode = "GPU" if self._dependencies.nvenc else "CPU"
        self._start_command(command, "render", f"{mode}を自動選択して動画を書き出しています")

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
                f"SubtitleEditBay-{info.latest_version.lstrip('v')}.exe"
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
                "start_new_session": True,
            }
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
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
        if self._project is not None and not self.saveProject():
            self._set_status("プロジェクトを保存できませんでした", "ERROR")
            return
        self.saveSettings(self._settings)
        command = updater.launch_update_script(
            self.workspace_root, self._update_info.download_url
        )
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
                start_new_session=True,
            )
        except OSError as error:
            self._set_status(f"再起動に失敗しました: {error}", "ERROR")
            return
        self.quit()

    @Slot()
    def renderShortVideo(self) -> None:
        if self._running or self._project is None:
            return
        self.refreshDependencies()
        if not self.saveProject():
            return
        command = build_gui_short_video_command(
            self.gui_config_path, project_path=self._project_path
        )
        mode = "GPU" if self._dependencies.nvenc else "CPU"
        self._start_command(command, "render_short", f"{mode}を自動選択してショート動画を書き出しています")

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
        if self._codex_session.running:
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

    def _on_codex_state(self, _snapshot: CodexSessionSnapshot) -> None:
        self.codexStateChanged.emit()
        self.codexMessageChanged.emit()
        if self._codex_session.snapshot.error:
            self._set_status(self._codex_session.snapshot.error, "ERROR")

    def _on_codex_message(self, _message: str) -> None:
        self.codexMessageChanged.emit()

    def _on_codex_proposal(self, proposal: Mapping[str, Any]) -> None:
        self._codex_proposal = dict(proposal)
        self.codexProposalChanged.emit()
        self._set_status("Codex編集案を確認できます", "CODEX")

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
    ) -> None:
        self._application_logger.append(
            message,
            severity=severity,
            component=component,
            job=job,
            stage=stage,
            process_id=process_id,
            exit_code=exit_code,
        )
        self._log = self._application_logger.text
        self.logChanged.emit()
        self.applicationLogChanged.emit()

    @Property(str, notify=applicationLogChanged)
    def logFilePath(self) -> str:
        return str(self._application_logger.log_path)

    def _related_process_log_tail(self) -> str:
        output_directory = str(self._source_selection.output_dir or "").strip()
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

    def _read_process_output(self) -> None:
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if not data:
            return
        normalized = data.replace("\r", "\n")
        self._record_log(
            normalized,
            component=self._active_job or "process",
            job=self._active_job,
            stage=self.stage,
            process_id=int(self.process.processId()) or None,
        )
        self._update_stage(normalized)

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
            self._active_job = ""
            self.activeJobChanged.emit()

    def _update_stage(self, output: str) -> None:
        markers = [
            ("Resolving alignment", "ALIGN", "動画と話者音声を同期しています", 0.08),
            ("Starting WhisperX", "WHISPERX", "文字起こししています", 0.22),
            ("Refining merged", "LAYOUT", "編集用字幕を組み立てています", 0.64),
            ("Building waveform", "WAVEFORM", "タイムライン波形を作成しています", 0.78),
            ("Project ready", "PROJECT", "編集プロジェクトを保存しています", 0.92),
            ("ASS preview ready", "ASS", "ASS字幕を生成しています", 0.3),
            ("Rendering edited", "ENCODE", "字幕を動画へ焼き付けています", 0.45),
            ("Render complete", "ENCODE", "動画を書き出しました", 0.96),
        ]
        for marker, stage, status, progress in markers:
            if marker in output:
                self._progress = max(self._progress, progress)
                self.progressChanged.emit()
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
                    for line in reversed(self._log.splitlines())
                    if line.strip() and not line.lstrip().startswith(">")
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
            self._set_status("処理を停止しました", "CANCELLED")
        elif exit_code == 0:
            self._progress = 1.0
            self.progressChanged.emit()
            if completed_job == "transcribe":
                loaded = self._try_load_default_project()
                self._set_status(
                    "文字起こし完了。字幕を確認して動画へ焼き付けられます"
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
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    if not engine.rootObjects():
        raise SystemExit(f"Could not load GUI: {qml_path}")
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
