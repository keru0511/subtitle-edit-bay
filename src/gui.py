from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from PySide6.QtCore import Property, QProcess, QProcessEnvironment, QTimer, QUrl, Signal, Slot
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QFileDialog

from .gui_base import APP_TITLE, EditBayBackend as LegacyEditBayBackend
from .gui_state import build_gui_render_command, build_gui_transcribe_command
from .subtitle_project import (
    MIN_SEGMENT_DURATION_SECONDS,
    SubtitleProjectError,
    derive_project_path,
    load_project,
    normalize_segment,
    save_project,
    validate_project,
)
from .subtitle_workflow import build_project_ass


class EditBayBackend(LegacyEditBayBackend):
    projectChanged = Signal()
    segmentsChanged = Signal()
    historyChanged = Signal()
    selectionChanged = Signal()
    activeJobChanged = Signal()
    assPathChanged = Signal()

    def __init__(self, argv: list[str], workspace_root: Path | None = None) -> None:
        self._project: dict[str, Any] | None = None
        self._project_path = ""
        self._project_dirty = False
        self._undo_stack: list[list[dict[str, Any]]] = []
        self._redo_stack: list[list[dict[str, Any]]] = []
        self._selected_segment_index = -1
        self._active_job = ""
        self._ass_path = ""
        self._loading_project_sources = False
        super().__init__(argv, workspace_root=workspace_root)
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(700)
        self.autosave_timer.timeout.connect(self._autosave_project)

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

    @Property("QVariantList", notify=segmentsChanged)
    def subtitleSegments(self) -> list[dict[str, Any]]:
        if self._project is None:
            return []
        return deepcopy(self._project.get("segments", []))

    @Property("QVariantList", notify=projectChanged)
    def projectSpeakers(self) -> list[dict[str, Any]]:
        if self._project is None:
            return []
        return deepcopy(self._project.get("speakers", []))

    @Property("QVariantList", notify=projectChanged)
    def subtitleWaveforms(self) -> list[dict[str, Any]]:
        if self._project is None:
            return []
        return deepcopy(self._project.get("waveforms", []))

    @Property(float, notify=projectChanged)
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

    def _set_source_selection(self, selection: Any) -> None:
        super()._set_source_selection(selection)
        if self._loading_project_sources or self._project is None:
            return
        project_video = str(Path(str(self._project.get("video", {}).get("path", ""))).resolve())
        selected_video = str(Path(selection.video).resolve()) if selection.video else ""
        project_audio = {
            str(Path(str(item.get("path", ""))).resolve())
            for item in self._project.get("audio_sources", [])
            if item.get("path")
        }
        selected_audio = {str(Path(path).resolve()) for path in selection.audio_files}
        project_output = str(Path(str(self._project.get("output_dir", ""))).resolve())
        selected_output = str(Path(selection.output_dir).resolve()) if selection.output_dir else ""
        if (
            (selected_video and selected_video != project_video)
            or selected_audio != project_audio
            or (selected_output and selected_output != project_output)
        ):
            self._clear_project()

    def _default_project_path(self) -> Path | None:
        selection = self._source_selection
        if not selection.video or not selection.output_dir:
            return None
        return derive_project_path(selection.video, selection.output_dir)

    def _clear_project(self) -> None:
        if self._project_dirty:
            self.saveProject()
        self._project = None
        self._project_path = ""
        self._project_dirty = False
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._selected_segment_index = -1
        self.projectChanged.emit()
        self.segmentsChanged.emit()
        self.historyChanged.emit()
        self.selectionChanged.emit()

    def _try_load_default_project(self) -> bool:
        if self._loading_project_sources:
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

    @Slot()
    def browseProjectFile(self) -> None:
        start_dir = self._source_selection.output_dir or str(self.workspace_root)
        path, _ = QFileDialog.getOpenFileName(
            None,
            "字幕編集プロジェクトを開く",
            start_dir,
            "Subtitle projects (*.subtitle-project.json);;JSON files (*.json)",
        )
        if path:
            self.loadProject(path)

    @Slot(str)
    def loadProject(self, path: str) -> None:
        candidate = self._local_path(path)
        self._load_project_path(candidate, update_sources=True)

    def _load_project_path(self, path: Path, *, update_sources: bool) -> bool:
        try:
            project = load_project(path)
        except (OSError, json.JSONDecodeError, SubtitleProjectError, TypeError, ValueError) as error:
            self._set_status(f"プロジェクトを開けません: {error}", "ERROR")
            return False
        self._project = project
        self._project_path = str(path.resolve())
        self._project_dirty = False
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._selected_segment_index = 0 if project.get("segments") else -1
        if update_sources:
            self._loading_project_sources = True
            try:
                video = Path(str(project.get("video", {}).get("path", "")))
                if video.is_file():
                    super().setVideoFile(str(video))
                output_dir = Path(str(project.get("output_dir", path.parent)))
                if output_dir.is_dir():
                    super().setOutputDirectory(str(output_dir))
                audio_files = [str(item.get("path", "")) for item in project.get("audio_sources", [])]
                existing_audio = [item for item in audio_files if Path(item).is_file()]
                if existing_audio:
                    super().setAudioFiles(existing_audio, False)
            finally:
                self._loading_project_sources = False
        self.projectChanged.emit()
        self.segmentsChanged.emit()
        self.historyChanged.emit()
        self.selectionChanged.emit()
        self._set_status(f"編集プロジェクトを開きました（字幕 {len(project['segments'])} 件）", "EDIT")
        return True

    def _record_history(self) -> None:
        if self._project is None:
            return
        self._undo_stack.append(deepcopy(self._project["segments"]))
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self.historyChanged.emit()

    def _mark_project_dirty(self) -> None:
        if self._project is None:
            return
        self._project_dirty = True
        self.projectChanged.emit()
        self.autosave_timer.start()

    def _replace_segments(self, segments: list[dict[str, Any]], selected_id: str | None = None) -> None:
        if self._project is None:
            return
        self._project["segments"] = segments
        validate_project(self._project)
        if selected_id:
            self._selected_segment_index = next(
                (index for index, item in enumerate(self._project["segments"]) if item["id"] == selected_id),
                -1,
            )
        elif self._selected_segment_index >= len(self._project["segments"]):
            self._selected_segment_index = len(self._project["segments"]) - 1
        self.segmentsChanged.emit()
        self.selectionChanged.emit()
        self._mark_project_dirty()

    @Slot(int)
    def selectSegment(self, index: int) -> None:
        count = len(self._project.get("segments", [])) if self._project else 0
        resolved = index if 0 <= index < count else -1
        if resolved != self._selected_segment_index:
            self._selected_segment_index = resolved
            self.selectionChanged.emit()

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
        if "text" in changes:
            updated["text"] = str(changes["text"]).strip()
            updated["manual_text"] = True
            updated.pop("words", None)
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
        if updated == current:
            return
        selected_id = current["id"]
        self._record_history()
        segments = deepcopy(self._project["segments"])
        segments[index] = normalize_segment(updated, index)
        self._replace_segments(segments, selected_id)

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
        self._record_history()
        self._replace_segments([*deepcopy(self._project["segments"]), segment], segment["id"])

    @Slot()
    def deleteSelectedSegment(self) -> None:
        if self._project is None or not 0 <= self._selected_segment_index < len(self._project["segments"]):
            return
        self._record_history()
        segments = deepcopy(self._project["segments"])
        segments.pop(self._selected_segment_index)
        self._replace_segments(segments)

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
        self._record_history()
        segments = deepcopy(self._project["segments"])
        segments[index:index + 1] = [first, second]
        self._replace_segments(segments, second["id"])

    @Slot()
    def undoSubtitleEdit(self) -> None:
        if self._project is None or not self._undo_stack:
            return
        self._redo_stack.append(deepcopy(self._project["segments"]))
        segments = self._undo_stack.pop()
        self._replace_segments(segments)
        self.historyChanged.emit()

    @Slot()
    def redoSubtitleEdit(self) -> None:
        if self._project is None or not self._redo_stack:
            return
        self._undo_stack.append(deepcopy(self._project["segments"]))
        segments = self._redo_stack.pop()
        self._replace_segments(segments)
        self.historyChanged.emit()

    @Slot()
    def saveProject(self) -> None:
        if self._project is None or not self._project_path:
            return
        try:
            save_project(self._project_path, self._project)
        except (OSError, SubtitleProjectError) as error:
            self._set_status(f"プロジェクトを保存できません: {error}", "ERROR")
            return
        self._project_dirty = False
        self.projectChanged.emit()
        self._set_status("字幕編集を保存しました", "SAVED")

    def _autosave_project(self) -> None:
        if self._project_dirty:
            self.saveProject()

    def _update_project_settings(self, settings: dict[str, Any]) -> None:
        if self._project is None:
            return
        subtitle = self._project.setdefault("subtitle_settings", {})
        subtitle.update(
            {
                "font_size": int(settings.get("subtitle_font_size", subtitle.get("font_size", 50))),
                "volume_scale_percent": float(settings.get("subtitle_volume_scale_percent", subtitle.get("volume_scale_percent", 20.0))),
                "max_gap_seconds": float(settings.get("subtitle_max_gap_seconds", subtitle.get("max_gap_seconds", 0.32))),
                "end_padding_seconds": float(settings.get("subtitle_end_padding_seconds", subtitle.get("end_padding_seconds", 0.08))),
                "min_duration_seconds": float(settings.get("subtitle_min_duration_seconds", subtitle.get("min_duration_seconds", 0.35))),
            }
        )
        self._mark_project_dirty()

    @Slot("QVariantMap")
    def buildSubtitlePreview(self, settings: dict[str, Any]) -> None:
        if self._project is None:
            return
        self._update_project_settings(settings)
        self.saveProject()
        try:
            output = build_project_ass(self._project_path)
        except (OSError, ValueError) as error:
            self._set_status(f"ASSを生成できません: {error}", "ERROR")
            return
        self._ass_path = str(output.resolve())
        self.assPathChanged.emit()
        self._set_status(f"ASSプレビューを生成しました: {output.name}", "ASS")

    def _start_command(self, command: list[str], job: str, status: str) -> None:
        self._active_job = job
        self.activeJobChanged.emit()
        self._log = f"> {subprocess.list2cmdline(command)}\n"
        self.logChanged.emit()
        self._progress = 0.02
        self.progressChanged.emit()
        self._elapsed_seconds = 0
        self._cancel_requested = False
        self.elapsedChanged.emit()
        self._set_status(status, "STARTING")
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUTF8", "1")
        environment.insert("PYTHONUNBUFFERED", "1")
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(self.workspace_root))
        self.process.start(command[0], command[1:])

    @Slot("QVariantMap")
    def startTranscription(self, settings: dict[str, Any]) -> None:
        if self._running:
            return
        self.refreshDependencies()
        selection = self._source_selection
        audio_files = [speaker["path"] for speaker in self._speakers]
        if not Path(selection.video).is_file() or not audio_files or not selection.output_dir:
            self._set_status("動画・話者音声・出力先を指定してください", "CHECK")
            return
        reference_audio = str(settings.get("reference_audio") or audio_files[0])
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
            alignment_offset_adjustment=adjustment,
        )
        self._start_command(command, "transcribe", "文字起こしを開始しています")

    @Slot("QVariantMap")
    def startProcessing(self, settings: dict[str, Any]) -> None:
        self.startTranscription(settings)

    @Slot("QVariantMap")
    def renderVideo(self, settings: dict[str, Any]) -> None:
        if self._running or self._project is None:
            return
        self.saveSettings(settings)
        self._update_project_settings(settings)
        self.saveProject()
        command = build_gui_render_command(self.gui_config_path, project_path=self._project_path)
        self._start_command(command, "render", "編集済み字幕の動画を書き出しています")

    def _process_started(self) -> None:
        self._running = True
        self.runningChanged.emit()
        self.elapsed_timer.start()
        if self._active_job == "transcribe":
            self._set_status("文字起こしと編集プロジェクト作成を実行しています", "TRANSCRIBE")
        else:
            self._set_status("編集済み字幕を動画へ焼き付けています", "ENCODE")

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
        self.elapsed_timer.stop()
        self._running = False
        self.runningChanged.emit()
        completed_job = self._active_job
        self._active_job = ""
        self.activeJobChanged.emit()
        if self._cancel_requested:
            self._set_status("処理を停止しました", "CANCELLED")
        elif exit_code == 0:
            self._progress = 1.0
            self.progressChanged.emit()
            if completed_job == "transcribe":
                loaded = self._try_load_default_project()
                self._set_status("文字起こし完了。字幕を編集できます" if loaded else "文字起こしが完了しました", "EDIT")
            else:
                self._set_status("編集済み動画の書き出しが完了しました", "COMPLETE")
        else:
            self._set_status(f"処理が終了しました（終了コード {exit_code}）", "ERROR")


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
