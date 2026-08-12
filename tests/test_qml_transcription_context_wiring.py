from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_QML = ROOT / "src" / "ui" / "Main.qml"
WRAPPER_QML = ROOT / "src" / "ui" / "screens" / "MainWorkflowScreenWithContext.qml"


class QmlTranscriptionContextWiringTests(unittest.TestCase):
    def test_main_uses_context_wrapper_screen(self) -> None:
        content = MAIN_QML.read_text(encoding="utf-8")

        self.assertIn("MainWorkflowScreenWithContext", content)
        self.assertNotIn("MainWorkflowScreen {}", content)

    def test_wrapper_binds_panel_to_backend_context(self) -> None:
        content = WRAPPER_QML.read_text(encoding="utf-8")

        self.assertIn("TranscriptionContextPanel", content)
        self.assertIn('objectName: "mainTranscriptionContextPanel"', content)
        self.assertIn("context: screenRoot.appBackend.transcriptionContext", content)
        self.assertIn("running: screenRoot.appBackend.running", content)
        self.assertIn("screenRoot.appBackend.setTranscriptionContext(context)", content)

    def test_wrapper_hosts_context_panel_on_dedicated_page(self) -> None:
        content = WRAPPER_QML.read_text(encoding="utf-8")

        self.assertIn('objectName: "transcriptionDictionaryPage"', content)
        self.assertIn("visible: screenRoot.dictionaryMode", content)
        self.assertIn('objectName: "transcriptionDictionarySaveButton"', content)
        self.assertIn('objectName: "transcriptionDictionaryBackButton"', content)
        self.assertIn("screenRoot.appBackend.saveSettings(screenRoot.currentSettings())", content)
        self.assertNotIn("anchors.topMargin: 76", content)


if __name__ == "__main__":
    unittest.main()
