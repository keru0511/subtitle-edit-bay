from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


UI_ROOT = Path(__file__).resolve().parents[1] / "src" / "ui"
ENTRYPOINT_QML = UI_ROOT / "Main.qml"
WORKFLOW_QML = UI_ROOT / "screens" / "MainWorkflowScreen.qml"
WORKFLOW_WRAPPER_QML = UI_ROOT / "screens" / "MainWorkflowScreenWithContext.qml"
COMPONENTS_ROOT = UI_ROOT / "components"
SHARED_CONTROL_QML_FILES = (
    COMPONENTS_ROOT / "PanelTitle.qml",
    COMPONENTS_ROOT / "SmallButton.qml",
    COMPONENTS_ROOT / "CompactSpinBox.qml",
    COMPONENTS_ROOT / "TimeField.qml",
    COMPONENTS_ROOT / "CodexChatPanel.qml",
)


def read_entrypoint_qml() -> str:
    return ENTRYPOINT_QML.read_text(encoding="utf-8")


def read_workflow_qml() -> str:
    return WORKFLOW_QML.read_text(encoding="utf-8")


def read_workflow_wrapper_qml() -> str:
    return WORKFLOW_WRAPPER_QML.read_text(encoding="utf-8")


def read_component_qml(name: str) -> str:
    return (COMPONENTS_ROOT / name).read_text(encoding="utf-8")


