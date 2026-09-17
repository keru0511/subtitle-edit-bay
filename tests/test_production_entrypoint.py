from __future__ import annotations

import re
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT_QML = REPOSITORY_ROOT / "src" / "ui" / "Main.qml"

PRODUCTION_PYTHON_LAUNCHERS = (
    REPOSITORY_ROOT / "src" / "gui.py",
    REPOSITORY_ROOT / "src" / "gui_base.py",
)
GUI_TEST_ENTRYPOINTS = (
    REPOSITORY_ROOT / "tests" / "test_gui_editor.py",
    REPOSITORY_ROOT / "tests" / "gui_performance_scenarios.py",
    REPOSITORY_ROOT / "tests" / "project_reload_probe.py",
)

NO_ALTERNATE_SCREEN_PATTERN = re.compile(
    r"(?i)(?:"
    r"(?:legacy|fallback)[_-]?(?:screen|qml|ui|entrypoint)|"
    r"(?:screen|qml|ui|entrypoint)[_-]?(?:legacy|fallback)|"
    r"(?:use|enable|disable|feature)[_-]?(?:legacy|new)[_-]?(?:screen|qml|ui)"
    r")"
)


class ProductionEntrypointContractTests(unittest.TestCase):
    def test_main_qml_directly_constructs_the_cutover_root(self) -> None:
        source = ENTRYPOINT_QML.read_text(encoding="utf-8")

        self.assertEqual(
            [line.strip() for line in source.splitlines() if line.strip()],
            [
                "pragma ComponentBehavior: Bound",
                "import QtQuick",
                'import "screens"',
                "MainWorkflowScreenWithContext {}",
            ],
        )
        self.assertNotIn("MainWorkflowScreen {", source)

    def test_python_production_launchers_load_the_canonical_qml_path(self) -> None:
        expected_path = 'Path(__file__).resolve().parent / "ui" / "Main.qml"'

        for launcher in PRODUCTION_PYTHON_LAUNCHERS:
            with self.subTest(launcher=launcher):
                source = launcher.read_text(encoding="utf-8")
                self.assertEqual(source.count("qml_path ="), 1)
                self.assertIn(expected_path, source)
                self.assertNotIn("MainWorkflowScreen.qml", source)
                self.assertNotIn("MainWorkflowScreen {", source)

    def test_launcher_packaging_and_dev_launch_use_the_same_production_module(self) -> None:
        installer_launcher = (REPOSITORY_ROOT / "installer" / "launch.ps1").read_text(encoding="utf-8-sig")
        start_batch = (REPOSITORY_ROOT / "start.bat").read_text(encoding="utf-8")
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        installer = (REPOSITORY_ROOT / "installer" / "SubtitleEditBay.iss").read_text(encoding="utf-8-sig")

        self.assertIn('$psi.Arguments = "-m src.gui"', installer_launcher)
        self.assertNotIn("src.gui_base", installer_launcher)
        self.assertIn('set "LAUNCH_SCRIPT=%~dp0scripts\\launch.ps1"', start_batch)
        self.assertIn('set "LAUNCH_SCRIPT=%~dp0installer\\launch.ps1"', start_batch)
        self.assertIn(r".\.venv\Scripts\python.exe -m src.gui", readme)
        self.assertNotIn("python -m src.gui_base", readme)
        self.assertIn(r'Source: "{#SourceRoot}\installer\launch.ps1"; DestDir: "{app}\scripts"', installer)

    def test_gui_regression_entrypoints_all_load_main_qml(self) -> None:
        for test_entrypoint in GUI_TEST_ENTRYPOINTS:
            with self.subTest(test_entrypoint=test_entrypoint):
                source = test_entrypoint.read_text(encoding="utf-8")
                self.assertIn('"src" / "ui" / "Main.qml"', source)
                self.assertNotIn("MainWorkflowScreen.qml", source)

    def test_production_launch_chain_has_no_legacy_screen_selector_or_fallback(self) -> None:
        launch_chain = (
            *PRODUCTION_PYTHON_LAUNCHERS,
            REPOSITORY_ROOT / "installer" / "launch.ps1",
            REPOSITORY_ROOT / "start.bat",
            REPOSITORY_ROOT / "tests" / "test_gui_editor.py",
            REPOSITORY_ROOT / "tests" / "gui_performance_scenarios.py",
            REPOSITORY_ROOT / "tests" / "project_reload_probe.py",
        )

        for path in launch_chain:
            with self.subTest(path=path):
                source = path.read_text(encoding="utf-8", errors="strict")
                self.assertIsNone(NO_ALTERNATE_SCREEN_PATTERN.search(source))


if __name__ == "__main__":
    unittest.main()
