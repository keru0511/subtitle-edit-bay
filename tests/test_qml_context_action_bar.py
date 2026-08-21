from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = ROOT / "src" / "ui"


def read_ui_file(relative_path: str) -> str:
    return (UI_ROOT / relative_path).read_text(encoding="utf-8")


class ContextActionBarQmlTests(unittest.TestCase):
    def test_workflow_uses_context_bar_and_codex_sidebar_slot(self) -> None:
        workflow = read_ui_file("screens/MainWorkflowScreen.qml")
        action_bar = read_ui_file("components/ContextActionBar.qml")
        sidebar = read_ui_file("components/CodexSidebarContainer.qml")

        self.assertIn("ContextActionBar", workflow)
        self.assertIn('objectName: "contextActionBar"', action_bar)
        self.assertIn("CodexSidebarContainer", workflow)
        self.assertIn('objectName: "codexChatSidebarContainer"', sidebar)
        self.assertIn('objectName: "codexChatPanel"', sidebar)
        self.assertNotIn('text: "次の操作"', workflow)

    def test_action_bar_preserves_all_workflow_actions_and_states(self) -> None:
        workflow = read_ui_file("screens/MainWorkflowScreen.qml")
        action_bar = read_ui_file("components/ContextActionBar.qml")

        for object_name in (
            "transcriptionDictionaryOpenButton",
            "transcribeButton",
            "editSubtitlesButton",
            "audioMixerOpenButton",
            "shortModeOpenButton",
            "renderVideoButton",
            "workflowBlockReason",
            "saveSettingsButton",
            "outputFolderButton",
            "settingsToggleButton",
        ):
            self.assertIn(f'objectName: "{object_name}"', action_bar)
        self.assertIn("visible: !actionBar.projectLoaded", action_bar)
        self.assertIn("visible: actionBar.projectLoaded", action_bar)
        self.assertIn('actionBar.activeJob === "transcribe"', action_bar)
        self.assertIn('actionBar.activeJob === "render"', action_bar)
        self.assertIn("transcriptionBlockReason()", workflow)
        self.assertIn("onSaveOrStopRequested", workflow)

    def test_detail_settings_are_popup_based_and_keep_standard_sizes(self) -> None:
        workflow = read_ui_file("screens/MainWorkflowScreen.qml")

        self.assertIn('objectName: "advancedSettingsPopup"', workflow)
        self.assertIn('objectName: "advancedSettingsPanel"', workflow)
        self.assertIn('objectName: "settingsToggleButton"', read_ui_file("components/ContextActionBar.qml"))
        self.assertIn("width: 1520", workflow)
        self.assertIn("height: 940", workflow)
        self.assertIn("minimumWidth: 1220", workflow)
        self.assertIn("minimumHeight: 760", workflow)


if __name__ == "__main__":
    unittest.main()
