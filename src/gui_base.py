from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from PySide6.QtCore import Property, QProcess, QProcessEnvironment, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication, QFileDialog

from .ass_template import (
    DEFAULT_SUBTITLE_FONT_SIZE,
    DEFAULT_SUBTITLE_OUTLINE_COLOR,
    DEFAULT_SUBTITLE_OUTLINE_THICKNESS,
)
from .color_config import normalize_rgb_color, save_speaker_color
from .craig_pipeline import (
    DEFAULT_ALIGNMENT_SAMPLE_RATE,
    DEFAULT_SUBTITLE_VOLUME_SCALE_PERCENT,
    resolve_alignment,
)
from .gui_state import (
    AUDIO_EXTENSIONS,
    SOURCE_CONFIG_KEYS,
    VIDEO_EXTENSIONS,
    SourceSelection,
    build_gui_command,
    build_gui_runtime_config,
    build_speaker_entries_from_files,
    gui_state_to_transcription_context,
    gui_transcription_context_state_from_config,
    write_gui_runtime_config,
)
from .runtime_config import DEFAULT_RUNTIME_CONFIG, load_runtime_config
from .runtime_dependencies import check_runtime_dependencies
from .transcription_web_dictionary import build_web_dictionary_candidate_metadata, HeuristicWebDictionaryProvider
from .transcribe import probe_audio_streams

APP_TITLE = "Subtitle Edit Bay"


