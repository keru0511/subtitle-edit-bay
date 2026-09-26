from __future__ import annotations

from typing import TYPE_CHECKING

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from PySide6.QtCore import (
    Property,
    Signal,
    Slot,
)
from PySide6.QtMultimedia import QAudioBuffer, QAudioBufferOutput

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
    AudioMixProposalError,
    apply_audio_mix_proposal,
    build_audio_mix_context,
    build_audio_mix_proposal_prompt,
)
from .audio_preview_cache import (
    AudioPreviewCacheResult,
)
from .gui_codex_state import (
    CodexSessionError,
)
from .gui_audio_preview_controller import AudioPreviewController

from .gui_feature_facade import FeatureFacade

if TYPE_CHECKING:
    from .gui import EditBayBackend


class AudioFacade(FeatureFacade):
    """音声プレビューとミキサーの画面窓口。"""

    audioMasterMetricsChanged = Signal()
    audioMixProposalChanged = Signal()
    audioMixerPreviewChannelsChanged = Signal()
    audioMixerPreviewGainsChanged = Signal()
    audioPreviewCacheChanged = Signal()
    audioPreviewLevelsChanged = Signal()
    projectDataChanged = Signal()

    def __init__(self, backend: "EditBayBackend") -> None:
        super().__init__(backend)
        backend.audioMasterMetricsChanged.connect(self.audioMasterMetricsChanged.emit)
        backend.audioMixProposalChanged.connect(self.audioMixProposalChanged.emit)
        backend.audioMixerPreviewChannelsChanged.connect(self.audioMixerPreviewChannelsChanged.emit)
        backend.audioMixerPreviewGainsChanged.connect(self.audioMixerPreviewGainsChanged.emit)
        backend.audioPreviewCacheChanged.connect(self.audioPreviewCacheChanged.emit)
        backend.audioPreviewLevelsChanged.connect(self.audioPreviewLevelsChanged.emit)
        backend.projectDataChanged.connect(self.projectDataChanged.emit)

    @Property("QVariantMap", notify=audioMixProposalChanged)
    def audioMixProposal(self) -> dict[str, Any]:
        backend = self._backend
        return deepcopy(backend._audio_mix_proposal or {})

    @Property(str, notify=audioMixProposalChanged)
    def audioMixProposalState(self) -> str:
        backend = self._backend
        return backend._codex_audio_mix_session.snapshot.state

    @Property(str, notify=audioMixProposalChanged)
    def audioMixProposalError(self) -> str:
        backend = self._backend
        return backend._codex_audio_mix_session.snapshot.error

    @Property(bool, notify=audioPreviewCacheChanged)
    def audioPreviewPreparing(self) -> bool:
        backend = self._backend
        return backend._audio_preview_preparing

    @Property(int, notify=audioPreviewCacheChanged)
    def audioPreviewGeneration(self) -> int:
        backend = self._backend
        return backend._audio_preview_generation

    @Property(str, notify=audioPreviewCacheChanged)
    def audioPreviewCacheSummary(self) -> str:
        backend = self._backend
        return backend._audio_preview_controller.audio_preview_cache_summary

    @Property(str, notify=audioMixerPreviewChannelsChanged)
    def audioPreviewClockUrl(self) -> str:
        backend = self._backend
        return backend._audio_preview_controller.audio_preview_clock_url

    def _reset_audio_preview_cache(self) -> None:
        backend = self._backend
        backend._audio_preview_controller.reset_cache()

    @Slot()
    def prepareAudioMixerPreview(self) -> None:
        backend = self._backend
        backend._audio_preview_controller.prepare_preview(
            ffmpeg_available=backend._dependencies.ffmpeg,
        )

    @Slot(int, object)
    def _apply_audio_preview_cache(
        self,
        request_id: int,
        result: AudioPreviewCacheResult,
    ) -> None:
        backend = self._backend
        backend._audio_preview_controller.apply_audio_preview_cache(request_id, result)

    @Slot()
    def clearAudioPreviewCache(self) -> None:
        backend = self._backend
        backend._audio_preview_controller.clear_cache()

    @Property("QVariantList", notify=projectDataChanged)
    def audioMixerChannels(self) -> list[dict[str, Any]]:
        backend = self._backend
        return backend._audio_preview_controller.mixer_channels

    @Property(bool, notify=projectDataChanged)
    def audioMixerAvailable(self) -> bool:
        return bool(self.audioMixerChannels)

    def _enabled_audio_mixer_channel_ids(self) -> set[str]:
        backend = self._backend
        return backend._audio_preview_controller.enabled_channel_ids()

    @Property(bool, notify=projectDataChanged)
    def audioMixerPreviewComplete(self) -> bool:
        backend = self._backend
        return backend._audio_preview_controller.preview_complete

    @Property(bool, notify=projectDataChanged)
    def audioMixerIntentionalSilence(self) -> bool:
        backend = self._backend
        return backend._audio_preview_controller.intentional_silence

    @Property("QVariantList", notify=projectDataChanged)
    def audioMixerSequenceChannels(self) -> list[dict[str, Any]]:
        backend = self._backend
        if backend._project is None:
            return []

        active_ids = {
            str(channel.get("id", "")) for channel in active_audio_mix_channels(backend._project.get("audio_mix", {}))
        }
        waveforms_by_path = {
            str(Path(str(waveform.get("source_path", ""))).resolve()).casefold(): waveform
            for waveform in backend._project.get("waveforms", [])
            if isinstance(waveform, dict) and waveform.get("source_path")
        }
        duration = max(
            0.0,
            float(backend._project.get("video", {}).get("duration_seconds", 0.0)),
        )
        colors = ("#6FA8DC", "#93C47D", "#F6B26B", "#E78284", "#81C8BE")
        sequence: list[dict[str, Any]] = []
        for index, channel in enumerate(backend._project.get("audio_mix", {}).get("channels", [])):
            if not isinstance(channel, dict) or not bool(channel.get("enabled")):
                continue
            view = self._audio_mixer_channel_view(channel)
            waveform = None
            if view.get("kind") == "external" and view.get("path"):
                waveform = waveforms_by_path.get(str(Path(str(view["path"])).resolve()).casefold())
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
        backend = self._backend
        return backend._audio_preview_controller.channel_view(channel)

    def _audio_mixer_preview_state(
        self,
    ) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], dict[str, float]]:
        backend = self._backend
        return backend._audio_preview_controller.preview_state()

    def _notify_audio_mixer_preview(self, *, structure_changed: bool) -> None:
        backend = self._backend
        backend._audio_preview_controller.notify_preview(structure_changed=structure_changed)

    @Property("QVariantList", notify=audioMixerPreviewChannelsChanged)
    def audioMixerPreviewChannels(self) -> list[dict[str, Any]]:
        backend = self._backend
        return backend._audio_preview_controller.preview_channels

    @Property("QVariantMap", notify=audioMixerPreviewGainsChanged)
    def audioMixerPreviewGains(self) -> dict[str, float]:
        backend = self._backend
        return backend._audio_preview_controller.preview_gains

    def _audio_preview_output(self, channel_id: str) -> QAudioBufferOutput:
        backend = self._backend
        return backend._audio_preview_controller.preview_output(channel_id)

    @staticmethod
    def _audio_buffer_peak(buffer: QAudioBuffer) -> float:
        return AudioPreviewController.audio_buffer_peak(buffer)

    def _receive_audio_preview_buffer(self, channel_id: str, buffer: QAudioBuffer) -> None:
        backend = self._backend
        backend._audio_preview_controller.receive_preview_buffer(channel_id, buffer)

    def _publish_audio_preview_levels(self) -> None:
        backend = self._backend
        backend._audio_preview_controller.publish_levels()

    @Property("QVariantMap", notify=audioPreviewLevelsChanged)
    def audioPreviewLevels(self) -> dict[str, float]:
        backend = self._backend
        return dict(backend._audio_preview_levels)

    @Slot(float, float)
    def _update_audio_master_metrics(self, level: float, reduction_db: float) -> None:
        backend = self._backend
        backend._audio_preview_controller._update_master_metrics(level, reduction_db)

    @Property(float, notify=audioMasterMetricsChanged)
    def audioMasterLevel(self) -> float:
        backend = self._backend
        return backend._audio_preview_controller.master_level

    @Property(float, notify=audioMasterMetricsChanged)
    def audioLimiterReductionDb(self) -> float:
        backend = self._backend
        return backend._audio_preview_controller.limiter_reduction_db

    @Slot(int)
    def startAudioMixerPreview(self, position_milliseconds: int) -> None:
        backend = self._backend
        backend._audio_preview_controller.start_preview(position_milliseconds)

    @Slot()
    def pauseAudioMixerPreview(self) -> None:
        backend = self._backend
        backend._audio_preview_controller.pause_preview()

    @Slot(int, bool)
    def seekAudioMixerPreview(self, position_milliseconds: int, playing: bool) -> None:
        backend = self._backend
        backend._audio_preview_controller.seek_preview(position_milliseconds, playing)

    @Slot()
    def stopAudioMixerPreview(self) -> None:
        backend = self._backend
        backend._audio_preview_controller.stop_preview()

    def _mixer_video_tracks(self) -> list[dict[str, str]]:
        backend = self._backend
        tracks = [
            {"selector": str(item.get("selector", "")), "label": str(item.get("label", ""))}
            for item in backend._audio_tracks
            if str(item.get("selector", "")).strip()
        ]
        return tracks

    def _fallback_video_tracks(self) -> list[dict[str, str]]:
        return [{"selector": DEFAULT_AUDIO_TRACK, "label": "既定の動画音声"}]

    @Slot(int, "QVariantMap")
    def updateAudioMixChannel(self, index: int, changes: dict[str, Any]) -> None:
        backend = self._backend
        if backend._project is None or backend._running:
            return
        draft = {**backend._project, "audio_sources": deepcopy(backend._project.get("audio_sources", []))}
        audio_mix = reconcile_audio_mix(draft, self._mixer_video_tracks())
        channels = audio_mix["channels"]
        if not 0 <= index < len(channels):
            backend._set_status("動画内または外部の音声トラックがありません", "CHECK")
            return
        channel = channels[index]
        channel_id = str(channel.get("id", ""))
        enabled_before = bool(channel.get("enabled"))
        try:
            updated_audio_mix = update_audio_mix_channel(audio_mix, channel_id, changes)
        except AudioMixError:
            backend._set_status("音量ミキサーの変更内容を確認してください", "CHECK")
            return
        backend._project_editor_controller.commit_section_change("audio_mix", updated_audio_mix)
        self._notify_audio_mixer_preview(
            structure_changed=enabled_before
            != bool(updated_audio_mix["channels"][index].get("enabled"))
        )
        backend._set_status("音量ミキサー設定を更新しました", "EDIT")

    def start_codex_audio_mix_proposal(
        self,
        *,
        intent: str,
        revision: int,
        context: Mapping[str, Any] | None = None,
    ) -> bool:
        backend = self._backend
        if backend._project is None or backend._running:
            backend._set_status("音量ミキサーの変更案には編集プロジェクトが必要です", "CHECK")
            return False
        if revision != backend._project_revision:
            backend._set_status("音量ミキサーの変更案が古くなっています", "CHECK")
            return False
        if backend._codex_session.running or backend._codex_audio_mix_session.running:
            backend._set_status("別のCodex変更案を処理中です", "BUSY")
            return False
        try:
            channels = self.audioMixerChannels
            proposal_context = (
                dict(context)
                if context is not None
                else build_audio_mix_context(
                    channels,
                    preview_levels=self.audioPreviewLevels,
                    master_level=self.audioMasterLevel,
                    limiter_reduction_db=self.audioLimiterReductionDb,
                    playhead_seconds=float(backend.workspace.editorPlayhead.get("sourcePositionMs", 0)) / 1000.0,
                    project_revision=revision,
                )
            )
            if proposal_context.get("project_revision") != revision:
                raise AudioMixProposalError("audio mix context is stale")
            prompt = build_audio_mix_proposal_prompt(intent)
            backend._audio_mix_proposal = None
            backend.audioMixProposalChanged.emit()
            backend._codex_audio_mix_session.start(
                prompt=prompt,
                context=proposal_context,
                output_schema=AUDIO_MIX_PROPOSAL_OUTPUT_SCHEMA,
                revision=revision,
            )
        except (AudioMixProposalError, CodexSessionError, ValueError) as error:
            backend._set_status(f"音量ミキサーの変更案を開始できません: {error}", "ERROR")
            return False
        backend._set_status("Codexへ音量ミキサーの変更案を依頼しています", "CODEX")
        return True

    @Slot(str, result=bool)
    def proposeAudioMix(self, intent: str) -> bool:
        backend = self._backend
        return self.start_codex_audio_mix_proposal(intent=intent, revision=backend._project_revision)

    @Slot()
    def stopCodexAudioMixProposal(self) -> None:
        backend = self._backend
        was_running = backend._codex_audio_mix_session.running
        backend._codex_audio_mix_session.stop()
        if was_running:
            backend._codex_chat.fail_proposal("", cancelled=True)
        backend._set_status("音量ミキサーの変更案を停止しました", "CODEX")

    @Slot("QVariantList", bool, result=bool)
    def applyAudioMixProposal(
        self,
        selected_operation_ids: list[Any] | None = None,
        allow_silence: bool = False,
    ) -> bool:
        backend = self._backend
        if backend._running:
            return False
        if backend._project is None or not backend._audio_mix_proposal:
            backend._set_status("適用する音量ミキサーの変更案がありません", "CHECK")
            return False
        if backend._codex_audio_mix_session.running:
            backend._set_status("音量ミキサーの変更案を生成中です", "BUSY")
            return False
        before = deepcopy(backend._project.get("audio_mix", {}))
        try:
            updated, _changed_ids = apply_audio_mix_proposal(
                before,
                backend._audio_mix_proposal,
                current_revision=backend._project_revision,
                selected_operation_ids=(
                    None
                    if selected_operation_ids is None
                    else {str(item) for item in selected_operation_ids}
                ),
                allow_silence=bool(allow_silence),
            )
        except (AudioMixProposalError, AudioMixError, ValueError, TypeError) as error:
            backend._set_status(f"音量ミキサーの変更案を適用できません: {error}", "ERROR")
            return False
        if updated == before:
            backend._set_status("音量ミキサーの変更はありません", "CHECK")
            return False
        backend._project_editor_controller.commit_section_change("audio_mix", updated)
        self._notify_audio_mixer_preview(structure_changed=True)
        backend._audio_mix_proposal = None
        backend.audioMixProposalChanged.emit()
        backend._set_status("音量ミキサーの変更案を適用しました。内容を確認して保存してください", "EDIT")
        return True

    @Slot()
    def discardAudioMixProposal(self) -> None:
        backend = self._backend
        backend._audio_mix_proposal = None
        backend.audioMixProposalChanged.emit()
        backend._set_status("音量ミキサーの変更案を破棄しました", "EDIT")

    @Slot()
    def resetAudioMixer(self) -> None:
        backend = self._backend
        if backend._project is None or backend._running:
            return
        draft = {**backend._project, "audio_sources": deepcopy(backend._project.get("audio_sources", []))}
        audio_mix = reset_audio_mix(draft, self._mixer_video_tracks())
        if not audio_mix["channels"]:
            backend._set_status("動画内または外部の音声トラックがありません", "CHECK")
            return
        backend._project_editor_controller.commit_section_change("audio_mix", audio_mix)
        self._notify_audio_mixer_preview(structure_changed=True)
        backend._set_status("音量ミキサーを既定値へ戻しました", "EDIT")
