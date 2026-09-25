from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.gui_project_editor_controller import ProjectEditorController
from src.subtitle_project import create_project, load_project
from src.video_sequence import SequenceTransition, VideoSequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_QML = REPOSITORY_ROOT / "src" / "ui" / "screens" / "MainWorkflowScreen.qml"
SHORT_SCREEN_QML = REPOSITORY_ROOT / "src" / "ui" / "screens" / "ShortModeScreen.qml"
HEADER_QML = REPOSITORY_ROOT / "src" / "ui" / "components" / "WorkspaceHeader.qml"
ACTION_BAR_QML = REPOSITORY_ROOT / "src" / "ui" / "components" / "ContextActionBar.qml"
SEQUENCE_PANEL_QML = REPOSITORY_ROOT / "src" / "ui" / "components" / "SequenceEditorPanel.qml"
MEDIA_BIN_QML = REPOSITORY_ROOT / "src" / "ui" / "components" / "MediaBinPanel.qml"


class ProductionIntegrationContractTests(unittest.TestCase):
    def test_production_workflow_routes_all_cross_workspace_actions_to_backend(self) -> None:
        workflow = WORKFLOW_QML.read_text(encoding="utf-8")
        short_screen = SHORT_SCREEN_QML.read_text(encoding="utf-8")
        header = HEADER_QML.read_text(encoding="utf-8")
        action_bar = ACTION_BAR_QML.read_text(encoding="utf-8")
        sequence_panel = SEQUENCE_PANEL_QML.read_text(encoding="utf-8")
        media_bin = MEDIA_BIN_QML.read_text(encoding="utf-8")

        # 保存・書き出しは入力確定を挟むため、直接呼び出しの文字列を固定しない。
        # test_workspace_header_save_commits_caption_key_input と
        # test_workspace_caption_key_input_is_committed_before_render が実操作を検証する。
        # The shared workflow is the only owner of navigation side effects.
        for route in (
            "onProjectOpenRequested: root.appBackend.browseProjectFile()",
            "onSourceSettingsRequested: sourcePopup.open()",
            "onOutputFolderRequested: root.appBackend.openOutputFolder()",
            "onShortWorkspaceRequested: root.openShortWorkspace()",
        ):
            with self.subTest(route=route):
                self.assertIn(route, workflow)

        # Short navigation snapshots the normal player before switching and
        # restores the backend-owned position when the workspace closes.
        self.assertRegex(
            workflow,
            r'root\.appBackend\.setWorkspacePlayerState\(\s*"normal-video",\s*mainPlayer\.position,\s*false\s*\)',
        )
        self.assertIn('root.appBackend.switchWorkspace("short-artifact")', workflow)
        self.assertIn('var playerState = root.appBackend.workspacePlayerState', workflow)
        self.assertIn('mainPlayer.position = Number(playerState.positionMs || 0)', workflow)
        self.assertIn('if (nextWorkspace === "normal-video")', workflow)

        # The short screen is a derived-artifact surface, not a second source
        # of truth for project, render, or navigation state.
        self.assertIn("visible: root.shortWorkspaceActive", workflow)
        self.assertIn("active: root.shortWorkspaceActive", workflow)
        self.assertIn("shortRoot.appBackend.renderShortVideo()", short_screen)
        self.assertIn("onClicked: shortRoot.mainRoot.closeShortWorkspace()", short_screen)
        self.assertIn('property string workspaceKind: "short-artifact"', workflow)

        for signal in (
            "projectOpenRequested",
            "sourceSettingsRequested",
            "saveRequested",
            "outputFolderRequested",
            "shortWorkspaceRequested",
            "renderRequested",
        ):
            with self.subTest(signal=signal):
                self.assertIn(f"signal {signal}()", header)
        self.assertIn("onClicked: actionBar.shortModeRequested()", action_bar)
        self.assertIn("onClicked: actionBar.renderRequested()", action_bar)

        # Sequence data and mutations remain backend-owned in the production
        # panel; this cross-file check prevents a future UI-only integration.
        self.assertIn("backend.sequenceClips", sequence_panel)
        self.assertIn("backend.insertSequenceClip", sequence_panel)
        self.assertIn("backend.mediaBinAssets", media_bin)
        self.assertIn("backend.addSequenceAssets", media_bin)
        self.assertIn("backend.addSequenceClip", media_bin)
        self.assertIn("backend.moveSequenceClip", sequence_panel)
        self.assertIn("backend.trimSequenceClip", sequence_panel)
        self.assertIn("backend.setSequenceTransition", sequence_panel)
        self.assertIn("backend.setSequenceClipAudio", sequence_panel)
        self.assertNotIn("project[", sequence_panel)
        self.assertNotIn("project.", sequence_panel)
        self.assertNotIn("project[", media_bin)
        self.assertNotIn("project.", media_bin)

    def test_sequence_and_short_artifact_survive_autosave_reload_as_one_project_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.mp4"
            second = root / "second.mp4"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            project_path = root / "integrated.subtitle-project.json"
            output_dir = root / "output"

            project = create_project(
                video_path=first,
                output_dir=output_dir,
                segments=[],
                duration_seconds=8.0,
            )
            project["short_video"] = {
                "enabled": True,
                "time_basis": "source",
                "clips": [{"segment_id": "short-a", "start": 1.0, "end": 2.0}],
            }

            controller = ProjectEditorController(root)
            self.addCleanup(controller.shutdown)
            controller.save_new_project(project_path, project)
            controller.load(project_path)

            updated = controller.apply_sequence_mutation(
                lambda sequence: sequence
                .add_asset(str(second), 6.0, asset_id="asset-2")
                .add_clip(
                    "asset-2",
                    1.0,
                    4.0,
                    clip_id="clip-2",
                    transition=SequenceTransition(type="crossfade", duration=0.25),
                )
            )
            self.assertIsNotNone(updated)
            controller.project["short_video"]["clips"].append(
                {"segment_id": "short-b", "start": 3.0, "end": 4.0}
            )
            controller.mark_dirty()
            controller.autosave()
            revision = controller.autosave_revision
            autosave_path = controller.autosave_path
            controller.autosave_future.result(timeout=5)
            controller.finish_autosave(revision, autosave_path, "")

            persisted = load_project(project_path)
            persisted_sequence = VideoSequence.from_json(persisted["sequence"])
            self.assertEqual(
                [asset.id for asset in persisted_sequence.assets],
                ["asset-video", "asset-2"],
            )
            self.assertEqual(
                [clip.id for clip in persisted_sequence.clips],
                ["clip-video", "clip-2"],
            )
            self.assertEqual(
                persisted_sequence.clips[1].transition,
                SequenceTransition(type="crossfade", duration=0.25),
            )
            self.assertEqual(
                persisted["short_video"]["clips"],
                [
                    {"segment_id": "short-a", "start": 1.0, "end": 2.0},
                    {"segment_id": "short-b", "start": 3.0, "end": 4.0},
                ],
            )

            restarted = ProjectEditorController(root)
            self.addCleanup(restarted.shutdown)
            reloaded = restarted.load(project_path)
            reloaded_sequence = VideoSequence.from_json(reloaded["sequence"])
            self.assertEqual(
                [clip.id for clip in reloaded_sequence.clips],
                ["clip-video", "clip-2"],
            )
            self.assertEqual(reloaded["short_video"]["time_basis"], "source")
            self.assertEqual(len(reloaded["short_video"]["clips"]), 2)


if __name__ == "__main__":
    unittest.main()