class QmlStaticTests(unittest.TestCase):
    def test_qml_files_pass_qmllint_without_warnings(self) -> None:
        executable_name = "pyside6-qmllint.exe" if os.name == "nt" else "pyside6-qmllint"
        bundled = Path(sys.executable).with_name(executable_name)
        executable = str(bundled) if bundled.is_file() else shutil.which(executable_name)
        self.assertTrue(executable, "pyside6-qmllint is required with PySide6")

        for qml_path in (ENTRYPOINT_QML, WORKFLOW_QML, WORKFLOW_WRAPPER_QML, *SHARED_CONTROL_QML_FILES):
            with self.subTest(qml_path=qml_path):
                result = subprocess.run(
                    [str(executable), str(qml_path)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )

                output = (result.stdout + result.stderr).strip()
                self.assertEqual(result.returncode, 0, output)
                self.assertNotIn("Warning:", output)

    def test_main_qml_is_thin_entrypoint_for_workflow_screen(self) -> None:
        qml = read_entrypoint_qml()
        wrapper = read_workflow_wrapper_qml()

        self.assertIn('import "screens"', qml)
        self.assertIn("MainWorkflowScreenWithContext {}", qml)
        self.assertIn("MainWorkflowScreen {", wrapper)
        self.assertNotIn("ApplicationWindow", qml)

    def test_shared_controls_are_available_as_standalone_components(self) -> None:
        for qml_path in SHARED_CONTROL_QML_FILES:
            with self.subTest(qml_path=qml_path):
                self.assertTrue(qml_path.is_file())

        self.assertIn("Text {", read_component_qml("PanelTitle.qml"))
        self.assertIn("Button {", read_component_qml("SmallButton.qml"))
        self.assertIn("SpinBox {", read_component_qml("CompactSpinBox.qml"))
        self.assertIn("TextField {", read_component_qml("TimeField.qml"))
        self.assertIn('font.family: "Yu Gothic UI"', read_component_qml("PanelTitle.qml"))
        self.assertIn('font.family: "Cascadia Mono"', read_component_qml("CompactSpinBox.qml"))
        self.assertIn("DoubleValidator", read_component_qml("TimeField.qml"))

    def test_caption_font_selector_is_wired_to_backend(self) -> None:
        qml = read_workflow_qml()
        overlay_qml = read_component_qml("SubtitleOverlay.qml")

        self.assertIn('objectName: "captionFontCombo"', qml)
        self.assertIn("model: root.appBackend.fontChoices", qml)
        self.assertIn('"subtitle_font_family": currentValue', qml)
        self.assertIn('font.family: segmentData.subtitle_font_family || "Yu Gothic UI"', overlay_qml)

    def test_caption_editor_supports_manual_breaks_and_live_formatted_preview(self) -> None:
        qml = read_workflow_qml()

        self.assertIn('objectName: "captionTextArea"', qml)
        self.assertIn("wrapMode: TextEdit.Wrap", qml)
        self.assertIn("text: captionRow.editorText", qml)
        self.assertIn("root.updateSubtitleDraft(captionRow.index, text)", qml)
        self.assertIn("root.appBackend.formatSubtitlePreview(sourceIndex, root.editorDraftText)", qml)
        self.assertIn("subtitleTextResolver: function(segmentData)", qml)
        self.assertNotIn('onEditingFinished: root.appBackend.updateSegment(captionRow.index, {"text": text})', qml)

    def test_timeline_delegate_and_position_handlers_are_safe_during_refresh(self) -> None:
        qml = read_workflow_qml()

        self.assertIn("property var segment: modelData && modelData.segment ? modelData.segment : ({})", qml)
        self.assertIn("visible: sourceIndex >= 0 && segment.start !== undefined", qml)
        self.assertIn("mainSeek.value = mainPlayer.position", qml)
        self.assertIn("editorSeek.value = editorPlayer.position", qml)

    def test_caption_size_control_and_source_labels_are_readable_and_consistent(self) -> None:
        qml = read_workflow_qml()

        self.assertIn("component CompactSpinBox: SpinBox", qml)
        self.assertIn('objectName: "captionSizeSpin"', qml)
        self.assertNotIn('objectName: "sourcePanelSetupButton"', qml)
        self.assertNotIn('text: "素材を変更"', qml)

    def test_header_exposes_application_information_for_troubleshooting(self) -> None:
        qml = read_workflow_qml()

        self.assertIn("applicationInfo.version", qml)
        self.assertNotIn('objectName: "copyApplicationInfoButton"', qml)

    def test_codex_edit_panel_exposes_prompt_scope_diff_and_apply_actions(self) -> None:
        qml = read_workflow_qml()
        panel = read_component_qml("CodexEditPanel.qml")

        self.assertIn('objectName: "codexEditPanel"', qml)
        self.assertIn('objectName: "codexPromptInput"', panel)
        self.assertIn('objectName: "codexScopeCombo"', panel)
        self.assertIn('objectName: "codexSendButton"', panel)
        self.assertIn('objectName: "codexStopButton"', panel)
        self.assertIn('objectName: "codexProposalList"', panel)
        self.assertIn('objectName: "codexApplyButton"', panel)
        self.assertIn('objectName: "codexDiscardButton"', panel)
        self.assertIn("backend.applyCodexProposal", panel)
        self.assertIn("selectedOperationState", panel)
        self.assertIn("operationIdFor", panel)
        self.assertIn("!panel.codexRunning()", panel)
        self.assertIn("setCodexCurrentTime", panel)

    def test_codex_chat_exposes_login_toggle_models_stream_and_errors(self) -> None:
        qml = read_workflow_qml()
        panel = read_component_qml("CodexChatPanel.qml")

        self.assertIn('objectName: "codexChatPanel"', qml)
        self.assertIn(
            "visible: !root.editorMode && !root.mixerMode && !root.dictionaryMode && !root.shortMode",
            qml,
        )
        self.assertIn('objectName: "codexConnectButton"', panel)
        self.assertIn('objectName: "codexChatToggleButton"', panel)
        self.assertIn('objectName: "codexModelCombo"', panel)
        self.assertIn('objectName: "codexChatMessageList"', panel)
        self.assertIn('objectName: "codexChatSendButton"', panel)
        self.assertIn('objectName: "codexChatStopButton"', panel)
        self.assertIn('objectName: "codexLocalReadNotice"', panel)
        self.assertIn("ローカルファイルを読み取る場合があります", panel)
        message_text = panel.split("id: messageText", 1)[1].split("}", 1)[0]
        self.assertIn("textFormat: Text.PlainText", message_text)
        self.assertIn("textFormat: TextEdit.PlainText", panel)
        self.assertIn("backend.startCodexLogin()", panel)
        self.assertIn("backend.reloginCodex()", panel)
        self.assertIn("backend.logoutCodex()", panel)
        self.assertIn("backend.selectCodexModel(currentValue)", panel)
        self.assertIn("backend.sendCodexChatMessage(message)", panel)
        self.assertIn('"streaming": "応答を受信中"', panel)
        self.assertIn('"send_failed": "送信失敗"', panel)
        self.assertIn('enabled: panel.authenticated()', panel)

    def test_application_log_panel_exposes_copy_and_error_actions(self) -> None:
        qml = read_workflow_qml()
        panel = read_component_qml("ApplicationLogPanel.qml")

        self.assertIn('objectName: "applicationLogPanel"', qml)
        self.assertIn('objectName: "applicationLogToggleButton"', panel)
        self.assertIn('objectName: "copyLogsButton"', panel)
        self.assertIn('objectName: "copyErrorLogsButton"', panel)
        self.assertIn('objectName: "copyApplicationInfoButton"', panel)
        self.assertIn('objectName: "openLogsButton"', panel)
        self.assertIn("selectByMouse: true", panel)
        self.assertIn("backend.hasLastProcessDiagnostic", panel)
        self.assertIn("backend.copyErrorLogsToClipboard()", panel)
        self.assertIn("backend.copyApplicationInfoToClipboard()", panel)
        self.assertIn('text: "ログフォルダを開く"', panel)

    def test_highlight_candidate_list_exposes_analysis_preview_add_and_reject(self) -> None:
        short_qml = (UI_ROOT / "screens" / "ShortModeScreen.qml").read_text(encoding="utf-8")
        candidate_qml = read_component_qml("HighlightCandidateList.qml")

        self.assertIn('objectName: "highlightCandidateList"', short_qml)
        self.assertIn('objectName: "highlightAnalyzeButton"', candidate_qml)
        self.assertIn('objectName: "highlightCancelButton"', candidate_qml)
        self.assertIn('objectName: "highlightSortCombo"', candidate_qml)
        self.assertIn('objectName: "highlightPreviewButton"', candidate_qml)
        self.assertIn('objectName: "highlightAddButton"', candidate_qml)
        self.assertIn('objectName: "highlightRejectButton"', candidate_qml)
        self.assertIn('"cancelling"', candidate_qml)
        self.assertIn("appBackend.addHighlightCandidate", candidate_qml)
        self.assertIn("appBackend.rejectHighlightCandidate", candidate_qml)
        self.assertIn("shortPreview.previewAt(seconds)", short_qml)

    def test_user_facing_labels_hide_internal_values(self) -> None:
        workflow_qml = read_workflow_qml()
        wrapper_qml = read_workflow_wrapper_qml()
        codex_qml = read_component_qml("CodexEditPanel.qml")
        highlight_qml = read_component_qml("HighlightCandidateList.qml")
        dictionary_qml = read_component_qml("TranscriptionContextPanel.qml")
        settings_qml = read_component_qml("ShortModeSettingsPanel.qml")
        clips_qml = read_component_qml("ShortModeClipList.qml")

        self.assertIn('"提案を作成"', codex_qml)
        self.assertIn('textRole: "label"', codex_qml)
        self.assertIn('valueRole: "value"', codex_qml)
        self.assertIn("panel.operationLabel(modelData.type)", codex_qml)
        self.assertNotIn("text: backend ? backend.codexState", codex_qml)
        self.assertNotIn("scopeBox.currentText", codex_qml)
        self.assertNotIn("text: modelData.type", codex_qml)

        self.assertIn('"見どころを探す"', highlight_qml)
        self.assertIn('text: "ショートに追加"', highlight_qml)
        self.assertIn('text: "候補から外す"', highlight_qml)
        self.assertIn('valueRole: "value"', highlight_qml)
        self.assertNotIn("text: appBackend ? appBackend.highlightAnalysisState", highlight_qml)
        self.assertNotIn('"score " +', highlight_qml)
        self.assertNotIn('model: ["all", "conversation", "emphasis"]', highlight_qml)

        self.assertIn('text: "この辞書を文字起こしに使用"', dictionary_qml)
        self.assertIn('text: "すべて選択"', dictionary_qml)
        self.assertIn('text: "選択解除"', dictionary_qml)
        self.assertNotIn("ASRへ渡す", dictionary_qml)
        self.assertNotIn("transcript cache", dictionary_qml)
        self.assertNotIn('model.source + " · score "', dictionary_qml)

        self.assertIn("userFacingStatusLabel", workflow_qml)
        self.assertIn('text: "プレビューを更新"', workflow_qml)
        self.assertIn('text: "素材を再指定"', workflow_qml)
        self.assertIn('"音声のずれを自動調整"', workflow_qml)
        self.assertIn('text: "字幕の音量バランス"', workflow_qml)
        self.assertNotIn('root.appBackend.stage + " · " + root.appBackend.status', workflow_qml)
        self.assertNotIn('text: "実行ファイル: "', workflow_qml)
        self.assertNotIn('text: "配置場所: "', workflow_qml)
        self.assertNotIn('text: "トラブルシューティング情報をコピー"', workflow_qml)
        self.assertIn('return "GPU処理を利用できません。処理方法をCPUに変更するか、アプリの実行環境を修復してください"', workflow_qml)
        self.assertNotIn("CUDA版PyTorch", workflow_qml)
        self.assertNotIn("setup.bat", workflow_qml)
        self.assertNotIn('text: "Cache: "', workflow_qml)
        self.assertNotIn('text: "SEQUENCE"', workflow_qml)
        self.assertNotIn('text: "INPUT CHANNELS"', workflow_qml)
        self.assertNotIn('text: "INPUT ON"', workflow_qml)
        self.assertIn('text: "すべての音声トラックをリセット"', workflow_qml)
        self.assertNotIn('text: "全チャンネルをリセット"', workflow_qml)
        self.assertNotIn('text: "ASSを更新"', workflow_qml)
        self.assertNotIn('stage + " · " + screenRoot.appBackend.status', wrapper_qml)

        self.assertIn('text: "ショート全体の設定"', settings_qml)
        self.assertIn('objectName: "shortModeTransitionDurationSlider"', settings_qml)
        self.assertIn("setShortVideoTransition(transitionCombo.currentValue, value)", settings_qml)
        self.assertNotIn("setShortVideoTransition(transitionCombo.currentText, value)", settings_qml)
        self.assertIn('"label": "画面いっぱい"', settings_qml)
        self.assertIn('"label": "全体を表示"', settings_qml)
        self.assertIn('"label": "ぼかし背景"', settings_qml)
        self.assertIn('text: "動画内の開始"', settings_qml)
        self.assertNotIn('model: ["cover", "contain", "blur"]', settings_qml)
        self.assertNotIn('model: ["crossfade", "fade", "cut"]', settings_qml)

        self.assertIn('text: "ショートに追加"', clips_qml)
        self.assertIn('textRole: "label"', clips_qml)
        self.assertIn('valueRole: "value"', clips_qml)
        self.assertNotIn('model: ["cover", "contain", "blur"]', clips_qml)

    def test_editor_playback_follows_caption_list_and_timeline(self) -> None:
        qml = read_workflow_qml()

        self.assertIn("root.appBackend.selectSegmentAtTime(editorPlayer.position / 1000)", qml)
        self.assertIn("timelineRoot.followPlaybackPosition(timelineRoot.player.position)", qml)
        self.assertIn('objectName: "captionTable"', qml)
        self.assertIn("function onSelectionChanged()", qml)
        self.assertIn("positionViewAtIndex(selectedIndex, ListView.Contain)", qml)

    def test_source_drag_and_drop_is_wired_to_backend(self) -> None:
        qml = read_workflow_qml()

        self.assertIn('objectName: "globalSourceDropArea"', qml)
        self.assertIn('objectName: "sourcePopupDropTarget"', qml)
        self.assertIn("root.appBackend.importDroppedSourceFiles(drop.urls)", qml)
        self.assertIn("drop.acceptProposedAction()", qml)

    def test_transcription_dictionary_opens_as_dedicated_screen(self) -> None:
        qml = read_workflow_qml()
        wrapper = read_workflow_wrapper_qml()

        self.assertIn('objectName: "transcriptionDictionaryOpenButton"', qml)
        self.assertIn("onClicked: root.openDictionaryScreen()", qml)
        self.assertIn("property bool dictionaryMode: false", qml)
        self.assertIn("!root.editorMode && !root.mixerMode && !root.dictionaryMode", qml)
        self.assertIn('objectName: "transcriptionDictionaryPage"', wrapper)
        self.assertIn("visible: screenRoot.dictionaryMode", wrapper)

    def test_transcription_overwrite_confirmation_is_wired(self) -> None:
        qml = read_workflow_qml()

        self.assertIn('objectName: "overwriteProjectDialog"', qml)
        self.assertIn("transcriptionProjectExists()", qml)
        self.assertIn("startTranscription(root.currentSettings(), false)", qml)
        self.assertIn("startTranscription(root.currentSettings(), true)", qml)
        self.assertIn("standardButtons: Dialog.Yes | Dialog.No", qml)

    def test_audio_mixer_is_wired_to_project_channels(self) -> None:
        qml = read_workflow_qml()
        mixer_block = qml.split("id: mixerContentComponent", 1)[1].split("id: editorPage", 1)[0]

        self.assertIn('objectName: "audioMixerOpenButton"', qml)
        self.assertIn('objectName: "mixerPage"', qml)
        self.assertIn('objectName: "mixerChannelList"', qml)
        self.assertIn('objectName: "mixerChannelFader"', qml)
        self.assertIn('objectName: "mixerPlayButton"', qml)
        self.assertIn('objectName: "mixerSeek"', qml)
        self.assertIn('objectName: "mixerSequence"', qml)
        self.assertIn('objectName: "mixerPreviewPlayers"', qml)
        self.assertIn("model: root.appBackend.audioMixerPreviewChannels", qml)
        self.assertIn("root.appBackend.startAudioMixerPreview(mixerPlayer.position)", mixer_block)
        self.assertIn("root.appBackend.seekAudioMixerPreview(", mixer_block)
        self.assertIn("root.appBackend.pauseAudioMixerPreview()", mixer_block)
        self.assertIn("muted: true", mixer_block)
        self.assertIn('objectName: "mixerPreviewPlayer-" + previewChannelId', mixer_block)
        self.assertIn("root.appBackend.prepareAudioMixerPreview()", qml)
        self.assertIn('text: "プレビューを作り直す"', mixer_block)
        self.assertIn("root.appBackend.clearAudioPreviewCache()", mixer_block)
        self.assertIn("root.appBackend.prepareAudioMixerPreview()", mixer_block)
        self.assertIn("root.appBackend.audioPreviewPreparing", mixer_block)
        self.assertIn("mixerContent.previewReady ? root.appBackend.audioPreviewClockUrl", mixer_block)
        self.assertIn("audioOutput: AudioOutput { muted: true }", qml)
        self.assertIn("value: mixerPlayer.position", qml)
        self.assertIn("to: Math.max(1, mixerPlayer.duration)", qml)
        self.assertIn("mixerContent.syncPreviewPlayers(true)", qml)
        self.assertIn("audioBufferOutput: modelData.preview_buffer_output", qml)
        self.assertIn("root.appBackend.audioPreviewLevels[modelData.id]", qml)
        self.assertIn("root.editorPositionCache = mixerPlayer.position", qml)
        self.assertIn("function restoreInitialPosition(confirmStable)", mixer_block)
        self.assertIn("property int requestedAudioTrack", mixer_block)
        self.assertIn("if (!hasPendingSync || audioTracks.length <= requestedAudioTrack)", mixer_block)
        self.assertIn("activeAudioTrack = requestedAudioTrack", mixer_block)
        self.assertIn("onTracksChanged: applyPendingSync()", mixer_block)
        self.assertIn("onSeekableChanged: applyPendingSync()", mixer_block)
        self.assertNotIn("activeAudioTrack: Number(", mixer_block)
        self.assertIn("orientation: Qt.Vertical", qml)
        self.assertIn("model: root.appBackend.audioMixerChannels", qml)
        self.assertIn("lanes: root.appBackend.audioMixerSequenceChannels", mixer_block)
        self.assertIn("showTrackVolume: true", mixer_block)
        self.assertIn("timelineRoot.rulerHeight - timelineFlick.contentY", qml)
        self.assertIn('objectName: "timelineLaneLabel-" + index', qml)
        self.assertIn("Layout.preferredHeight: Math.min(230, Math.max(174", mixer_block)
        self.assertIn("laneHeight: 42", mixer_block)
        self.assertIn('objectName: "mixerChannelStrip-" + index', mixer_block)
        self.assertIn("width: 170", mixer_block)
        self.assertIn('objectName: "mixerSequenceVolumeBar"', qml)
        self.assertIn('objectName: "mixerMasterMeter"', mixer_block)
        self.assertIn('objectName: "mixerLimiterReduction"', mixer_block)
        self.assertIn("function updateMixerChannel(index, changes)", mixer_block)
        self.assertIn("mixerChannelScrollRestoreTimer.restart()", mixer_block)
        self.assertIn("updateAudioMixChannel", qml)
        self.assertIn("resetAudioMixer", qml)
        self.assertNotIn('objectName: "audioMixerPopup"', qml)
        self.assertNotIn("Qt.callLater", mixer_block)

    def test_video_encoder_is_selected_automatically(self) -> None:
        qml = read_workflow_qml()

        self.assertNotIn("codecCombo", qml)
        self.assertNotIn('"video_codec":', qml)
        self.assertIn('objectName: "automaticVideoCodecText"', qml)
        self.assertIn("root.appBackend.dependencyStatus.nvenc", qml)

    def test_detail_settings_preserve_zero_values_in_sync(self) -> None:
        qml = read_workflow_qml()

        self.assertIn('modelCombo.currentIndex = Math.max(0, modelCombo.find(coalesceSetting(value.model, "large-v3")))', qml)
        self.assertIn('deviceCombo.currentIndex = Math.max(0, deviceCombo.find(coalesceSetting(value.device, "cuda")))', qml)
        self.assertNotIn("value.model || \"large-v3\"", qml)
        self.assertNotIn("value.device || \"cuda\"", qml)

    def test_main_font_size_control_supports_nine_hundred_percent(self) -> None:
        qml = read_workflow_qml()
        overlay_qml = read_component_qml("SubtitleOverlay.qml")

        self.assertIn('objectName: "fontSizeSpin"; from: 10; to: 900; value: 100', qml)
        self.assertIn("root.defaultSubtitleFontSize * fontSizeSpin.value / 100", qml)
        self.assertIn('readonly property int selectedSubtitleFontSize', qml)
        self.assertIn('baseFontSize: root.selectedSubtitleFontSize', qml)
        self.assertIn('property int baseFontSize: 50', overlay_qml)
        self.assertIn('font.pixelSize: overlayRoot.previewPixelSize(segmentData.subtitle_font_scale)', overlay_qml)

    def test_caption_overlay_uses_ass_margin_formula(self) -> None:
        qml = read_workflow_qml()
        overlay_qml = read_component_qml("SubtitleOverlay.qml")

        self.assertIn("function maxSubtitleFontScale()", overlay_qml)
        self.assertIn("function maxSubtitlePixelSize()", overlay_qml)
        self.assertIn("function maxLayoutRow()", overlay_qml)
        self.assertIn("function previewRowMarginStep()", overlay_qml)
        self.assertIn("rowMarginBase: 34", overlay_qml)
        self.assertIn("rowMarginStepBase: 156", overlay_qml)
        self.assertIn(
            "anchors.bottomMargin: overlayRoot.rowMarginBase + Number(segmentData.layout_row || 0) * overlayRoot.previewRowMarginStep()",
            overlay_qml,
        )
        self.assertIn("defaultSubtitleFontSize", overlay_qml)

    def test_short_preview_uses_shared_subtitle_overlay(self) -> None:
        overlay_qml = read_component_qml("SubtitleOverlay.qml")
        preview_qml = read_component_qml("ShortModePreview.qml")
        workflow_qml = read_workflow_qml()
        self.assertIn("property var subtitleTextResolver", overlay_qml)
        self.assertIn("activeSubtitleSegments", overlay_qml)
        self.assertIn("shortSubtitleOverlayCaption", preview_qml)
        self.assertIn("subtitle_scale_percent", preview_qml)
        self.assertIn("normalizedSubtitleScalePercent", preview_qml)
        self.assertNotIn("subtitle_scale_percent || 150", preview_qml)
        self.assertIn("SubtitleOverlay", workflow_qml)

    def test_global_subtitle_outline_controls_are_wired_to_preview(self) -> None:
        qml = read_workflow_qml()

        self.assertIn('objectName: "outlineColorDialog"', qml)
        self.assertIn('objectName: "outlineColorButton"', qml)
        self.assertIn('objectName: "outlineThicknessSpin"; from: 0; to: 20; value: 3', qml)
        self.assertIn('"subtitle_outline_color": root.selectedSubtitleOutlineColor', qml)
        self.assertIn('"subtitle_outline_thickness": root.selectedSubtitleOutlineThickness', qml)
        self.assertIn('outlineThickness: root.selectedSubtitleOutlineThickness', qml)
        self.assertIn('model: overlayRoot.outlineOffsets(overlayRoot.outlineThickness)', read_component_qml("SubtitleOverlay.qml"))
        self.assertIn('outlineColor: root.selectedSubtitleOutlineColor', qml)

    def test_speaker_color_picker_is_wired_per_speaker(self) -> None:
        qml = read_workflow_qml()

        self.assertIn('objectName: "speakerColorDialog"', qml)
        self.assertIn('objectName: "sourceSpeakerColorButton"', qml)
        self.assertIn('objectName: "projectSpeakerColorList"', qml)
        self.assertIn("updateSpeakerColor(root.colorTargetIndex", qml)
        self.assertIn("updateProjectSpeakerColor(root.colorTargetIndex", qml)


    def test_short_clip_list_exposes_trim_fields(self) -> None:
        qml = read_component_qml("ShortModeClipList.qml")
        self.assertIn('objectName: "shortModeStartTimeField" + index', qml)
        self.assertIn('objectName: "shortModeEndTimeField" + index', qml)
        self.assertIn("updateShortVideoClip", qml)
        self.assertIn("TimeField", qml)


if __name__ == "__main__":
    unittest.main()
