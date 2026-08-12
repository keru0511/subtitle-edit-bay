from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL_QML = ROOT / "src" / "ui" / "components" / "SubtitleLineCountPanel.qml"
WRAPPER_QML = ROOT / "src" / "ui" / "screens" / "MainWorkflowScreenWithContext.qml"


class SubtitleLineCountPanelQmlTests(unittest.TestCase):
    def test_panel_exposes_line_count_choices(self) -> None:
        content = PANEL_QML.read_text(encoding="utf-8")

        self.assertIn('objectName: "subtitleLineCountPanel"', content)
        self.assertIn('objectName: "subtitleLineCountCombo"', content)
        self.assertIn('"label": "自動（既存ルール）", "value": "auto"', content)
        self.assertIn('"label": "1行", "value": "1"', content)
        self.assertIn('"label": "2行", "value": "2"', content)

    def test_panel_emits_selected_segment_line_count(self) -> None:
        content = PANEL_QML.read_text(encoding="utf-8")

        self.assertIn("signal lineCountChanged(int segmentIndex, string lineCount)", content)
        self.assertIn("panelRoot.lineCountChanged(panelRoot.selectedSegmentIndex, currentValue)", content)
        self.assertIn("segment.subtitle_line_count", content)

    def test_wrapper_wires_panel_to_backend_update_segment(self) -> None:
        content = WRAPPER_QML.read_text(encoding="utf-8")

        self.assertIn("SubtitleLineCountPanel", content)
        self.assertIn('objectName: "editorSubtitleLineCountPanel"', content)
        self.assertIn("visible: screenRoot.editorMode && screenRoot.appBackend.projectLoaded", content)
        self.assertIn("selectedSegmentIndex: screenRoot.appBackend.selectedSegmentIndex", content)
        self.assertIn("segment: screenRoot.selectedSubtitleSegment()", content)
        self.assertIn('screenRoot.appBackend.updateSegment(segmentIndex, {"subtitle_line_count": lineCount})', content)


if __name__ == "__main__":
    unittest.main()
