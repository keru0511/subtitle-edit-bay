"""旧バックエンドのPython・Qt公開APIを機能別窓口へ接続する。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from PySide6.QtCore import QProcess, Slot
from PySide6.QtMultimedia import QAudioBuffer, QAudioBufferOutput

from .audio_preview_cache import AudioPreviewCacheResult
from .codex_actions import ActionResult, ActionScope
from .codex_app_server_client import CodexAppServerClient
from .editor_workspace import EditModeCapabilities, TimeMapping
from .gemini_acp_provider import GeminiAcpProvider
from .gui_codex_chat_state import CodexChatSnapshot
from .gui_codex_state import CodexSessionSnapshot
from .video_sequence import VideoSequence
from .video_timeline import VideoTimeline
from .workflow_actions import ActionCapability


class LegacyBackendCompatibility:
    """旧APIの利用者を保ち、処理と状態の所有権は各機能窓口に置く。"""

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

    def _cut_timeline_model(self) -> VideoTimeline:
        return self._workspace_facade._cut_timeline_model()

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

    @Slot(int, result="QVariantMap")
    def shortVideoClipAt(self, index: int) -> dict[str, Any]:
        return self._short_video_facade.shortVideoClipAt(index)

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

    def _preview_text_for_segment(self, segment: dict[str, Any]) -> str:
        return self._subtitles_facade._preview_text_for_segment(segment)

    def _segment_view(self, segment: dict[str, Any], source_index: int | None = None) -> dict[str, Any]:
        return self._subtitles_facade._segment_view(segment, source_index)

    def _find_segment_by_id(self, segment_id: str) -> dict[str, Any] | None:
        return self._subtitles_facade._find_segment_by_id(segment_id)

    def _short_video_clip_count(self) -> int:
        return self._short_video_facade._short_video_clip_count()

    def _short_video_clip_view_at(self, index: int) -> dict[str, Any]:
        return self._short_video_facade._short_video_clip_view_at(index)

    def _refresh_short_video_clip_data(self) -> None:
        return self._short_video_facade._refresh_short_video_clip_data()

    def _build_short_video_clip_view(self, clip: dict[str, Any], index: int) -> dict[str, Any]:
        return self._short_video_facade._build_short_video_clip_view(clip, index)

    @Slot()
    def initializeShortVideoClips(self) -> None:
        return self._short_video_facade.initializeShortVideoClips()

    @Slot(str, result=bool)
    def addShortVideoClip(self, segment_id: str) -> bool:
        return self._short_video_facade.addShortVideoClip(segment_id)

    @Slot(float, float, result=bool)
    def addShortVideoClipByRange(self, start: float, end: float) -> bool:
        return self._short_video_facade.addShortVideoClipByRange(start, end)

    @Slot(int, result=bool)
    def removeShortVideoClip(self, index: int) -> bool:
        return self._short_video_facade.removeShortVideoClip(index)

    @Slot(int, int, result=bool)
    def moveShortVideoClip(self, from_index: int, to_index: int) -> bool:
        return self._short_video_facade.moveShortVideoClip(from_index, to_index)

    @Slot(int, "QVariantMap", result=bool)
    def updateShortVideoClip(self, index: int, fields: dict[str, Any]) -> bool:
        return self._short_video_facade.updateShortVideoClip(index, fields)

    @Slot(str, result=bool)
    def setShortVideoGlobalFit(self, fit: str) -> bool:
        return self._short_video_facade.setShortVideoGlobalFit(fit)

    @Slot(str, result=bool)
    def setShortVideoGlobalBackgroundColor(self, color: str) -> bool:
        return self._short_video_facade.setShortVideoGlobalBackgroundColor(color)

    @Slot(str, float, result=bool)
    def setShortVideoTransition(self, transition_type: str, duration: float) -> bool:
        return self._short_video_facade.setShortVideoTransition(transition_type, duration)

    @Slot("QVariantMap", result=bool)
    def setShortVideoBgm(self, fields: dict[str, Any]) -> bool:
        return self._short_video_facade.setShortVideoBgm(fields)

    @Slot(int, int, int, result=bool)
    def setShortVideoOutput(self, width: int, height: int, fps: int) -> bool:
        return self._short_video_facade.setShortVideoOutput(width, height, fps)

    @Slot(float, result=bool)
    def setShortVideoSubtitleScale(self, percent: float) -> bool:
        return self._short_video_facade.setShortVideoSubtitleScale(percent)

    @Slot(result=bool)
    def startHighlightAnalysis(self) -> bool:
        return self._short_video_facade.startHighlightAnalysis()

    @Slot(result=bool)
    def cancelHighlightAnalysis(self) -> bool:
        return self._short_video_facade.cancelHighlightAnalysis()

    @Slot(result=bool)
    def retryHighlightAnalysis(self) -> bool:
        return self._short_video_facade.retryHighlightAnalysis()

    @Slot(int, result=bool)
    def addHighlightCandidate(self, index: int) -> bool:
        return self._short_video_facade.addHighlightCandidate(index)

    @Slot(int, result=bool)
    def rejectHighlightCandidate(self, index: int) -> bool:
        return self._short_video_facade.rejectHighlightCandidate(index)

    @Slot(result=bool)
    def undoHighlightRejection(self) -> bool:
        return self._short_video_facade.undoHighlightRejection()

    def _is_current_highlight_run(self, generation: int) -> bool:
        return self._short_video_facade._is_current_highlight_run(generation)

    def _update_highlight_progress(self, generation: int, value: float) -> None:
        return self._short_video_facade._update_highlight_progress(generation, value)

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

    def _enabled_audio_mixer_channel_ids(self) -> set[str]:
        return self._audio_facade._enabled_audio_mixer_channel_ids()

    def _audio_mixer_channel_view(self, channel: dict[str, Any]) -> dict[str, Any]:
        return self._audio_facade._audio_mixer_channel_view(channel)

    def _audio_mixer_preview_state(
        self,
    ) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], dict[str, float]]:
        return self._audio_facade._audio_mixer_preview_state()

    def _notify_audio_mixer_preview(self, *, structure_changed: bool) -> None:
        return self._audio_facade._notify_audio_mixer_preview(structure_changed=structure_changed)

    def _audio_preview_output(self, channel_id: str) -> QAudioBufferOutput:
        return self._audio_facade._audio_preview_output(channel_id)

    def _receive_audio_preview_buffer(self, channel_id: str, buffer: QAudioBuffer) -> None:
        return self._audio_facade._receive_audio_preview_buffer(channel_id, buffer)

    def _publish_audio_preview_levels(self) -> None:
        return self._audio_facade._publish_audio_preview_levels()

    @Slot(float, float)
    def _update_audio_master_metrics(self, level: float, reduction_db: float) -> None:
        return self._audio_facade._update_audio_master_metrics(level, reduction_db)

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

    def _apply_project_speaker_color(self, index: int, color: str) -> bool:
        return self._subtitles_facade._apply_project_speaker_color(index, color)

    def _source_speaker_color_updated(self, speaker: dict[str, str]) -> None:
        return self._subtitles_facade._source_speaker_color_updated(speaker)

    @Slot(int, str)
    def updateProjectSpeakerColor(self, index: int, color: str) -> None:
        return self._subtitles_facade.updateProjectSpeakerColor(index, color)

    @Slot(result=str)
    def browseShortModeBgm(self) -> str:
        return self._short_video_facade.browseShortModeBgm()

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

    def _mark_project_dirty(self) -> None:
        return self._subtitles_facade._mark_project_dirty()

    def _commit_segment_change(
        self,
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
        selected_id: str | None = None,
        *,
        reflow_layout: bool = True,
    ) -> None:
        return self._subtitles_facade._commit_segment_change(before, after, selected_id, reflow_layout=reflow_layout)

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

    @Slot()
    def reportPendingInputMethod(self) -> None:
        self._set_status("入力中の文字を確定してから、もう一度操作してください", "CHECK")

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
