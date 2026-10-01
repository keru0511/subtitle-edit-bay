from __future__ import annotations

import unittest
from pathlib import Path
from tests.typed_case import TypedTestCase


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = REPOSITORY_ROOT / "src" / "ui"
WORKFLOW_QML = UI_ROOT / "screens" / "MainWorkflowScreen.qml"


class WorkspaceContractTests(TypedTestCase):
    def test_qml_has_no_local_workspace_compatibility_mirror(self) -> None:
        qml_files = sorted(UI_ROOT.rglob("*.qml"))
        forbidden_markers = (
            "property string currentWorkspace",
            "onCurrentWorkspaceChanged",
            "root.currentWorkspace",
        )
        offenders = {
            str(path.relative_to(REPOSITORY_ROOT)): [
                marker for marker in forbidden_markers if marker in path.read_text(encoding="utf-8")
            ]
            for path in qml_files
            if any(marker in path.read_text(encoding="utf-8") for marker in forbidden_markers)
        }
        self.assertEqual(offenders, {})

    def test_main_workflow_uses_backend_workspace_as_the_only_navigation_boundary(self) -> None:
        workflow = WORKFLOW_QML.read_text(encoding="utf-8")

        self.assertIn(
            'readonly property bool shortWorkspaceActive: root.appBackend\n'
            '        && root.appBackend.workspace.currentWorkspace === "short-artifact"',
            workflow,
        )
        self.assertIn('root.appBackend.workspace.switchWorkspace("short-artifact")', workflow)
        self.assertIn('root.appBackend.workspace.switchWorkspace("normal-video")', workflow)
        self.assertIn(
            'var nextWorkspace = String(root.appBackend.workspace.currentWorkspace || "normal-video")',
            workflow,
        )
        self.assertIn("function onWorkspaceChanged()", workflow)
        self.assertIn("var playerState = root.appBackend.workspace.workspacePlayerState", workflow)
        self.assertIn("mainPlayer.position = Number(playerState.positionMs || 0)", workflow)
        self.assertNotIn("root.currentWorkspace", workflow)

    def test_gui_workspace_callers_use_backend_api(self) -> None:
        gui_tests = (REPOSITORY_ROOT / "tests" / "test_gui_editor.py").read_text(encoding="utf-8")

        self.assertNotIn('window.setProperty("currentWorkspace"', gui_tests)
        self.assertNotIn('window.property("currentWorkspace"', gui_tests)
        self.assertIn('self.app.switchWorkspace("short-artifact")', gui_tests)
        self.assertIn('self.app.switchWorkspace("normal-video")', gui_tests)
        self.assertIn("self.app.currentWorkspace", gui_tests)

    def test_workspace_cutover_keeps_ai_state_on_backend_boundary(self) -> None:
        workflow = WORKFLOW_QML.read_text(encoding="utf-8")

        self.assertIn(
            'readonly property bool codexAuthenticated: root.appBackend\n'
            '        && root.appBackend.ai.codexAuthState === "authenticated"',
            workflow,
        )
        self.assertIn("backend: root.appBackend", workflow)
        self.assertIn("target: root.appBackend", workflow)


if __name__ == "__main__":
    unittest.main()
