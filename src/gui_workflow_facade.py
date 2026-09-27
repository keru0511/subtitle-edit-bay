from __future__ import annotations

from typing import TYPE_CHECKING

import subprocess
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from PySide6.QtCore import (
    Property,
    QProcess,
    Signal,
    Slot,
)

from .gui_state import build_gui_transcribe_command
from .workflow_actions import (
    ActionCapability,
    prepare_render_request,
    render_capability,
    transcription_capability,
)
from .subtitle_project import (
    SubtitleProjectError,
    assign_project_layout_rows,
    project_work_directory,
)
from .application_logging import ProcessDiagnosticSnapshot
from .processing_progress import ProcessingProgress, parse_ffmpeg_timestamp, parse_progress_events

from .gui_feature_facade import FeatureFacade

if TYPE_CHECKING:
    from .gui import EditBayBackend


@dataclass(slots=True)
class WorkflowRuntimeState:
    """実行中の処理と文字起こし結果の統合に必要な一時状態。"""

    processing_progress: ProcessingProgress = field(default_factory=ProcessingProgress)
    ffmpeg_duration_seconds: float = 0.0
    ffmpeg_duration_from_event: bool = False
    processing_machine_event_seen: bool = False
    last_process_diagnostic: ProcessDiagnosticSnapshot | None = None
    pending_process_error: str = ""
    process_output_tail: str = ""
    transcription_merge_mode: str = ""
    transcription_preserved_project: dict[str, Any] | None = None
    transcription_preserved_project_path: str = ""
    transcription_generated_project_path: str = ""


