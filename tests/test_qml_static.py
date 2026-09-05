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
    COMPONENTS_ROOT / "ProcessingProgressPanel.qml",
    COMPONENTS_ROOT / "CodexChatPanel.qml",
    COMPONENTS_ROOT / "CodexSidebarContainer.qml",
    COMPONENTS_ROOT / "EditorModeRail.qml",
    COMPONENTS_ROOT / "AudioPreviewBridge.qml",
    COMPONENTS_ROOT / "AudioModeSettings.qml",
    COMPONENTS_ROOT / "SubtitleModeSettings.qml",
    COMPONENTS_ROOT / "SubtitleOverlay.qml",
    COMPONENTS_ROOT / "ShortModePreview.qml",
)
QML_LINT_FILES = (
    ENTRYPOINT_QML,
    WORKFLOW_QML,
    WORKFLOW_WRAPPER_QML,
    *SHARED_CONTROL_QML_FILES,
)


class QmlStaticTests(unittest.TestCase):
    def test_qml_files_pass_qmllint_without_warnings(self) -> None:
        executable_name = "pyside6-qmllint.exe" if os.name == "nt" else "pyside6-qmllint"
        bundled = Path(sys.executable).with_name(executable_name)
        executable = str(bundled) if bundled.is_file() else shutil.which(executable_name)
        self.assertTrue(executable, "pyside6-qmllint is required with PySide6")

        for qml_path in QML_LINT_FILES:
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

    def test_codex_chat_treats_external_messages_as_plain_text(self) -> None:
        panel = (COMPONENTS_ROOT / "CodexChatPanel.qml").read_text(encoding="utf-8")
        message_text = panel.split("id: messageText", 1)[1].split("}", 1)[0]

        self.assertIn("textFormat: Text.PlainText", message_text)
        self.assertIn("textFormat: TextEdit.PlainText", panel)
        self.assertIn(
            "書き込みは禁止されていますが、Codexはローカルファイルを読み取る場合があります。",
            panel,
        )

    def test_user_facing_copy_avoids_internal_terms(self) -> None:
        qml_by_area = {
            "codex edit": (COMPONENTS_ROOT / "CodexEditPanel.qml").read_text(encoding="utf-8"),
            "highlight": (COMPONENTS_ROOT / "HighlightCandidateList.qml").read_text(encoding="utf-8"),
            "dictionary": (COMPONENTS_ROOT / "TranscriptionContextPanel.qml").read_text(encoding="utf-8"),
            "workflow": WORKFLOW_QML.read_text(encoding="utf-8"),
            "short settings": (COMPONENTS_ROOT / "ShortModeSettingsPanel.qml").read_text(encoding="utf-8"),
            "short clips": (COMPONENTS_ROOT / "ShortModeClipList.qml").read_text(encoding="utf-8"),
        }
        required_copy = {
            "codex edit": ("提案を作成",),
            "highlight": ("見どころを探す", "ショートに追加", "候補から外す"),
            "dictionary": ("この辞書を文字起こしに使用", "すべて選択", "選択解除"),
            "workflow": (
                "プレビューを更新",
                "素材を再指定",
                "音声のずれを自動調整",
                "字幕の音量バランス",
                "すべての音声トラックをリセット",
            ),
            "short settings": ("ショート全体の設定", "画面いっぱい", "全体を表示", "ぼかし背景", "動画内の開始"),
            "short clips": ("ショートに追加",),
        }
        forbidden_copy = {
            "dictionary": ("ASRへ渡す", "transcript cache", " · score "),
            "workflow": (
                "CUDA版PyTorch",
                "setup.bat",
                'text: "Cache: "',
                'text: "SEQUENCE"',
                'text: "INPUT CHANNELS"',
                'text: "INPUT ON"',
                'text: "全チャンネルをリセット"',
                'text: "ASSを更新"',
                'text: "実行ファイル: "',
                'text: "配置場所: "',
                'text: "トラブルシューティング情報をコピー"',
            ),
        }

        for area, expected_values in required_copy.items():
            for expected in expected_values:
                with self.subTest(area=area, required=expected):
                    self.assertIn(expected, qml_by_area[area])
        for area, forbidden_values in forbidden_copy.items():
            for forbidden in forbidden_values:
                with self.subTest(area=area, forbidden=forbidden):
                    self.assertNotIn(forbidden, qml_by_area[area])

    def test_editor_workspace_has_one_overlay_and_one_shared_preview(self) -> None:
        workflow = WORKFLOW_QML.read_text(encoding="utf-8")
        main_workspace = workflow.split('objectName: "mainWorkspace"', 1)[1].split(
            'objectName: "overwriteProjectDialog"', 1
        )[0]
        editor_content = workflow.split("id: editorContentComponent", 1)[1].split(
            "id: shortModePage", 1
        )[0]

        self.assertIn('property string activeOverlay: ""', workflow)
        self.assertNotIn("\n    property bool editorMode:", workflow)
        self.assertNotIn("\n    property bool mixerMode:", workflow)
        self.assertIn('objectName: "editorModeRail"', main_workspace)
        self.assertIn('objectName: "modeEditorSlot"', main_workspace)
        self.assertIn('objectName: "modeSettingsSlot"', main_workspace)
        self.assertIn('property Component cutModeEditorContent: null', workflow)
        self.assertIn('property Component cutModeSettingsContent: null', workflow)
        self.assertIn('sourceComponent: root.modeEditorContent', main_workspace)
        self.assertIn('sourceComponent: root.modeSettingsContent', main_workspace)
        self.assertEqual(main_workspace.count("MediaPlayer {"), 1)
        self.assertIn('objectName: "mainWorkspacePlayer"', main_workspace)
        self.assertIn('objectName: "mainWorkspaceAudioOutput"', main_workspace)
        self.assertNotIn("MediaPlayer {", editor_content)
        self.assertNotIn("editorPlayer", workflow)
        self.assertIn("mainPlayer.videoOutput = editorVideo", editor_content)
        self.assertIn("mainPlayer.videoOutput = mainVideo", editor_content)
        self.assertIn('String(root.appBackend.editorPlayhead.basis || "source")', workflow)
        self.assertIn("interval: 100", main_workspace)
        self.assertIn(
            "onTriggered: root.syncEditorPlayhead(mainPlayer.position, false)",
            main_workspace,
        )
        self.assertIn("onActiveSegmentsChanged:", editor_content)
        self.assertIn("syncEditorSelectionFromActiveSegments", editor_content)

    def test_subtitle_and_audio_modes_share_the_workspace_player(self) -> None:
        workflow = WORKFLOW_QML.read_text(encoding="utf-8")
        audio_bridge = (COMPONENTS_ROOT / "AudioPreviewBridge.qml").read_text(
            encoding="utf-8"
        )

        self.assertIn('objectName: "workspaceSubtitleEditor"', workflow)
        self.assertIn('objectName: "workspaceAudioEditor"', workflow)
        self.assertEqual(workflow.count("AudioPreviewBridge {"), 1)
        self.assertNotIn("videoOutput", audio_bridge)
        self.assertNotIn("property real position", audio_bridge)

    def test_subtitle_preview_does_not_copy_the_full_segment_list(self) -> None:
        workflow = WORKFLOW_QML.read_text(encoding="utf-8")
        overlay = (COMPONENTS_ROOT / "SubtitleOverlay.qml").read_text(encoding="utf-8")
        short_clip_list = (COMPONENTS_ROOT / "ShortModeClipList.qml").read_text(encoding="utf-8")
        short_screen = (UI_ROOT / "screens" / "ShortModeScreen.qml").read_text(encoding="utf-8")

        self.assertNotIn("subtitleSegments", workflow)
        self.assertNotIn("subtitleSegments", overlay)
        self.assertNotIn("subtitleSegments", short_clip_list)
        self.assertNotIn("shortVideoClips", short_clip_list)
        self.assertNotIn("shortVideoClips", short_screen)
        self.assertIn("property var layoutMetrics", overlay)
        self.assertIn("appBackend.activeSubtitleSegments", overlay)
        self.assertIn("appBackend.segmentCount", workflow)
        self.assertIn("appBackend.subtitleModel", short_clip_list)
        self.assertIn("appBackend.shortVideoClipModel", short_clip_list)
        self.assertIn("appBackend.shortVideoClipCount", short_screen)
        self.assertIn("appBackend.shortVideoClipAt", short_screen)
        self.assertIn("function clampCurrentClipIndex()", short_screen)
        self.assertNotIn("function clampSelected()", short_clip_list)
        self.assertNotIn("shortVideoClipCount", short_clip_list)

    def test_short_mode_preview_keeps_playback_for_visual_only_updates(self) -> None:
        preview = (COMPONENTS_ROOT / "ShortModePreview.qml").read_text(encoding="utf-8")

        self.assertIn("function clipPlaybackKey(clip)", preview)
        self.assertIn("if (!force && nextKey === previewRoot.activeClipKey) return", preview)
        self.assertIn("onClipDataChanged: previewRoot.syncClipPlayback(false)", preview)
        self.assertNotIn("previewPlayer.stop()\n            previewPlayer.position", preview)


if __name__ == "__main__":
    unittest.main()
