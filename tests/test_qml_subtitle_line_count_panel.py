from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL_QML = ROOT / "src" / "ui" / "components" / "SubtitleLineCountPanel.qml"
WRAPPER_QML = ROOT / "src" / "ui" / "screens" / "MainWorkflowScreenWithContext.qml"
GUI_BASE = ROOT / "src" / "gui_base.py"


class SubtitleLineCountPanelRemovalTests(unittest.TestCase):
    def test_line_count_panel_component_is_removed(self) -> None:
        self.assertFalse(PANEL_QML.exists())

    def test_wrapper_no_longer_wires_line_count_panel(self) -> None:
        content = WRAPPER_QML.read_text(encoding="utf-8")

        self.assertNotIn("SubtitleLineCountPanel", content)
        self.assertNotIn('objectName: "editorSubtitleLineCountPanel"', content)
        self.assertNotIn("selectedSubtitleSegment", content)

    def test_backend_no_longer_exposes_line_count_slot(self) -> None:
        content = GUI_BASE.read_text(encoding="utf-8")

        self.assertNotIn("def updateSegmentLineCount", content)


if __name__ == "__main__":
    unittest.main()
