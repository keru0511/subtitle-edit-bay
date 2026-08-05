from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


class QmlStaticTests(unittest.TestCase):
    def test_main_qml_passes_qmllint_without_warnings(self) -> None:
        executable_name = "pyside6-qmllint.exe" if os.name == "nt" else "pyside6-qmllint"
        bundled = Path(sys.executable).with_name(executable_name)
        executable = str(bundled) if bundled.is_file() else shutil.which(executable_name)
        self.assertTrue(executable, "pyside6-qmllint is required with PySide6")

        qml_path = Path(__file__).resolve().parents[1] / "src" / "ui" / "Main.qml"
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

    def test_caption_font_selector_is_wired_to_backend(self) -> None:
        qml_path = Path(__file__).resolve().parents[1] / "src" / "ui" / "Main.qml"
        qml = qml_path.read_text(encoding="utf-8")

        self.assertIn('objectName: "captionFontCombo"', qml)
        self.assertIn("model: root.appBackend.fontChoices", qml)
        self.assertIn('"subtitle_font_family": currentValue', qml)
        self.assertIn('font.family: segmentData.subtitle_font_family || "Yu Gothic UI"', qml)

    def test_timeline_delegate_and_position_handlers_are_safe_during_refresh(self) -> None:
        qml_path = Path(__file__).resolve().parents[1] / "src" / "ui" / "Main.qml"
        qml = qml_path.read_text(encoding="utf-8")

        self.assertIn("property var segment: modelData && modelData.segment ? modelData.segment : ({})", qml)
        self.assertIn("visible: sourceIndex >= 0 && segment.start !== undefined", qml)
        self.assertIn("mainSeek.value = mainPlayer.position", qml)
        self.assertIn("editorSeek.value = editorPlayer.position", qml)

    def test_caption_size_control_and_source_labels_are_readable_and_consistent(self) -> None:
        qml_path = Path(__file__).resolve().parents[1] / "src" / "ui" / "Main.qml"
        qml = qml_path.read_text(encoding="utf-8")

        self.assertIn("component CompactSpinBox: SpinBox", qml)
        self.assertIn('objectName: "captionSizeSpin"', qml)
        self.assertIn('objectName: "sourcePanelSetupButton"', qml)
        self.assertNotIn('text: "素材を変更"', qml)

    def test_speaker_color_picker_is_wired_per_speaker(self) -> None:
        qml_path = Path(__file__).resolve().parents[1] / "src" / "ui" / "Main.qml"
        qml = qml_path.read_text(encoding="utf-8")

        self.assertIn('objectName: "speakerColorDialog"', qml)
        self.assertIn('objectName: "sourceSpeakerColorButton"', qml)
        self.assertIn('objectName: "projectSpeakerColorList"', qml)
        self.assertIn("updateSpeakerColor(root.colorTargetIndex", qml)
        self.assertIn("updateProjectSpeakerColor(root.colorTargetIndex", qml)


if __name__ == "__main__":
    unittest.main()