class EditBayBackend(QApplication):
    sourceSelectionChanged = Signal()
    dependenciesChanged = Signal()
    speakersChanged = Signal()
    audioTracksChanged = Signal()
    alignmentChanged = Signal()
    alignmentComputed = Signal(object)
    alignmentFailed = Signal(str)
    settingsChanged = Signal()
    transcriptionContextChanged = Signal()
    runningChanged = Signal()
    statusChanged = Signal()
    progressChanged = Signal()
    logChanged = Signal()
    elapsedChanged = Signal()

    def __init__(self, argv: list[str], workspace_root: Path | None = None) -> None:
        super().__init__(argv)
        self.setApplicationName(APP_TITLE)
        self.setOrganizationName("Subtitle Edit Bay")

        self.workspace_root = (workspace_root or Path(__file__).resolve().parent.parent).resolve()
        self.gui_config_path = self.workspace_root / ".gui" / "runtime_config.json"
        self.color_config_path = self.workspace_root / "assets" / "speaker_colors.json"
        self._base_config = load_runtime_config(DEFAULT_RUNTIME_CONFIG)
        self._config = load_runtime_config(self.gui_config_path) if self.gui_config_path.exists() else self._base_config
        self._source_selection = SourceSelection()
        self._dependencies = check_runtime_dependencies(probe_nvenc=True)
        self._speakers: list[dict[str, str]] = []
        self._audio_tracks: list[dict[str, str]] = self._default_audio_tracks()
        self._alignment_result = self._empty_alignment_result()
        self._alignment_busy = False
        self._settings = self._settings_from_config(self._config)
        self._transcription_context = gui_transcription_context_state_from_config(self._config)
        self._running = False
        self._status = "蜍慕判繝ｻ隧ｱ閠・浹螢ｰ繝ｻ蜃ｺ蜉帛・繧呈欠螳壹＠縺ｦ縺上□縺輔＞"
        self._stage = "READY"
        self._progress = 0.0
        self._log = ""
        self._elapsed_seconds = 0
        self._cancel_requested = False
        self._alignment_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alignment")

        self.alignmentComputed.connect(self._apply_alignment_result)
        self.alignmentFailed.connect(self._apply_alignment_error)
        self.aboutToQuit.connect(self._shutdown_executor)

        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_process_output)
        self.process.started.connect(self._process_started)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)

        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(1000)
        self.elapsed_timer.timeout.connect(self._tick_elapsed)
        self._update_source_status()

    @staticmethod
    def _default_audio_tracks() -> list[dict[str, str]]:
        return [{"selector": "", "label": "No tracks"}]

    @staticmethod
    def _empty_alignment_result(status: str = "OK") -> dict[str, Any]:
        return {
            "status": status,
            "track": "",
            "detected_offset": 0.0,
            "adjustment": 0.0,
            "offset": 0.0,
            "score": 0.0,
        }

    @staticmethod
    def _local_path(value: object) -> Path:
        raw_value = str(value)
        url = value if isinstance(value, QUrl) else QUrl(raw_value)
        return Path(url.toLocalFile()) if url.isLocalFile() else Path(raw_value)

    @staticmethod
    def _normalized_gui_transcription_context(
        context: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        runtime_context = gui_state_to_transcription_context(context)
        return gui_transcription_context_state_from_config(
            {"craig_pipeline": {"transcription_context": runtime_context}}
        )

    def _settings_from_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        shared = payload.get("shared", {})
        craig = payload.get("craig_pipeline", {})
        return {
            "model": shared.get("model", "large-v3"),
            "device": shared.get("device", "cuda"),
            "compute_type": shared.get("compute_type", "float16"),
            "language": shared.get("language", "ja"),
            "nvenc_cq": int(shared.get("nvenc_cq", 18)),
            "x264_crf": int(shared.get("x264_crf", 18)),
            "subtitle_font_size": int(shared.get("subtitle_font_size", DEFAULT_SUBTITLE_FONT_SIZE)),
            "subtitle_outline_color": normalize_rgb_color(shared.get("subtitle_outline_color", DEFAULT_SUBTITLE_OUTLINE_COLOR)),
            "subtitle_outline_thickness": int(shared.get("subtitle_outline_thickness", DEFAULT_SUBTITLE_OUTLINE_THICKNESS)),
            "subtitle_volume_scale_percent": float(craig.get("subtitle_volume_scale_percent", DEFAULT_SUBTITLE_VOLUME_SCALE_PERCENT)),
            "subtitle_max_gap_seconds": float(shared.get("subtitle_max_gap_seconds", 0.1)),
            "subtitle_end_padding_seconds": float(shared.get("subtitle_end_padding_seconds", 0.08)),
            "subtitle_min_duration_seconds": float(shared.get("subtitle_min_duration_seconds", 0.35)),
            "video_codec": craig.get("video_codec", "h264_nvenc"),
            "audio_normalize": bool(craig.get("audio_normalize", True)),
            "audio_target_lufs": float(craig.get("audio_target_lufs", -16.0)),
            "cut_no_speech": bool(craig.get("cut_no_speech", False)),
            "no_speech_min_seconds": float(craig.get("no_speech_min_seconds", 1.2)),
            "speech_padding_seconds": float(craig.get("speech_padding_seconds", 0.25)),
            "postprocess_workers": int(craig.get("postprocess_workers", 4)),
            "alignment_offset_adjustment": float(craig.get("alignment_offset_adjustment", 0.0)),
        }

    @Property("QVariantMap", notify=sourceSelectionChanged)
    def sourceSelection(self) -> dict[str, Any]:
        return self._source_selection.to_dict()

    @Property("QVariantMap", notify=dependenciesChanged)
    def dependencyStatus(self) -> dict[str, Any]:
        return self._dependencies.to_dict()

    @Property("QVariantList", notify=speakersChanged)
    def speakers(self) -> list[dict[str, str]]:
        return list(self._speakers)

    @Property("QVariantList", notify=audioTracksChanged)
    def audioTracks(self) -> list[dict[str, str]]:
        return list(self._audio_tracks)

    @Property("QVariantMap", notify=alignmentChanged)
    def alignmentResult(self) -> dict[str, Any]:
        return dict(self._alignment_result)

    @Property(bool, notify=alignmentChanged)
    def alignmentBusy(self) -> bool:
        return self._alignment_busy

    @Property("QVariantMap", notify=settingsChanged)
    def settings(self) -> dict[str, Any]:
        return dict(self._settings)

    @Property("QVariantMap", notify=transcriptionContextChanged)
    def transcriptionContext(self) -> dict[str, Any]:
        return dict(self._transcription_context)

    @Property(bool, notify=runningChanged)
    def running(self) -> bool:
        return self._running

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(str, notify=statusChanged)
    def stage(self) -> str:
        return self._stage

    @Property(float, notify=progressChanged)
    def progress(self) -> float:
        return self._progress

    @Property(str, notify=logChanged)
    def logText(self) -> str:
        return self._log

    @Property(str, notify=elapsedChanged)
    def elapsed(self) -> str:
        hours, remainder = divmod(self._elapsed_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    @Property(str, notify=sourceSelectionChanged)
    def previewUrl(self) -> str:
        if not self._source_selection.video:
            return ""
        return QUrl.fromLocalFile(self._source_selection.video).toString()

    def _set_source_selection(self, selection: SourceSelection) -> None:
        previous = self._source_selection
        video_changed = previous.video != selection.video
        media_changed = video_changed or previous.audio_files != selection.audio_files
        self._source_selection = selection
        self._speakers = build_speaker_entries_from_files(selection.audio_files, self.color_config_path)

        if video_changed:
            self._probe_audio_tracks(selection.video)
        if media_changed:
            self._alignment_result = self._empty_alignment_result()
            self.alignmentChanged.emit()

        self.sourceSelectionChanged.emit()
        self.speakersChanged.emit()
        self._update_source_status()

    def _has_referenceable_audio_track(self) -> bool:
        return any(track.get("selector") for track in self._audio_tracks)

    def _has_audio_source(self, audio_files: list[str]) -> bool:
        return bool(audio_files) or self._has_referenceable_audio_track()

    def _update_source_status(self) -> None:
        if not self._dependencies.ready:
            missing = ", ".join(self._dependencies.missing())
            self._set_status(f"螳溯｡後ヤ繝ｼ繝ｫ縺御ｸ崎ｶｳ縺励※縺・∪縺・ {missing}", "SETUP")
        elif not self._source_selection.video:
            self._set_status("蜍慕判繧偵ラ繝ｭ繝・・縺励※縺上□縺輔＞", "INPUT")
        elif not self._has_audio_source(list(self._source_selection.audio_files)):
            self._set_status("隴ｭ隧ｱ閠・浹螢ｰ繧偵ラ繝ｭ繝・・縺励※縺上□縺輔＞", "INPUT")
        elif not self._source_selection.output_dir:
            self._set_status("蜃ｺ蜉帛・繝輔か繝ｫ繝繧呈欠螳壹＠縺ｦ縺上□縺輔＞", "INPUT")
        else:
            self._set_status(
                f"蜈･蜉帶ｺ門ｙ螳御ｺ・ {len(self._speakers)}莠ｺ縺ｮ隧ｱ閠・浹螢ｰ",
                "READY",
            )

    def _probe_audio_tracks(self, video_path: str) -> None:
        tracks = self._default_audio_tracks()
        if self._dependencies.ffprobe and video_path and Path(video_path).is_file():
            try:
                for audio_index, stream in enumerate(probe_audio_streams(video_path)):
                    selector = f"0:a:{audio_index}"
                    tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
                    title = str(tags.get("title", "")).strip()
                    codec = str(stream.get("codec_name", "audio"))
                    channels = stream.get("channels", "?")
                    detail = title or f"{codec} / {channels}ch"
                    tracks.append({"selector": selector, "label": f"{selector}  {detail}"})
            except (OSError, subprocess.SubprocessError, ValueError) as error:
                self._set_status(f"蜍慕判髻ｳ螢ｰ繝医Λ繝・け繧貞叙蠕励〒縺阪∪縺帙ｓ: {error}", "CHECK")
        self._audio_tracks = tracks
        self.audioTracksChanged.emit()

    @Slot()
    def refreshDependencies(self) -> None:
        self._dependencies = check_runtime_dependencies(probe_nvenc=True)
        self.dependenciesChanged.emit()
        if self._source_selection.video:
            self._probe_audio_tracks(self._source_selection.video)
        self._update_source_status()

    @Slot()
    def browseVideoFile(self) -> None:
        if self._running:
            self._set_status("蜃ｦ逅・ｸｭ縺ｯ蜈･蜉帙た繝ｼ繧ｹ繧貞､画峩縺ｧ縺阪∪縺帙ｓ", "BUSY")
            return
        start_dir = str(Path(self._source_selection.video).parent) if self._source_selection.video else str(self.workspace_root)
        path, _ = QFileDialog.getOpenFileName(
            None,
            "蜍慕判繝輔ぃ繧､繝ｫ繧帝∈謚・,
            start_dir,
            "Video files (*.mkv *.mp4 *.mov *.webm);;All files (*)",
        )
        if path:
            self.setVideoFile(path)

    @Slot(str)
    def setVideoFile(self, path: str) -> None:
        if self._running:
            self._set_status("蜃ｦ逅・ｸｭ縺ｯ蜈･蜉帙た繝ｼ繧ｹ繧貞､画峩縺ｧ縺阪∪縺帙ｓ", "BUSY")
            return
        video = self._local_path(path)
        if not video.is_file() or video.suffix.lower() not in VIDEO_EXTENSIONS:
            self._set_status("蟇ｾ蠢懊☆繧句虚逕ｻ繝輔ぃ繧､繝ｫ繧呈欠螳壹＠縺ｦ縺上□縺輔＞", "CHECK")
            return
        self._set_source_selection(replace(self._source_selection, video=str(video.resolve())))

    @Slot()
    def browseAudioFiles(self) -> None:
        if self._running:
            self._set_status("蜃ｦ逅・ｸｭ縺ｯ蜈･蜉帙た繝ｼ繧ｹ繧貞､画峩縺ｧ縺阪∪縺帙ｓ", "BUSY")
            return
        if self._source_selection.audio_files:
            start_dir = str(Path(self._source_selection.audio_files[0]).parent)
        else:
            start_dir = str(self.workspace_root)
        paths, _ = QFileDialog.getOpenFileNames(
            None,
            "隧ｱ閠・浹螢ｰ繝輔ぃ繧､繝ｫ繧帝∈謚・,
            start_dir,
            "Audio files (*.aac *.flac *.wav *.m4a);;All files (*)",
        )
        if paths:
            self.setAudioFiles(paths, True)

    @Slot("QVariantList", bool)
    def setAudioFiles(self, paths: list[Any], append: bool) -> None:
        if self._running:
            self._set_status("蜃ｦ逅・ｸｭ縺ｯ蜈･蜉帙た繝ｼ繧ｹ繧貞､画峩縺ｧ縺阪∪縺帙ｓ", "BUSY")
            return
        valid_files: list[str] = []
        for value in paths:
            audio = self._local_path(str(value))
            if audio.is_file() and audio.suffix.lower() in AUDIO_EXTENSIONS:
                valid_files.append(str(audio.resolve()))
        if not valid_files:
            self._set_status("蟇ｾ蠢懊☆繧玖ｩｱ閠・浹螢ｰ繝輔ぃ繧､繝ｫ繧呈欠螳壹＠縺ｦ縺上□縺輔＞", "CHECK")
            return

        existing = list(self._source_selection.audio_files) if append else []
        combined = sorted(
            dict.fromkeys([*existing, *valid_files]),
            key=lambda path: (Path(path).name.casefold(), path.casefold()),
        )
        self._set_source_selection(replace(self._source_selection, audio_files=tuple(combined)))

    @Slot("QVariantList")
    def importDroppedSourceFiles(self, values: list[Any]) -> None:
        if self._running:
            self._set_status("蜃ｦ逅・ｸｭ縺ｯ蜈･蜉帙た繝ｼ繧ｹ繧貞､画峩縺ｧ縺阪∪縺帙ｓ", "BUSY")
            return

        video_files: list[str] = []
        audio_files: list[str] = []
        ignored_count = 0
        for value in values:
            source = self._local_path(value)
            if not source.is_file():
                ignored_count += 1
                continue
            resolved = str(source.resolve())
            if source.suffix.lower() in VIDEO_EXTENSIONS:
                video_files.append(resolved)
            elif source.suffix.lower() in AUDIO_EXTENSIONS:
                audio_files.append(resolved)
            else:
                ignored_count += 1

        if not video_files and not audio_files:
            self._set_status("蟇ｾ蠢懊☆繧句虚逕ｻ縺ｾ縺溘・隧ｱ閠・浹螢ｰ繧偵ラ繝ｭ繝・・縺励※縺上□縺輔＞", "CHECK")
            return

        if video_files:
            self.setVideoFile(video_files[0])
        if audio_files:
            self.setAudioFiles(audio_files, True)

        skipped_videos = max(0, len(video_files) - 1)
        if skipped_videos or ignored_count:
            details: list[str] = []
            if skipped_videos:
                details.append(f"霑ｽ蜉縺ｮ蜍慕判{skipped_videos}莉ｶ")
            if ignored_count:
                details.append(f"譛ｪ蟇ｾ蠢懊ヵ繧｡繧､繝ｫ{ignored_count}莉ｶ")
            self._set_status(f"{'縲・.join(details)}繧堤┌隕悶＠縲∝ｯｾ蠢懊☆繧狗ｴ譚舌ｒ霑ｽ蜉縺励∪縺励◆", "CHECK")

    @Slot(int)
    def removeAudioFile(self, index: int) -> None:
        if self._running:
            self._set_status("蜃ｦ逅・ｸｭ縺ｯ蜈･蜉帙た繝ｼ繧ｹ繧貞､画峩縺ｧ縺阪∪縺帙ｓ", "BUSY")
            return
        audio_files = list(self._source_selection.audio_files)
        if not 0 <= index < len(audio_files):
            return
        audio_files.pop(index)
        self._set_source_selection(replace(self._source_selection, audio_files=tuple(audio_files)))

    @Slot()
    def clearAudioFiles(self) -> None:
        if self._running:
            return
        self._set_source_selection(replace(self._source_selection, audio_files=()))

    @Slot()
    def browseOutputDirectory(self) -> None:
        if self._running:
            self._set_status("蜃ｦ逅・ｸｭ縺ｯ蜃ｺ蜉帛・繧貞､画峩縺ｧ縺阪∪縺帙ｓ", "BUSY")
            return
        start_dir = self._source_selection.output_dir or str(self.workspace_root)
        folder = QFileDialog.getExistingDirectory(None, "蜃ｺ蜉帛・繧帝∈謚・, start_dir)
        if folder:
            self.setOutputDirectory(folder)

    @Slot(str)
    def setOutputDirectory(self, path: str) -> None:
        if self._running:
            self._set_status("蜃ｦ逅・ｸｭ縺ｯ蜃ｺ蜉帛・繧貞､画峩縺ｧ縺阪∪縺帙ｓ", "BUSY")
            return
        output = self._local_path(path)
        if not output.is_dir():
            self._set_status("蟄伜惠縺吶ｋ蜃ｺ蜉帙ヵ繧ｩ繝ｫ繝繧呈欠螳壹＠縺ｦ縺上□縺輔＞", "CHECK")
            return
        self._set_source_selection(replace(self._source_selection, output_dir=str(output.resolve())))

    @Slot()
    def resetSources(self) -> None:
        if self._running:
            return
        self._source_selection = SourceSelection()
        self._speakers = []
        self._audio_tracks = self._default_audio_tracks()
        self._alignment_result = self._empty_alignment_result()
        self.sourceSelectionChanged.emit()
        self.speakersChanged.emit()
        self.audioTracksChanged.emit()
        self.alignmentChanged.emit()
        self._update_source_status()

    def _source_speaker_color_updated(self, speaker: dict[str, str]) -> None:
        pass

    @Slot(int, str)
    def updateSpeakerColor(self, index: int, color: str) -> None:
        if self._running or not 0 <= index < len(self._speakers):
            return
        speaker = self._speakers[index]
        try:
            normalized = normalize_rgb_color(color)
            save_speaker_color(
                self.color_config_path,
                file_name=speaker.get("file_name", ""),
                speaker_name=speaker.get("name", ""),
                color=normalized,
            )
        except (OSError, ValueError, TypeError) as error:
            self._set_status(f"隧ｱ閠・牡繧剃ｿ晏ｭ倥〒縺阪∪縺帙ｓ: {error}", "ERROR")
            return
        updated = {**speaker, "color": normalized}
        self._speakers[index] = updated
        self.speakersChanged.emit()
        self._source_speaker_color_updated(updated)
        self._set_status(f"{updated.get('name', '隧ｱ閠・)} 縺ｮ蟄怜ｹ戊牡繧剃ｿ晏ｭ倥＠縺ｾ縺励◆", "SAVED")

    @Slot(str, str, float)
    def analyzeAlignment(self, reference_audio: str, reference_track: str, adjustment: float) -> None:
        if self._running:
            self._set_status("蜃ｦ逅・ｸｭ縺ｯ蜷梧悄隗｣譫舌ｒ髢句ｧ九〒縺阪∪縺帙ｓ", "BUSY")
            return
        if self._alignment_busy:
            return
        self.refreshDependencies()
        if not self._dependencies.ffmpeg or not self._dependencies.ffprobe:
            self._set_status("蜷梧悄隗｣譫舌↓縺ｯffmpeg縺ｨffprobe縺悟ｿ・ｦ√〒縺・, "SETUP")
            return
        video = self._source_selection.video
        if not Path(video).is_file() or not Path(reference_audio).is_file():
            self._set_status("蜷梧悄隗｣譫舌↓縺ｯ蜍慕判縺ｨ蝓ｺ貅夜浹螢ｰ縺悟ｿ・ｦ√〒縺・, "CHECK")
            return

        self._alignment_busy = True
        self._alignment_result = self._empty_alignment_result("隗｣譫蝉ｸｭ")
        self._alignment_result["adjustment"] = float(adjustment)
        self.alignmentChanged.emit()
        self._set_status("蝓ｺ貅夜浹螢ｰ縺ｨ蜍慕判繝医Λ繝・け繧貞酔譛溯ｧ｣譫舌＠縺ｦ縺・∪縺・, "ALIGN")
        future = self._alignment_executor.submit(
            self._calculate_alignment,
            video,
            reference_audio,
            reference_track,
            float(adjustment),
        )
        future.add_done_callback(self._alignment_finished)

    def _calculate_alignment(
        self,
        video: str,
        reference_audio: str,
        reference_track: str,
        adjustment: float,
    ) -> dict[str, Any]:
        matched_track, detected_offset, score = resolve_alignment(
            video,
            reference_audio,
            reference_track or None,
            DEFAULT_ALIGNMENT_SAMPLE_RATE,
        )
        return {
            "status": "隗｣譫仙ｮ御ｺ・,
            "track": matched_track,
            "detected_offset": detected_offset,
            "adjustment": adjustment,
            "offset": detected_offset + adjustment,
            "score": score,
        }

    def _alignment_finished(self, future: Future[dict[str, Any]]) -> None:
        try:
            self.alignmentComputed.emit(future.result())
        except Exception as error:
            self.alignmentFailed.emit(str(error))

    @Slot(object)
    def _apply_alignment_result(self, result: dict[str, Any]) -> None:
        self._alignment_busy = False
        self._alignment_result = dict(result)
        self.alignmentChanged.emit()
        if self._running or self._stage == "ERROR":
            return
        self._set_status(
            f"蜷梧悄螳御ｺ・ {result['track']} / offset {float(result['offset']):+.3f}s",
            "READY",
        )

    @Slot(str)
    def _apply_alignment_error(self, message: str) -> None:
        self._alignment_busy = False
        self._alignment_result = self._empty_alignment_result("隗｣譫仙､ｱ謨・)
        self._alignment_result["error"] = message
        self.alignmentChanged.emit()
        if self._running or self._stage == "ERROR":
            return
        self._set_status(f"蜷梧悄隗｣譫舌↓螟ｱ謨励＠縺ｾ縺励◆: {message}", "ERROR")

    @Slot("QVariantMap")
    def setTranscriptionContext(self, context: dict[str, Any]) -> None:
        if self._running:
            self._set_status("蜃ｦ逅・ｸｭ縺ｯ譁・ｭ苓ｵｷ縺薙＠霎樊嶌險ｭ螳壹ｒ螟画峩縺ｧ縺阪∪縺帙ｓ", "BUSY")
            return
        try:
            self._transcription_context = self._normalized_gui_transcription_context(context)
        except (TypeError, ValueError) as error:
            self._set_status(f"譁・ｭ苓ｵｷ縺薙＠霎樊嶌險ｭ螳壹ｒ譖ｴ譁ｰ縺ｧ縺阪∪縺帙ｓ: {error}", "ERROR")
            return
        self.transcriptionContextChanged.emit()
        self._set_status("譁・ｭ苓ｵｷ縺薙＠霎樊嶌險ｭ螳壹ｒ譖ｴ譁ｰ縺励∪縺励◆", "SAVED")

    @Slot("QVariantMap")
    def refreshWebDictionaryCandidates(self, context: dict[str, Any]) -> None:
        if self._running:
            self._set_status("蜃ｦ逅・ｸｭ縺ｯ譁・ｭ苓ｵｷ縺薙＠霎樊嶌險ｭ螳壹ｒ螟画峩縺ｧ縺阪∪縺帙ｓ", "BUSY")
            return
        try:
            runtime_context = gui_state_to_transcription_context(context)
        except (TypeError, ValueError) as error:
            self._set_status(f"Web候補を再読込できませんでした: {error}", "ERROR")
            return

        provider = HeuristicWebDictionaryProvider(max_snippets=3)
        candidate_records = provider.build_candidate_records(
            runtime_context["game_title"],
            runtime_context["game_notes"],
            max_terms=40,
        )
        candidate_metadata = [record.to_dict() for record in candidate_records]
        candidate_terms = [record["term"] for record in candidate_metadata]
        selected_lookup = {str(term).casefold() for term in runtime_context["web_dictionary_terms"]}
        selected_terms = [term for term in candidate_terms if term.casefold() in selected_lookup]

        refreshed = dict(runtime_context)
        refreshed["web_dictionary_candidates"] = candidate_terms
        refreshed["web_dictionary_terms"] = selected_terms
        refreshed["web_dictionary_candidate_metadata"] = candidate_metadata
        self._transcription_context = self._normalized_gui_transcription_context(refreshed)
        self.transcriptionContextChanged.emit()
        self._set_status("Web候補を再読込しました", "READY")

    @Slot("QVariantMap")
    def saveSettings(self, settings: dict[str, Any]) -> None:
        persistent_settings = dict(settings)
        incoming_context = persistent_settings.pop("transcription_context", None)
        if incoming_context is not None:
            try:
                self._transcription_context = self._normalized_gui_transcription_context(
                    incoming_context
                )
            except (TypeError, ValueError) as error:
                self._set_status(f"譁・ｭ苓ｵｷ縺薙＠霎樊嶌險ｭ螳壹ｒ菫晏ｭ倥〒縺阪∪縺帙ｓ: {error}", "ERROR")
                return
            self.transcriptionContextChanged.emit()

        for key in SOURCE_CONFIG_KEYS:
            persistent_settings.pop(key, None)
        self._settings.update(persistent_settings)
        try:
            transcription_context = gui_state_to_transcription_context(self._transcription_context)
        except (TypeError, ValueError) as error:
            self._set_status(f"譁・ｭ苓ｵｷ縺薙＠霎樊嶌險ｭ螳壹ｒ菫晏ｭ倥〒縺阪∪縺帙ｓ: {error}", "ERROR")
            return
        payload = build_gui_runtime_config(
            self._base_config,
            self._settings,
            self._speakers,
            transcription_context=transcription_context,
        )
        write_gui_runtime_config(self.gui_config_path, payload)
        self._config = payload
        self.settingsChanged.emit()
        self._set_status("GUI險ｭ螳壹ｒ菫晏ｭ倥＠縺ｾ縺励◆", "SAVED")

    @Slot("QVariantMap")
    def startProcessing(self, settings: dict[str, Any]) -> None:
        if self._running:
            return

        self.refreshDependencies()
        if not self._dependencies.ready:
            missing = ", ".join(self._dependencies.missing())
            self._set_status(f"螳溯｡後〒縺阪∪縺帙ｓ縲ゅう繝ｳ繧ｹ繝医・繝ｫ縺悟ｿ・ｦ√〒縺・ {missing}", "SETUP")
            return

        selection = self._source_selection
        audio_files = [speaker["path"] for speaker in self._speakers]
        if not Path(selection.video).is_file() or not self._has_audio_source(audio_files):
            self._set_status("蜍慕判縺ｨ1縺､莉･荳翫・隧ｱ閠・浹螢ｰ繧呈欠螳壹＠縺ｦ縺上□縺輔＞", "CHECK")
            return
        if not selection.output_dir:
            self._set_status("蜃ｺ蜉帛・繧呈欠螳壹＠縺ｦ縺上□縺輔＞", "CHECK")
            return

        reference_audio = str(settings.get("reference_audio") or "")
        reference_track = str(settings.get("reference_track") or "")
        adjustment = float(settings.get("alignment_offset_adjustment") or 0.0)
        self.saveSettings(settings)
        command = build_gui_command(
            self.gui_config_path,
            video=selection.video,
            audio_files=audio_files,
            output_dir=selection.output_dir,
            reference_audio=reference_audio,
            reference_track=reference_track,
            alignment_offset_adjustment=adjustment,
        )
        self._log = f"> {subprocess.list2cmdline(command)}\n"
        self.logChanged.emit()
        self._progress = 0.02
        self.progressChanged.emit()
        self._elapsed_seconds = 0
        self._cancel_requested = False
        self.elapsedChanged.emit()
        self._set_status("繝代う繝励Λ繧､繝ｳ繧定ｵｷ蜍輔＠縺ｦ縺・∪縺・, "STARTING")

        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUTF8", "1")
        environment.insert("PYTHONUNBUFFERED", "1")
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(self.workspace_root))
        self.process.start(command[0], command[1:])

    @Slot()
    def cancelProcessing(self) -> None:
        if not self._running:
            return
        self._cancel_requested = True
        self._set_status("蛛懈ｭ｢繧定ｦ∵ｱゅ＠縺ｦ縺・∪縺・, "STOPPING")
        if os.name == "nt" and self.process.processId():
            subprocess.run(
                ["taskkill", "/PID", str(self.process.processId()), "/T"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
        else:
            self.process.terminate()
        QTimer.singleShot(5000, self._kill_if_running)

    def _kill_if_running(self) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            if os.name == "nt" and self.process.processId():
                subprocess.run(
                    ["taskkill", "/PID", str(self.process.processId()), "/T", "/F"],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    check=False,
                )
            else:
                self.process.kill()

    @Slot()
    def openOutputFolder(self) -> None:
        if not self._source_selection.output_dir:
            self._set_status("蜈医↓蜃ｺ蜉帛・繝輔か繝ｫ繝繧呈欠螳壹＠縺ｦ縺上□縺輔＞", "CHECK")
            return
        output = Path(self._source_selection.output_dir)
        try:
            output.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self._set_status(f"蜃ｺ蜉帛・繝輔か繝ｫ繝繧呈ｺ門ｙ縺ｧ縺阪∪縺帙ｓ: {error}", "ERROR")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(output))):
            self._set_status("蜃ｺ蜉帛・繝輔か繝ｫ繝繧帝幕縺代∪縺帙ｓ縺ｧ縺励◆", "ERROR")

    def _process_started(self) -> None:
        self._running = True
        self.runningChanged.emit()
        self.elapsed_timer.start()
        self._set_status("髻ｳ螢ｰ縺ｨ譏蜒上ｒ隗｣譫舌＠縺ｦ縺・∪縺・, "ALIGN")

    def _read_process_output(self) -> None:
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if not data:
            return
        normalized = data.replace("\r", "\n")
        self._log = (self._log + normalized)[-50000:]
        self.logChanged.emit()
        self._update_stage(normalized)

    def _update_stage(self, output: str) -> None:
        markers = [
            ("Resolving alignment", "ALIGN", "蝓ｺ貅夜浹螢ｰ繧貞酔譛溘＠縺ｦ縺・∪縺・, 0.08),
            ("Starting WhisperX", "WHISPERX", "GPU縺ｧ譁・ｭ苓ｵｷ縺薙＠縺励※縺・∪縺・, 0.22),
            ("CPU postprocess", "LAYOUT", "蟄怜ｹ輔ｒ謨ｴ蠖｢縺励※縺・∪縺・, 0.58),
            ("Refining merged", "LAYOUT", "蟄怜ｹ輔・繝ｼ繧ｸ繧堤ｵ・∩遶九※縺ｦ縺・∪縺・, 0.66),
            ("Writing ASS", "ASS", "蟄怜ｹ輔せ繧ｿ繧､繝ｫ繧呈嶌縺榊・縺励※縺・∪縺・, 0.72),
            ("Detecting speech", "SPEECH", "逋ｺ隧ｱ蛹ｺ髢薙ｒ讀懷・縺励※縺・∪縺・, 0.78),
            ("Rendering subtitles", "ENCODE", "蟄怜ｹ輔ｒ辟ｼ縺崎ｾｼ縺ｿ縺ｪ縺後ｉ蜃ｺ蜉帙＠縺ｦ縺・∪縺・, 0.84),
            ("Burning subtitles", "ENCODE", "蟄怜ｹ輔ｒ辟ｼ縺崎ｾｼ縺ｿ縺ｪ縺後ｉ蜃ｺ蜉帙＠縺ｦ縺・∪縺・, 0.84),
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
        if self._cancel_requested:
            self._set_status("蜃ｦ逅・ｒ蛛懈ｭ｢縺励∪縺励◆", "CANCELLED")
        elif exit_code == 0:
            self._progress = 1.0
            self.progressChanged.emit()
            self._set_status("蜍慕判縺ｮ逕滓・縺悟ｮ御ｺ・＠縺ｾ縺励◆", "COMPLETE")
        else:
            self._set_status(f"蜃ｦ逅・′邨ゆｺ・＠縺ｾ縺励◆・育ｵゆｺ・さ繝ｼ繝・{exit_code}・・, "ERROR")

    def _process_error(self, _error: QProcess.ProcessError) -> None:
        if not self._running and self.process.state() == QProcess.ProcessState.NotRunning:
            self._set_status(self.process.errorString(), "ERROR")

    def _tick_elapsed(self) -> None:
        self._elapsed_seconds += 1
        self.elapsedChanged.emit()

    def _set_status(self, status: str, stage: str) -> None:
        self._status = status
        self._stage = stage
        self.statusChanged.emit()

    def _shutdown_executor(self) -> None:
        self._alignment_executor.shutdown(wait=False, cancel_futures=True)


def main() -> None:
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    app = EditBayBackend(sys.argv)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", app)
    qml_path = Path(__file__).resolve().parent / "ui" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    if not engine.rootObjects():
        raise SystemExit(f"Could not load GUI: {qml_path}")
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()

