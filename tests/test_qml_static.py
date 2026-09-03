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

        self.assertIn('property string activeOverlay: ""', workflow)
        self.assertNotIn("\n    property bool editorMode:", workflow)
        self.assertNotIn("\n    property bool mixerMode:", workflow)
        self.assertIn('objectName: "editorModeRail"', main_workspace)
        self.assertIn('objectName: "modeEditorSlot"', main_workspace)
        self.assertIn('objectName: "modeSettingsSlot"', main_workspace)
        self.assertEqual(main_workspace.count("MediaPlayer {"), 1)


if __name__ == "__main__":
    unittest.main()
