from __future__ import annotations

from typing import TYPE_CHECKING

import math
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    Property,
    QObject,
    Signal,
    Slot,
)
from PySide6.QtWidgets import QFileDialog

from .color_config import normalize_rgb_color
from .short_video_schema import VALID_FIT_MODES, VALID_TRANSITION_TYPES

from .gui_feature_facade import FeatureFacade

if TYPE_CHECKING:
    from .gui import EditBayBackend


class ShortVideoFacade(FeatureFacade):
    """ショート動画とハイライトの画面窓口。"""

    highlightAnalysisChanged = Signal()
    highlightCandidatesChanged = Signal()
    shortVideoChanged = Signal()
    shortVideoClipDataChanged = Signal()

    def __init__(self, backend: "EditBayBackend") -> None:
        super().__init__(backend)
        backend.highlightAnalysisChanged.connect(self.highlightAnalysisChanged.emit)
        backend.highlightCandidatesChanged.connect(self.highlightCandidatesChanged.emit)
        backend.shortVideoChanged.connect(self.shortVideoChanged.emit)
        backend.shortVideoClipDataChanged.connect(self.shortVideoClipDataChanged.emit)

    @Property("QVariantList", notify=shortVideoChanged)
    def shortVideoClips(self) -> list[dict[str, Any]]:
        """Return all clips for callers outside QML.

        QML uses ``shortVideoClipModel`` and ``shortVideoClipAt`` so delegates and
        the preview only materialize the rows they currently need.
        """

        return [self._short_video_clip_view_at(index) for index in range(self._short_video_clip_count())]

    @Property("QVariantMap", notify=shortVideoChanged)
    def shortVideoSettings(self) -> dict[str, Any]:
        backend = self._backend
        if backend._project is None:
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
        backend = self._backend
        return deepcopy(backend._highlight_candidates)

    @Property(bool, notify=highlightCandidatesChanged)
    def highlightUndoAvailable(self) -> bool:
        backend = self._backend
        return bool(backend._highlight_rejected)

    @Property(str, notify=highlightAnalysisChanged)
    def highlightAnalysisState(self) -> str:
        backend = self._backend
        return backend._highlight_status

    @Property(float, notify=highlightAnalysisChanged)
    def highlightAnalysisProgress(self) -> float:
        backend = self._backend
        return backend._highlight_progress

    @Property(QObject, constant=True)
    def shortVideoClipModel(self) -> QObject:
        backend = self._backend
        return backend._short_video_clip_model

    @Property(int, notify=shortVideoClipDataChanged)
    def shortVideoClipCount(self) -> int:
        return self._short_video_clip_count()

    @Slot(int, result="QVariantMap")
    def shortVideoClipAt(self, index: int) -> dict[str, Any]:
        return self._short_video_clip_view_at(index)

    def _short_video_section(self) -> dict[str, Any]:
        backend = self._backend
        if backend._project is None:
            return {}
        section = backend._project.setdefault(
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
            section = backend._project["short_video"] = {
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

    def _short_video_clip_count(self) -> int:
        backend = self._backend
        if backend._project is None:
            return 0
        section = backend._project.get("short_video", {})
        if not isinstance(section, dict):
            return 0
        clips = section.get("clips", [])
        return len(clips) if isinstance(clips, list) else 0

    def _short_video_clip_view_at(self, index: int) -> dict[str, Any]:
        backend = self._backend
        if backend._project is None:
            return {}
        section = backend._project.get("short_video", {})
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
        backend = self._backend
        backend._short_video_clip_model.refresh()
        backend.shortVideoClipDataChanged.emit()

    def _build_short_video_clip_view(self, clip: dict[str, Any], index: int) -> dict[str, Any]:
        backend = self._backend
        segment_id = str(clip.get("segment_id", ""))
        segment = backend.subtitles._find_segment_by_id(segment_id) or {}
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
            "preview_text": backend.subtitles._preview_text_for_segment(segment)
            if segment
            else str(clip.get("text", "")),
        }

    @Slot()
    def initializeShortVideoClips(self) -> None:
        backend = self._backend
        if backend._project is None:
            return
        section = self._short_video_section()
        if section.get("clips"):
            return
        clips: list[dict[str, Any]] = []
        for segment in sorted(
            backend._project.get("segments", []),
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
        backend.subtitles._mark_project_dirty()
        backend.projectDataChanged.emit()
        backend.shortVideoChanged.emit()

    @Slot(str, result=bool)
    def addShortVideoClip(self, segment_id: str) -> bool:
        backend = self._backend
        if backend._project is None or backend._running:
            return False
        segment = backend.subtitles._find_segment_by_id(segment_id)
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
        backend.subtitles._mark_project_dirty()
        backend.projectDataChanged.emit()
        backend.shortVideoChanged.emit()
        return True

    @Slot(float, float, result=bool)
    def addShortVideoClipByRange(self, start: float, end: float) -> bool:
        backend = self._backend
        if backend._project is None or backend._running:
            return False
        try:
            start = float(start)
            end = float(end)
        except (TypeError, ValueError, OverflowError):
            return False
        if not math.isfinite(start) or not math.isfinite(end):
            return False
        duration = max(0.0, float(backend.projectDuration))
        if start < 0.0 or start >= end or (duration > 0.0 and end > duration):
            return False
        section = self._short_video_section()
        clips = list(section.get("clips", []))
        clips.append({"segment_id": "", "start": round(start, 3), "end": round(end, 3)})
        section["enabled"] = True
        section["clips"] = clips
        backend.subtitles._mark_project_dirty()
        backend.projectDataChanged.emit()
        backend.shortVideoChanged.emit()
        return True

    @Slot(int, result=bool)
    def removeShortVideoClip(self, index: int) -> bool:
        backend = self._backend
        if backend._project is None or backend._running:
            return False
        section = self._short_video_section()
        clips = list(section.get("clips", []))
        if not 0 <= index < len(clips):
            return False
        clips.pop(index)
        section["clips"] = clips
        backend.subtitles._mark_project_dirty()
        backend.projectDataChanged.emit()
        backend.shortVideoChanged.emit()
        return True

    @Slot(int, int, result=bool)
    def moveShortVideoClip(self, from_index: int, to_index: int) -> bool:
        backend = self._backend
        if backend._project is None or backend._running:
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
        backend.subtitles._mark_project_dirty()
        backend.projectDataChanged.emit()
        backend.shortVideoChanged.emit()
        return True

    @Slot(int, "QVariantMap", result=bool)
    def updateShortVideoClip(self, index: int, fields: dict[str, Any]) -> bool:
        backend = self._backend
        if backend._project is None or backend._running:
            return False
        if not isinstance(fields, dict) or not fields:
            return False
        section = self._short_video_section()
        clips = list(section.get("clips", []))
        if not 0 <= index < len(clips):
            return False
        clip = dict(clips[index])
        trim_requested = "start" in fields or "end" in fields
        if not trim_requested and not any(key in fields for key in ("fit", "background_color")):
            return False

        if trim_requested:
            segment = backend.subtitles._find_segment_by_id(str(clip.get("segment_id", "")))
            range_clip = not str(clip.get("segment_id", "")).strip()
            if segment is None and not range_clip:
                return False
            try:
                if segment is None:
                    segment_start = 0.0
                    segment_end = float(backend.projectDuration)
                    if segment_end <= 0.0:
                        segment_end = max(float(clip.get("end", 0.0)), 0.0)
                else:
                    segment_start = float(segment.get("start", 0.0))
                    segment_end = float(segment.get("end", segment_start))
                start = float(fields.get("start", clip.get("start", segment_start)))
                end = float(fields.get("end", clip.get("end", segment_end)))
                if not all(math.isfinite(value) for value in (segment_start, segment_end, start, end)):
                    return False
            except (TypeError, ValueError):
                return False
            video_duration = backend.projectDuration
            upper_bound = min(segment_end, video_duration) if video_duration > 0.0 else segment_end
            lower_bound = max(0.0, segment_start)
            if upper_bound <= lower_bound or start < lower_bound or end > upper_bound or start >= end:
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
        backend.subtitles._mark_project_dirty()
        backend.projectDataChanged.emit()
        backend.shortVideoChanged.emit()
        return True

    @Slot(str, result=bool)
    def setShortVideoGlobalFit(self, fit: str) -> bool:
        backend = self._backend
        if backend._project is None or backend._running:
            return False
        fit = str(fit).lower()
        if fit not in VALID_FIT_MODES:
            return False
        section = self._short_video_section()
        section["global_fit"] = fit
        backend.subtitles._mark_project_dirty()
        backend.projectDataChanged.emit()
        backend.shortVideoChanged.emit()
        return True

    @Slot(str, result=bool)
    def setShortVideoGlobalBackgroundColor(self, color: str) -> bool:
        backend = self._backend
        if backend._project is None or backend._running:
            return False
        try:
            normalized = normalize_rgb_color(color)
        except (TypeError, ValueError, OverflowError):
            return False
        section = self._short_video_section()
        section["global_background_color"] = normalized
        backend.subtitles._mark_project_dirty()
        backend.projectDataChanged.emit()
        backend.shortVideoChanged.emit()
        return True

    @Slot(str, float, result=bool)
    def setShortVideoTransition(self, transition_type: str, duration: float) -> bool:
        backend = self._backend
        if backend._project is None or backend._running:
            return False
        transition_type = str(transition_type).lower()
        if transition_type not in VALID_TRANSITION_TYPES:
            return False
        section = self._short_video_section()
        section["transition"] = {"type": transition_type, "duration": max(0.0, round(float(duration), 3))}
        backend.subtitles._mark_project_dirty()
        backend.projectDataChanged.emit()
        backend.shortVideoChanged.emit()
        return True

    @Slot("QVariantMap", result=bool)
    def setShortVideoBgm(self, fields: dict[str, Any]) -> bool:
        backend = self._backend
        if backend._project is None or backend._running:
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
        backend.subtitles._mark_project_dirty()
        backend.projectDataChanged.emit()
        backend.shortVideoChanged.emit()
        return True

    @Slot(int, int, int, result=bool)
    def setShortVideoOutput(self, width: int, height: int, fps: int) -> bool:
        backend = self._backend
        if backend._project is None or backend._running:
            return False
        section = self._short_video_section()
        section["output"] = {"width": max(1, int(width)), "height": max(1, int(height)), "fps": max(1, int(fps))}
        backend.subtitles._mark_project_dirty()
        backend.projectDataChanged.emit()
        backend.shortVideoChanged.emit()
        return True

    @Slot(float, result=bool)
    def setShortVideoSubtitleScale(self, percent: float) -> bool:
        backend = self._backend
        if backend._project is None or backend._running:
            return False
        section = self._short_video_section()
        section["subtitle_scale_percent"] = max(0.0, float(percent))
        backend.subtitles._mark_project_dirty()
        backend.projectDataChanged.emit()
        backend.shortVideoChanged.emit()
        return True

    @Slot(result=bool)
    def startHighlightAnalysis(self) -> bool:
        backend = self._backend
        if backend._project is None or backend._running:
            return False
        if backend._highlight_status in {"running", "cancelling"}:
            return False
        backend._highlight_generation += 1
        generation = backend._highlight_generation
        cancel_event = threading.Event()
        backend._highlight_cancel = cancel_event
        had_rejected = bool(backend._highlight_rejected)
        backend._highlight_rejected = []
        backend._highlight_status = "running"
        backend._highlight_progress = 0.0
        backend.highlightAnalysisChanged.emit()
        if had_rejected:
            backend.highlightCandidatesChanged.emit()
        segments = deepcopy(backend._project.get("segments", []))
        duration = backend.projectDuration
        cache_directory = Path(backend._project_path).parent / ".highlight-cache" if backend._project_path else None

        def worker() -> None:
            try:
                from .highlight_candidates import generate_highlight_candidates

                candidates = generate_highlight_candidates(
                    segments,
                    duration_seconds=duration,
                    cancel_check=cancel_event.is_set,
                    progress_callback=lambda value: self._update_highlight_progress(generation, value),
                    cache_directory=cache_directory,
                )
                if not self._is_current_highlight_run(generation):
                    return
                if cancel_event.is_set():
                    backend._highlight_status = "cancelled"
                    backend.highlightAnalysisChanged.emit()
                    return
                backend._highlight_candidates = [item.to_json() for item in candidates]
                backend._highlight_status = "completed"
                backend._highlight_progress = 1.0
                backend.highlightCandidatesChanged.emit()
                backend.highlightAnalysisChanged.emit()
            except Exception as error:
                if not self._is_current_highlight_run(generation):
                    return
                backend._highlight_status = "cancelled" if cancel_event.is_set() else "error"
                if not cancel_event.is_set():
                    backend._set_status(f"見どころ候補の解析に失敗しました: {error}", "ERROR")
                backend.highlightAnalysisChanged.emit()

        threading.Thread(target=worker, name="highlight-analysis", daemon=True).start()
        return True

    @Slot(result=bool)
    def cancelHighlightAnalysis(self) -> bool:
        backend = self._backend
        if backend._highlight_status != "running":
            return False
        backend._highlight_cancel.set()
        backend._highlight_status = "cancelling"
        backend.highlightAnalysisChanged.emit()
        return True

    @Slot(result=bool)
    def retryHighlightAnalysis(self) -> bool:
        backend = self._backend
        if backend._highlight_status in {"running", "cancelling"}:
            return False
        backend._highlight_candidates = []
        backend.highlightCandidatesChanged.emit()
        return self.startHighlightAnalysis()

    @Slot(int, result=bool)
    def addHighlightCandidate(self, index: int) -> bool:
        backend = self._backend
        if backend._project is None or backend._running or not 0 <= index < len(backend._highlight_candidates):
            return False
        candidate = backend._highlight_candidates[index]
        source_ids = [str(item) for item in candidate.get("source_segment_ids", [])]
        if not source_ids:
            return False
        section = self._short_video_section()
        clips = list(section.get("clips", []))
        candidate_start = float(candidate.get("start", 0.0))
        candidate_end = float(candidate.get("end", candidate_start))
        if any(
            str(clip.get("segment_id", "")) in source_ids
            and min(float(clip.get("end", 0.0)), candidate_end) > max(float(clip.get("start", 0.0)), candidate_start)
            for clip in clips
        ):
            backend._set_status("同じ区間のショートクリップは追加済みです", "CHECK")
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
        backend.subtitles._mark_project_dirty()
        backend.projectDataChanged.emit()
        backend.shortVideoChanged.emit()
        return True

    @Slot(int, result=bool)
    def rejectHighlightCandidate(self, index: int) -> bool:
        backend = self._backend
        if not 0 <= index < len(backend._highlight_candidates):
            return False
        backend._highlight_rejected.append(backend._highlight_candidates.pop(index))
        backend.highlightCandidatesChanged.emit()
        return True

    @Slot(result=bool)
    def undoHighlightRejection(self) -> bool:
        backend = self._backend
        if not backend._highlight_rejected:
            return False
        backend._highlight_candidates.append(backend._highlight_rejected.pop())
        backend.highlightCandidatesChanged.emit()
        return True

    def _is_current_highlight_run(self, generation: int) -> bool:
        backend = self._backend
        return generation == backend._highlight_generation

    def _update_highlight_progress(self, generation: int, value: float) -> None:
        backend = self._backend
        if not self._is_current_highlight_run(generation):
            return
        backend._highlight_progress = max(0.0, min(1.0, float(value)))
        backend.highlightAnalysisChanged.emit()

    @Slot(result=str)
    def browseShortModeBgm(self) -> str:
        backend = self._backend
        if backend._running:
            return ""
        start_dir = backend._source_selection.output_dir or str(backend.workspace_root)
        path, _ = QFileDialog.getOpenFileName(
            None,
            "BGM ファイルを選択",
            start_dir,
            "Audio files (*.mp3 *.wav *.m4a *.aac *.ogg *.flac);;All files (*.*)",
        )
        if path:
            self.setShortVideoBgm({"path": path})
        return path
