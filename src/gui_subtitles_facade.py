from __future__ import annotations

from typing import TYPE_CHECKING

from bisect import bisect_left, bisect_right
import math
from copy import deepcopy
from typing import Any
from uuid import uuid4

from PySide6.QtCore import (
    Property,
    QObject,
    Signal,
    Slot,
)

from .color_config import normalize_rgb_color, save_speaker_color
from .subtitle_project import (
    MIN_SEGMENT_DURATION_SECONDS,
    normalize_segment,
)
from .subtitle_line_count import segment_preview_text
from .subtitle_workflow import build_project_ass

from .gui_feature_facade import FeatureFacade

if TYPE_CHECKING:
    from .gui import EditBayBackend


class SubtitleFacade(FeatureFacade):
    """字幕の一覧・編集・プレビューの画面窓口。"""

    historyChanged = Signal()
    projectDataChanged = Signal()
    segmentsChanged = Signal()
    selectionChanged = Signal()

    def __init__(self, backend: "EditBayBackend") -> None:
        super().__init__(backend)
        # 保存データから導出する表示状態は、字幕窓口のインスタンスが所有する。
        self._segment_by_id: dict[str, dict[str, Any]] = {}
        self._subtitle_layout_metrics: dict[str, float | int] = {
            "maxFontScale": 1.0,
            "maxLayoutRow": 0,
        }
        self._subtitle_preview_text_cache: dict[str, tuple[tuple[object, ...], str]] = {}
        self._segment_starts: list[float] = []
        self._segment_prefix_max_end: list[float] = []
        backend.historyChanged.connect(self.historyChanged.emit)
        backend.projectDataChanged.connect(self.projectDataChanged.emit)
        backend.segmentsChanged.connect(self.segmentsChanged.emit)
        backend.selectionChanged.connect(self.selectionChanged.emit)

    @Property("QVariantList", notify=segmentsChanged)
    def subtitleSegments(self) -> list[dict[str, Any]]:
        if self.project_editor.project is None:
            return []
        return deepcopy(self.project_editor.project.get("segments", []))

    @Property("QVariantMap", notify=segmentsChanged)
    def subtitleLayoutMetrics(self) -> dict[str, float | int]:
        """Return the small aggregate QML needs without copying every segment."""

        return dict(self._subtitle_layout_metrics)

    @Property(QObject, constant=True)
    def subtitleModel(self) -> QObject:
        backend = self._backend
        return backend._subtitle_model

    @Property("QVariantList", constant=True)
    def fontChoices(self) -> list[dict[str, str]]:
        backend = self._backend
        return deepcopy(backend._font_choices)

    @Property(int, notify=segmentsChanged)
    def segmentCount(self) -> int:
        return len(self.project_editor.project.get("segments", [])) if self.project_editor.project else 0

    def _sync_subtitle_model(self) -> None:
        backend = self._backend
        segments = self.project_editor.project.get("segments", []) if self.project_editor.project else []
        self._segment_by_id = {str(segment["id"]): segment for segment in segments}
        backend._subtitle_model.set_segments(segments)
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
        backend.shortVideo._refresh_short_video_clip_data()

    def _on_project_segments_changed(self) -> None:
        """Publish controller segment changes through the existing QML facade."""

        backend = self._backend

        self._sync_subtitle_model()
        backend.segmentsChanged.emit()

    def _on_project_history_applied(
        self,
        entry: dict[str, Any],
        _state: str,
    ) -> None:
        """Refresh side effects that are intentionally owned by the facade."""
        backend = self._backend

        if entry.get("kind") == "audio_mix":
            backend.audio._notify_audio_mixer_preview(structure_changed=True)
        elif entry.get("kind") == "timeline":
            backend.workspace._sync_project_timeline()
        elif entry.get("kind") == "short_video":
            backend.shortVideoChanged.emit()

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

    def _find_segment_by_id(self, segment_id: str) -> dict[str, Any] | None:
        return self._segment_by_id.get(str(segment_id))

    @Slot(int, result="QVariantMap")
    def segmentAt(self, index: int) -> dict[str, Any]:
        segments = self.project_editor.project.get("segments", []) if self.project_editor.project else []
        if not 0 <= index < len(segments):
            return {}
        return self._segment_view(segments[index], index)

    @Slot(int, str, result=str)
    def formatSubtitlePreview(self, index: int, text: str) -> str:
        segments = self.project_editor.project.get("segments", []) if self.project_editor.project else []
        if not 0 <= index < len(segments):
            return str(text)
        draft = {**segments[index], "text": str(text)}
        return segment_preview_text(draft)

    @Slot(float, result="QVariantList")
    def activeSubtitleSegments(self, seconds: float) -> list[dict[str, Any]]:
        segments = self.project_editor.project.get("segments", []) if self.project_editor.project else []
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
        segments = self.project_editor.project.get("segments", []) if self.project_editor.project else []
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
        if self.project_editor.project is None:
            return []
        return deepcopy(self.project_editor.project.get("speakers", []))

    @Property("QVariantList", notify=projectDataChanged)
    def subtitleWaveforms(self) -> list[dict[str, Any]]:
        if self.project_editor.project is None:
            return []
        return deepcopy(self.project_editor.project.get("waveforms", []))

    @Property(bool, notify=historyChanged)
    def canUndo(self) -> bool:
        return bool(self.project_editor.undo_stack)

    @Property(bool, notify=historyChanged)
    def canRedo(self) -> bool:
        return bool(self.project_editor.redo_stack)

    @Property(int, notify=selectionChanged)
    def selectedSegmentIndex(self) -> int:
        return self.project_editor.selected_segment_index

    def _apply_project_speaker_color(self, index: int, color: str) -> bool:
        backend = self._backend
        if self.project_editor.project is None or not 0 <= index < len(self.project_editor.project.get("speakers", [])):
            return False
        current = self.project_editor.project["speakers"][index]
        if str(current.get("color", "")).upper() == color:
            return False
        updated = {**current, "color": color}
        self.project_editor.project["speakers"][index] = updated
        style = str(updated.get("style", ""))
        name = str(updated.get("name", ""))
        for waveform in self.project_editor.project.get("waveforms", []):
            if waveform.get("style") == style or waveform.get("speaker") == name:
                waveform["color"] = color

        source_changed = False
        for source_index, source in enumerate(backend._speakers):
            if (
                source.get("path") == updated.get("path")
                or source.get("file_name") == updated.get("file_name")
                or source.get("name") == name
            ):
                backend._speakers[source_index] = {**source, "color": color}
                source_changed = True
        if source_changed:
            backend.speakersChanged.emit()
        backend.projectDataChanged.emit()
        self._mark_project_dirty()
        return True

    def _source_speaker_color_updated(self, speaker: dict[str, str]) -> None:
        if self.project_editor.project is None:
            return
        index = next(
            (
                index
                for index, project_speaker in enumerate(self.project_editor.project.get("speakers", []))
                if (
                    project_speaker.get("path") == speaker.get("path")
                    or project_speaker.get("file_name") == speaker.get("file_name")
                    or project_speaker.get("name") == speaker.get("name")
                )
            ),
            -1,
        )
        if index >= 0:
            self._apply_project_speaker_color(index, str(speaker["color"]))

    @Slot(int, str)
    def updateProjectSpeakerColor(self, index: int, color: str) -> None:
        backend = self._backend
        if (
            backend._running
            or self.project_editor.project is None
            or not 0 <= index < len(self.project_editor.project.get("speakers", []))
        ):
            return
        speaker = self.project_editor.project["speakers"][index]
        try:
            normalized = normalize_rgb_color(color)
            save_speaker_color(
                backend.color_config_path,
                file_name=str(speaker.get("file_name", "")),
                speaker_name=str(speaker.get("name", "")),
                color=normalized,
            )
        except (OSError, ValueError, TypeError) as error:
            backend._set_status(f"話者色を保存できません: {error}", "ERROR")
            return
        self._apply_project_speaker_color(index, normalized)
        backend._set_status(f"{speaker.get('name', '話者')} の字幕色を保存しました", "SAVED")

    def _mark_project_dirty(self) -> None:
        self.project_editor.mark_dirty()

    def _commit_segment_change(
        self,
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
        selected_id: str | None = None,
        *,
        reflow_layout: bool = True,
    ) -> None:
        self.project_editor.commit_segment_change(
            before,
            after,
            selected_id,
            reflow_layout=reflow_layout,
        )

    @Slot(int)
    def selectSegment(self, index: int) -> None:
        self.project_editor.select_segment(index)

    @Slot(float, result=int)
    def segmentIndexAtTime(self, seconds: float) -> int:
        segments = self.project_editor.project.get("segments", []) if self.project_editor.project else []
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
        backend = self._backend
        try:
            number = float(value)
        except (TypeError, ValueError):
            backend._set_status(f"{label}には数値を入力してください", "CHECK")
            return None
        if not math.isfinite(number):
            backend._set_status(f"{label}には有限の数値を入力してください", "CHECK")
            return None
        return number

    @Slot(int, "QVariantMap")
    def updateSegment(self, index: int, changes: dict[str, Any]) -> None:
        if self.project_editor.project is None or not 0 <= index < len(self.project_editor.project["segments"]):
            return
        current = self.project_editor.project["segments"][index]
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
            speaker = next(
                (item for item in self.project_editor.project.get("speakers", []) if item.get("style") == style), None
            )
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
        if self.project_editor.project is not None:
            edges = [
                float(edge)
                for index, segment in enumerate(self.project_editor.project["segments"])
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
        if self.project_editor.project is None or not 0 <= index < len(self.project_editor.project["segments"]):
            return
        duration = max(MIN_SEGMENT_DURATION_SECONDS, end - start)
        snapped_start = self._snap_time(start, index, max(0.0, snap_seconds))
        snapped_end = snapped_start + duration
        self.updateSegment(index, {"start": snapped_start, "end": snapped_end})

    @Slot(int, float, float)
    def resizeSegmentStart(self, index: int, start: float, snap_seconds: float) -> None:
        if self.project_editor.project is None or not 0 <= index < len(self.project_editor.project["segments"]):
            return
        segment = self.project_editor.project["segments"][index]
        snapped = self._snap_time(start, index, max(0.0, snap_seconds))
        self.updateSegment(index, {"start": min(snapped, float(segment["end"]) - MIN_SEGMENT_DURATION_SECONDS)})

    @Slot(int, float, float)
    def resizeSegmentEnd(self, index: int, end: float, snap_seconds: float) -> None:
        if self.project_editor.project is None or not 0 <= index < len(self.project_editor.project["segments"]):
            return
        segment = self.project_editor.project["segments"][index]
        snapped = self._snap_time(end, index, max(0.0, snap_seconds))
        self.updateSegment(index, {"end": max(snapped, float(segment["start"]) + MIN_SEGMENT_DURATION_SECONDS)})

    @Slot(float)
    def addSegment(self, at_seconds: float) -> None:
        if self.project_editor.project is None:
            return
        speakers = self.project_editor.project.get("speakers", [])
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
            len(self.project_editor.project["segments"]),
        )
        self._commit_segment_change([], [segment], segment["id"])

    @Slot()
    def deleteSelectedSegment(self) -> None:
        if self.project_editor.project is None or not 0 <= self.project_editor.selected_segment_index < len(
            self.project_editor.project["segments"]
        ):
            return
        self._commit_segment_change(
            [self.project_editor.project["segments"][self.project_editor.selected_segment_index]], []
        )

    @Slot(float)
    def splitSelectedSegment(self, at_seconds: float) -> None:
        backend = self._backend
        if self.project_editor.project is None or not 0 <= self.project_editor.selected_segment_index < len(
            self.project_editor.project["segments"]
        ):
            return
        index = self.project_editor.selected_segment_index
        segment = deepcopy(self.project_editor.project["segments"][index])
        split_at = float(at_seconds)
        if (
            not float(segment["start"]) + MIN_SEGMENT_DURATION_SECONDS
            < split_at
            < float(segment["end"]) - MIN_SEGMENT_DURATION_SECONDS
        ):
            backend._set_status("再生位置を選択字幕の途中へ移動してください", "CHECK")
            return
        text = str(segment.get("text", ""))
        midpoint = (
            max(
                1,
                min(
                    len(text) - 1,
                    round(len(text) * (split_at - segment["start"]) / (segment["end"] - segment["start"])),
                ),
            )
            if len(text) > 1
            else len(text)
        )
        first = {
            **segment,
            "end": split_at,
            "text": text[:midpoint].strip(),
            "manual_text": True,
            "manual_timing": True,
        }
        second = {
            **segment,
            "id": f"subtitle-{uuid4().hex[:12]}",
            "start": split_at,
            "text": text[midpoint:].strip(),
            "manual_text": True,
            "manual_timing": True,
        }
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
        self.project_editor.undo()

    @Slot()
    def redoSubtitleEdit(self) -> None:
        self.redoEdit()

    @Slot()
    def redoCutEdit(self) -> None:
        self.redoEdit()

    @Slot()
    def redoEdit(self) -> None:
        self.project_editor.redo()

    @Slot("QVariantMap")
    def buildSubtitlePreview(self, settings: dict[str, Any]) -> None:
        backend = self._backend
        if self.project_editor.project is None:
            return
        backend._update_project_settings(settings)
        if not backend.saveProject():
            return
        try:
            output = build_project_ass(self.project_editor.project_path)
        except (OSError, ValueError) as error:
            backend._set_status(f"自動保存に失敗しました: {error}", "ERROR")
            return
        backend._ass_path = str(output.resolve())
        backend.assPathChanged.emit()
        backend._set_status(f"ASSプレビューを生成しました: {output.name}", "ASS")