class WorkflowFacade(FeatureFacade):
    """文字起こし・書き出し・進捗の画面窓口。"""

    actionCapabilitiesChanged = Signal()
    activeJobChanged = Signal()
    assPathChanged = Signal()
    progressDetailsChanged = Signal()

    def __init__(self, backend: "EditBayBackend") -> None:
        super().__init__(backend)
        self._state = WorkflowRuntimeState()
        backend.actionCapabilitiesChanged.connect(self.actionCapabilitiesChanged.emit)
        backend.activeJobChanged.connect(self.activeJobChanged.emit)
        backend.assPathChanged.connect(self.assPathChanged.emit)
        backend.progressDetailsChanged.connect(self.progressDetailsChanged.emit)

    @property
    def processing_progress(self) -> ProcessingProgress:
        return self._state.processing_progress

    @Property(str, notify=activeJobChanged)
    def activeJob(self) -> str:
        backend = self._backend
        return backend._active_job

    @Property("QVariantList", notify=progressDetailsChanged)
    def progressSteps(self) -> list[dict[str, Any]]:
        return self._state.processing_progress.as_list()

    @Property(int, notify=progressDetailsChanged)
    def progressPercent(self) -> int:
        return int(round(self._state.processing_progress.value * 100))

    @Property(str, notify=progressDetailsChanged)
    def progressCurrentStep(self) -> str:
        return self._state.processing_progress.current_step

    @Property(str, notify=progressDetailsChanged)
    def progressCurrentStepDisplay(self) -> str:
        for step in self._state.processing_progress.as_list():
            if step["id"] == self._state.processing_progress.current_step:
                return str(step["label"])
        return ""

    @Property(str, notify=progressDetailsChanged)
    def progressState(self) -> str:
        return self._state.processing_progress.status

    @Property(bool, notify=progressDetailsChanged)
    def progressVisible(self) -> bool:
        return bool(self._state.processing_progress.steps)

    @Property(str, notify=assPathChanged)
    def assPath(self) -> str:
        backend = self._backend
        return backend._ass_path

    @Slot("QVariantMap", str)
    def transcribeProject(self, settings: dict[str, Any], mode: str) -> None:
        backend = self._backend
        if backend._running:
            return
        selected_mode = str(mode or "").strip().lower()
        if selected_mode not in {"replace", "merge"}:
            backend._set_status("文字起こし結果の取り込み方法を選択してください", "CHECK")
            return
        if self.project_editor.project is None:
            self._reset_transcription_integration_state()
            self.startTranscription(settings, False)
            return
        if not backend.saveProject():
            return
        self._state.transcription_merge_mode = selected_mode
        self._state.transcription_preserved_project = deepcopy(self.project_editor.project)
        self._state.transcription_preserved_project_path = self.project_editor.project_path
        default_project_path = backend._default_project_path()
        if default_project_path is None:
            self._reset_transcription_integration_state()
            backend._set_status("文字起こし結果の保存先を決定できません", "ERROR")
            return
        generated_project_path = project_work_directory(default_project_path) / (
            f".{default_project_path.stem}.{uuid4().hex}.subtitle-project.json"
        )
        self.startTranscription(settings, True, str(generated_project_path))

    def _merge_preserved_transcription_segments(self) -> bool:
        backend = self._backend
        if self.project_editor.project is None or self._state.transcription_preserved_project is None:
            return False
        generated = deepcopy(self.project_editor.project)
        preserved = deepcopy(self._state.transcription_preserved_project)
        generated_segments = deepcopy(generated.get("segments", []))
        if self._state.transcription_merge_mode == "merge":
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
        elif self._state.transcription_merge_mode == "replace":
            segments = generated_segments
        else:
            return False

        preserved["segments"] = assign_project_layout_rows(
            sorted(segments, key=lambda item: (item["start"], item["end"], item["id"]))
        )
        for key in ("transcription", "transcription_context", "waveforms"):
            if key in generated:
                preserved[key] = deepcopy(generated[key])
        backend._project = preserved
        preserved_project_path = self._state.transcription_preserved_project_path or self.project_editor.project_path
        backend._project_path = preserved_project_path
        backend._apply_project_subtitle_settings(self.project_editor.project)
        backend._selected_segment_index = 0 if self.project_editor.project["segments"] else -1
        self.project_editor.save(preserved_project_path, emit=False)
        backend._project_dirty = False
        backend.workspace._sync_project_timeline()
        backend.subtitles._sync_subtitle_model()
        backend.projectChanged.emit()
        backend.projectDataChanged.emit()
        backend.segmentsChanged.emit()
        backend.selectionChanged.emit()
        return True

    def _restore_preserved_transcription_project(self) -> None:
        backend = self._backend
        if self._state.transcription_preserved_project is None:
            return
        backend._project = deepcopy(self._state.transcription_preserved_project)
        backend._project_path = self._state.transcription_preserved_project_path
        backend._apply_project_subtitle_settings(self.project_editor.project)
        self.project_editor.save(self.project_editor.project_path, emit=False)
        backend._project_dirty = False
        backend._selected_segment_index = 0 if self.project_editor.project.get("segments") else -1
        backend.subtitles._sync_subtitle_model()
        backend.workspace._sync_project_timeline()
        backend.projectChanged.emit()
        backend.projectDataChanged.emit()
        backend.segmentsChanged.emit()
        backend.selectionChanged.emit()

    def _cleanup_transcription_project_artifact(self) -> None:
        backend = self._backend
        if not self._state.transcription_generated_project_path:
            return
        try:
            Path(self._state.transcription_generated_project_path).unlink(missing_ok=True)
        except OSError as error:
            backend._record_log(
                f"一時文字起こしプロジェクトを削除できません: {error}",
                severity="WARNING",
                component="gui",
                job="transcribe",
                stage="CLEANUP",
            )
        finally:
            self._state.transcription_generated_project_path = ""

    def _reset_transcription_integration_state(self) -> None:
        self._cleanup_transcription_project_artifact()
        self._state.transcription_merge_mode = ""
        self._state.transcription_preserved_project = None
        self._state.transcription_preserved_project_path = ""

    def _start_command(self, command: list[str], job: str, status: str) -> None:
        backend = self._backend
        backend._active_job = job
        backend.activeJobChanged.emit()
        skip_steps: set[str] = set()
        if job == "render" and self.project_editor.project is not None:
            segments = self.project_editor.project.get("segments", ())
            if not isinstance(segments, (list, tuple)) or not segments:
                skip_steps.add("subtitle")
        self._state.processing_progress.start(job, skip_steps=skip_steps)
        backend.progressDetailsChanged.emit()
        if self._state.last_process_diagnostic is not None:
            self._state.last_process_diagnostic = None
            backend.lastProcessDiagnosticChanged.emit()
        self._state.pending_process_error = ""
        self._state.process_output_tail = ""
        backend._record_log(
            f"> {subprocess.list2cmdline(command)}",
            component="gui",
            job=job,
            stage="STARTING",
        )
        backend._progress = self._state.processing_progress.value if self._state.processing_progress.steps else 0.02
        backend.progressChanged.emit()
        self._state.ffmpeg_duration_seconds = 0.0
        self._state.ffmpeg_duration_from_event = False
        self._state.processing_machine_event_seen = False
        backend._elapsed_seconds = 0
        backend._cancel_requested = False
        backend.elapsedChanged.emit()
        backend._set_status(status, "STARTING")
        backend._start_process(command)

    def _has_audio_source(self, audio_files: list[str], audio_tracks: list[dict[str, Any]] | None = None) -> bool:
        backend = self._backend
        if audio_files:
            return True
        tracks = audio_tracks if audio_tracks is not None else backend._audio_tracks
        for track in tracks:
            if str(track.get("selector", "")).strip():
                return True
        return False

    def _default_video_audio_track(self, audio_tracks: list[dict[str, Any]] | None = None) -> str:
        backend = self._backend
        tracks = audio_tracks if audio_tracks is not None else backend._audio_tracks
        for track in tracks:
            selector = str(track.get("selector", "")).strip()
            if selector:
                return selector
        return ""

    def _transcription_capability(self, device: str) -> ActionCapability:
        backend = self._backend
        return transcription_capability(
            backend._dependencies,
            device=device,
            has_video=Path(backend._source_selection.video).is_file(),
            has_audio=self._has_audio_source([speaker["path"] for speaker in backend._speakers]),
            project_path=backend.projectSavePath,
            running=backend._running,
        )

    @Property("QVariantMap", notify=actionCapabilitiesChanged)
    def actionCapabilities(self) -> dict[str, Any]:
        backend = self._backend
        return self.actionCapabilitiesForDevice(str(backend._settings.get("device", "cuda")))

    @Slot(str, result="QVariantMap")
    def actionCapabilitiesForDevice(self, device: str) -> dict[str, Any]:
        backend = self._backend
        transcribe = self._transcription_capability(device)
        normal = render_capability(
            backend._dependencies,
            self.project_editor.project,
            self.project_editor.project_path,
            running=backend._running,
        )
        short = render_capability(
            backend._dependencies,
            self.project_editor.project,
            self.project_editor.project_path,
            short=True,
            running=backend._running,
        )
        needs_output = {}
        for artifact, is_short in (("normal", False), ("short", True)):
            needs_output[artifact] = (
                not backend.videoOutputDirectory
                and render_capability(
                    backend._dependencies,
                    self.project_editor.project,
                    self.project_editor.project_path,
                    short=is_short,
                    running=backend._running,
                    require_output=False,
                ).enabled
            )
        return {
            "canTranscribe": transcribe.enabled,
            "transcriptionReason": transcribe.reason,
            "canRenderNormal": normal.enabled,
            "normalRenderReason": normal.reason,
            "canRenderShort": short.enabled,
            "shortRenderReason": short.reason,
            "normalRenderNeedsOutput": needs_output["normal"],
            "shortRenderNeedsOutput": needs_output["short"],
            "canUseTranscriptionCuda": backend._dependencies.cuda,
            "canUseNvenc": backend._dependencies.nvenc,
        }

    @Slot("QVariantMap", bool)
    def startTranscription(
        self,
        settings: dict[str, Any],
        overwrite_project: bool = False,
        project_path: str | None = None,
    ) -> None:
        backend = self._backend
        if backend._running:
            return
        if project_path is None:
            self._reset_transcription_integration_state()

        def reject_start(message: str, stage: str) -> None:
            backend._set_status(message, stage)
            if project_path is not None:
                self._reset_transcription_integration_state()

        if not backend._dependencies.ready:
            backend.refreshDependencies()
        audio_tracks = list(backend._audio_tracks)
        device = str(settings.get("device") or backend._settings.get("device"))
        capability = self._transcription_capability(device)
        if not capability.enabled:
            setup_missing = not backend._dependencies.ready or (device == "cuda" and not backend._dependencies.cuda)
            reject_start(capability.reason, "SETUP" if setup_missing else "CHECK")
            return
        selection = backend._source_selection
        audio_files = [speaker["path"] for speaker in backend._speakers]
        video_audio_track = ""
        if not audio_files:
            video_audio_track = str(settings.get("reference_track") or self._default_video_audio_track(audio_tracks))
        reference_audio = settings.get("reference_audio")
        if not reference_audio and audio_files:
            reference_audio = audio_files[0]
        reference_track = str(settings.get("reference_track") or "")
        adjustment = float(settings.get("alignment_offset_adjustment") or 0.0)
        if not backend.saveSettings(settings):
            if project_path is not None:
                self._reset_transcription_integration_state()
            return
        self._state.transcription_generated_project_path = str(Path(project_path).resolve()) if project_path else ""
        command = build_gui_transcribe_command(
            backend.gui_config_path,
            video=selection.video,
            audio_files=audio_files,
            output_dir=str(project_work_directory(backend.projectSavePath)),
            render_output_dir=backend.videoOutputDirectory,
            context_base_dir=str(
                (self.project_editor.project or {}).get("transcription", {}).get("context_base_dir")
                or Path(backend.projectSavePath).parent
            ),
            reference_audio=reference_audio,
            reference_track=reference_track,
            video_audio_track=video_audio_track,
            alignment_offset_adjustment=adjustment,
            overwrite_project=overwrite_project,
            project_path=project_path or backend.projectSavePath,
        )
        self._start_command(command, "transcribe", "文字起こしを開始しています")

    @Slot("QVariantMap")
    def startProcessing(self, settings: dict[str, Any]) -> None:
        self.startTranscription(settings)

    @Slot("QVariantMap")
    def renderVideo(self, settings: dict[str, Any]) -> None:
        self._start_render(settings, short=False)

    def _start_render(self, settings: dict[str, Any], *, short: bool) -> None:
        backend = self._backend
        if backend._running or self.project_editor.project is None:
            return
        backend.refreshDependencies()
        preflight = render_capability(
            backend._dependencies,
            self.project_editor.project,
            self.project_editor.project_path,
            short=short,
            require_output=False,
        )
        if not preflight.enabled:
            backend._set_status(preflight.reason, "CHECK")
            return
        if not backend.videoOutputDirectory:
            backend.browseOutputDirectory()
            if not backend.videoOutputDirectory:
                backend._set_status("書き出しを中止しました。プロジェクトはそのまま編集できます", "CHECK")
                return
        try:
            request = prepare_render_request(
                backend._dependencies,
                self.project_editor.project,
                self.project_editor.project_path,
                backend.gui_config_path,
                short=short,
            )
        except ValueError as error:
            backend._set_status(str(error), "CHECK")
            return
        effective_settings = dict(settings)
        effective_settings["video_codec"] = request.video_codec
        if not backend.saveSettings(effective_settings):
            return
        backend._update_project_settings(effective_settings)
        if not backend.saveProject():
            return
        mode = "GPU" if request.video_codec == "h264_nvenc" else "CPU"
        artifact = "ショート動画" if short else "動画"
        self._start_command(request.command, request.job, f"{mode}を自動選択して{artifact}を書き出しています")

    @Slot()
    def renderShortVideo(self) -> None:
        backend = self._backend
        self._start_render(backend.settings, short=True)

    def _process_started(self) -> None:
        backend = self._backend
        backend._running = True
        backend.runningChanged.emit()
        backend.elapsed_timer.start()
        if backend._active_job == "transcribe":
            backend._set_status("文字起こしと編集プロジェクト作成を実行しています", "TRANSCRIBE")
        elif backend._active_job == "update":
            backend._set_status("アプリケーションを更新しています", "UPDATE")
        elif backend._active_job == "render_short":
            backend._set_status("ショート動画を書き出しています", "ENCODE")
        else:
            backend._set_status("編集済み字幕を動画へ焼き付けています", "ENCODE")

    def _read_process_output(self, output: str | None = None) -> None:
        backend = self._backend
        data = (
            output
            if output is not None
            else bytes(backend.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        )
        if not data:
            return
        normalized = data.replace("\r", "\n")
        self._state.process_output_tail = (self._state.process_output_tail + normalized)[-50_000:]
        backend._record_log(
            normalized,
            component=backend._active_job or "process",
            job=backend._active_job,
            stage=backend.stage,
            process_id=int(backend.process.processId()) or None,
        )
        self._update_stage(normalized)

    def _finish_processing_progress(self, outcome: str) -> None:
        backend = self._backend
        if not self._state.processing_progress.steps and backend._active_job:
            self._state.processing_progress.start(backend._active_job)
        if not self._state.processing_progress.steps:
            # Trackerless jobs (currently the self-update process) retain the
            # legacy scalar progress path.  A successful process still needs
            # to reach 100% when its QProcess exits cleanly.
            self._state.processing_progress.finish(outcome)
            if outcome == "completed":
                backend._progress = 1.0
                backend.progressChanged.emit()
            backend.progressDetailsChanged.emit()
            return
        self._state.processing_progress.finish(outcome)
        backend._progress = self._state.processing_progress.value
        backend.progressChanged.emit()
        backend.progressDetailsChanged.emit()

    def _process_error(self, error: QProcess.ProcessError) -> None:
        backend = self._backend
        message = backend.process.errorString() or str(error)
        self._state.pending_process_error = message
        backend._record_log(
            message,
            severity="ERROR",
            component=backend._active_job or "qprocess",
            job=backend._active_job,
            stage="ERROR",
            process_id=int(backend.process.processId()) or None,
        )
        if not backend._running and backend.process.state() == QProcess.ProcessState.NotRunning:
            self._read_process_output()
            backend._set_status(message, "ERROR")
            failed_job = backend._active_job
            backend._capture_process_diagnostic(
                job=failed_job,
                outcome="failed",
                exit_code=None,
            )
            if failed_job == "transcribe":
                self._reset_transcription_integration_state()
            self._finish_processing_progress("error")
            backend._active_job = ""
            backend.activeJobChanged.emit()

    def _update_stage(self, output: str) -> None:
        backend = self._backend
        for event in parse_progress_events(output):
            try:
                target_duration = float(event.get("duration", 0.0))
            except (TypeError, ValueError):
                target_duration = 0.0
            if target_duration > 0.0:
                self._state.ffmpeg_duration_seconds = target_duration
                self._state.ffmpeg_duration_from_event = True
            if (
                event.get("step") == "encode"
                and event.get("phase") == "start"
                and not self._state.ffmpeg_duration_from_event
            ):
                self._state.ffmpeg_duration_seconds = 0.0
            if self._state.processing_progress.update(event):
                self._state.processing_machine_event_seen = True
                backend._progress = self._state.processing_progress.value
                backend.progressChanged.emit()
                backend.progressDetailsChanged.emit()
        for line in output.splitlines():
            if "Duration:" in line and self._state.ffmpeg_duration_seconds <= 0.0:
                duration = parse_ffmpeg_timestamp(line)
                if duration and duration > 0.0:
                    self._state.ffmpeg_duration_seconds = duration
            if self._state.processing_progress.current_step != "encode":
                continue
            if "time=" not in line:
                continue
            timestamp = parse_ffmpeg_timestamp(line)
            if timestamp is None or self._state.ffmpeg_duration_seconds <= 0.0:
                continue
            encode_progress = min(1.0, timestamp / self._state.ffmpeg_duration_seconds)
            if self._state.processing_progress.update(
                {
                    "job": self._state.processing_progress.job,
                    "step": "encode",
                    "phase": "progress",
                    "progress": encode_progress,
                }
            ):
                backend._progress = self._state.processing_progress.value
                backend.progressChanged.emit()
                backend.progressDetailsChanged.emit()
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
                    self._state.processing_progress.job
                    and not self._state.processing_machine_event_seen
                    and step != "waveform"
                ):
                    tracker_updated = self._state.processing_progress.update(
                        {
                            "job": self._state.processing_progress.job,
                            "step": step,
                            "phase": "progress",
                            "progress": progress,
                        }
                    )
                if tracker_updated:
                    backend._progress = self._state.processing_progress.value
                elif not self._state.processing_machine_event_seen and (
                    step != "waveform" or not self._state.processing_progress.steps
                ):
                    backend._progress = max(backend._progress, progress)
                backend.progressChanged.emit()
                backend.progressDetailsChanged.emit()
                backend._set_status(status, stage)

    def _process_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        backend = self._backend
        self._read_process_output()
        backend.elapsed_timer.stop()
        backend._running = False
        backend.runningChanged.emit()
        completed_job = backend._active_job
        failure_detail = ""
        if exit_code != 0 and completed_job != "update":
            failure_detail = next(
                (
                    line.strip()
                    for line in reversed(self._state.process_output_tail.splitlines())
                    if line.strip() and not line.lstrip().startswith("PROGRESS_EVENT ")
                ),
                "",
            )
        finish_stage = "CANCELLED" if backend._cancel_requested else "COMPLETE" if exit_code == 0 else "ERROR"
        backend._record_log(
            f"Process finished with exit code {exit_code}",
            severity="INFO" if exit_code == 0 or backend._cancel_requested else "ERROR",
            component=completed_job or "process",
            job=completed_job,
            stage=finish_stage,
            exit_code=exit_code,
        )
        if backend._cancel_requested:
            if completed_job == "transcribe":
                self._reset_transcription_integration_state()
            self._finish_processing_progress("cancelled")
            backend._set_status("処理を停止しました", "CANCELLED")
        elif exit_code == 0:
            self._finish_processing_progress("completed")
            if completed_job == "transcribe":
                preserved_workspace = (
                    (backend.workspace.currentEditMode, backend.workspace.editorPlayhead)
                    if self._state.transcription_preserved_project is not None
                    else None
                )
                generated_project_path = (
                    Path(self._state.transcription_generated_project_path)
                    if self._state.transcription_generated_project_path
                    else None
                )
                loaded = (
                    backend._load_project_path(generated_project_path, update_sources=False)
                    if generated_project_path is not None and generated_project_path.is_file()
                    else backend._try_load_default_project()
                )
                merged = False
                integration_error = ""
                if loaded and self._state.transcription_merge_mode in {"merge", "replace"}:
                    try:
                        applied = self._merge_preserved_transcription_segments()
                        merged = applied and self._state.transcription_merge_mode == "merge"
                    except (OSError, SubtitleProjectError, TypeError, ValueError) as error:
                        integration_error = f"文字起こし結果の統合に失敗しました: {error}"
                        try:
                            self._restore_preserved_transcription_project()
                        except (OSError, SubtitleProjectError, TypeError, ValueError) as restore_error:
                            integration_error += f"（元プロジェクトの復元にも失敗しました: {restore_error}）"
                if self._state.transcription_generated_project_path and not loaded:
                    integration_error = "文字起こし結果の一時プロジェクトを読み込めませんでした"
                self._reset_transcription_integration_state()
                if preserved_workspace is not None:
                    backend.workspace.selectEditMode(preserved_workspace[0])
                    playhead = preserved_workspace[1]
                    basis = playhead["basis"]
                    backend.workspace.setEditorPlayhead(playhead[f"{basis}PositionMs"], basis)
                if integration_error:
                    backend._set_status(integration_error, "ERROR")
                else:
                    backend._set_status(
                        "文字起こし結果を既存字幕へ追加しました。内容を確認してください"
                        if merged
                        else "文字起こし完了。字幕を確認して動画へ焼き付けられます"
                        if loaded
                        else "文字起こしが完了しました。編集プロジェクトを開いてください",
                        "EDIT" if loaded else "CHECK",
                    )
            elif completed_job == "update":
                backend._set_status("更新が完了しました。アプリを再起動してください", "UPDATE")
            elif completed_job == "render_short":
                backend._set_status("ショート動画の書き出しが完了しました", "COMPLETE")
            else:
                backend._set_status("編集済み動画の書き出しが完了しました", "COMPLETE")
        else:
            if completed_job == "transcribe":
                self._reset_transcription_integration_state()
            self._finish_processing_progress("error")
            if completed_job == "update":
                backend._set_status(
                    f"更新に失敗しました（終了コード {exit_code}）。バックアップから復元されています", "ERROR"
                )
            else:
                suffix = f": {failure_detail}" if failure_detail else ""
                backend._set_status(
                    f"処理が終了しました（終了コード {exit_code}）{suffix}",
                    "ERROR",
                )
        if backend._cancel_requested or exit_code != 0:
            backend._capture_process_diagnostic(
                job=completed_job,
                outcome="cancelled" if backend._cancel_requested else "failed",
                exit_code=exit_code,
            )
        backend._active_job = ""
        backend.activeJobChanged.emit()

    @Slot()
    def cancelProcessing(self) -> None:
        backend = self._backend
        if backend._running and backend._active_job == "update":
            backend._set_status("更新処理は途中で停止できません", "UPDATE")
            return
        backend._cancel_processing()
