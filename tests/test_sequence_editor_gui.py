from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from src.subtitle_project import create_project, load_project, save_project
from tests.edit_bay_gui_test_session import EditBayGuiTestSession
from tests.gui_test_harness import GuiTestHarness


class SequenceEditorGuiTests(unittest.TestCase):
    """GUI/backend contracts for the #408 media-bin sequence editor slice."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._session = EditBayGuiTestSession()
        cls.app = cls._session.backend

    @classmethod
    def tearDownClass(cls) -> None:
        cls._session.cleanup()

    def setUp(self) -> None:
        self.addCleanup(self._session.finish_test)
        self.root = self._session.prepare_test(self._testMethodName)
        self.media_validation_patch = patch.object(
            self.app,
            "_is_supported_media_file",
            return_value=(True, ""),
        )
        self.media_validation_patch.start()
        self.addCleanup(self.media_validation_patch.stop)
        qml_root = Path(__file__).resolve().parents[1] / "src" / "ui"
        self.gui = GuiTestHarness(self.app, backend=self.app, qml_roots=(qml_root,))
        self.addCleanup(self.gui.cleanup)

    def _make_project(self) -> tuple[Path, Path, Path]:
        first_video = self.root / "first.mp4"
        second_video = self.root / "second.mp4"
        first_video.write_bytes(b"first video fixture")
        second_video.write_bytes(b"second video fixture")
        output_dir = self.root / "export"
        output_dir.mkdir()
        project = create_project(
            video_path=first_video,
            output_dir=output_dir,
            segments=[],
            duration_seconds=10.0,
        )
        project_path = output_dir / "sequence.subtitle-project.json"
        save_project(project_path, project)
        self.assertTrue(self.app._load_project_path(project_path, update_sources=False))
        self.app.autosave_timer.stop()
        return project_path, first_video, second_video

    def _add_second_asset(self, second_video: Path, *, duration: float = 8.0) -> str:
        with patch("src.gui.probe_media_duration", return_value=duration):
            self.assertTrue(self.app.addSequenceAsset(str(second_video)))
        assets = self.app.mediaBinAssets
        self.assertEqual(len(assets), 2)
        return str(assets[-1]["id"])

    def test_sequence_edits_use_controller_history_and_persist_in_order(self) -> None:
        project_path, _first_video, second_video = self._make_project()
        second_asset_id = self._add_second_asset(second_video)

        self.assertTrue(self.app.addSequenceClip(second_asset_id))
        second_clip = next(
            clip for clip in self.app.sequenceClips if clip["assetId"] == second_asset_id
        )
        second_clip_id = str(second_clip["clipId"])
        self.assertTrue(self.app.trimSequenceClip(second_clip_id, 1.0, 4.0))
        self.assertTrue(
            self.app.setSequenceClipAudio(
                second_clip_id,
                False,
                1.25,
                -0.1,
                True,
            )
        )

        # Reordering resets only the moved clip's stale incoming transition;
        # configure the transition after the order change on the new incoming
        # clip so the persisted boundary remains valid.
        self.assertTrue(self.app.moveSequenceClip(second_clip_id, 0))
        first_clip = self.app.sequenceClips[0]
        legacy_clip_id = str(first_clip["clipId"])
        self.assertTrue(self.app.setSequenceTransition(legacy_clip_id, "crossfade", 0.5))

        view = self.app.sequenceView
        self.assertEqual([clip["clipId"] for clip in view["clips"]], [second_clip_id, legacy_clip_id])
        self.assertAlmostEqual(float(view["outputDuration"]), 12.5)
        self.assertTrue(self.app.setSequencePlayhead(1500))
        playhead = self.app.sequencePlayhead
        self.assertEqual(playhead["clipId"], second_clip_id)
        self.assertAlmostEqual(float(playhead["sourceTime"]), 2.5)
        self.assertTrue(self.app.projectDirty)
        self.assertTrue(self.app.canUndo)

        saved_clip = next(clip for clip in view["clips"] if clip["clipId"] == second_clip_id)
        self.assertAlmostEqual(float(saved_clip["sourceStart"]), 1.0)
        self.assertAlmostEqual(float(saved_clip["sourceEnd"]), 4.0)
        self.assertFalse(bool(saved_clip["audioLinked"]))
        self.assertAlmostEqual(float(saved_clip["volume"]), 1.25)
        self.assertAlmostEqual(float(saved_clip["audioOffset"]), -0.1)
        self.assertTrue(bool(saved_clip["muted"]))

        self.assertTrue(self.app.saveProject())
        saved = load_project(project_path)
        self.assertEqual(
            [clip["id"] for clip in saved["sequence"]["clips"]],
            [second_clip_id, legacy_clip_id],
        )

        self.app.undoCutEdit()
        self.assertTrue(self.app.canRedo)
        self.assertEqual(self.app.sequenceView["clips"][1]["transition"]["type"], "cut")
        self.app.redoCutEdit()
        self.assertEqual(
            self.app.sequenceView["clips"][1]["transition"]["type"],
            "crossfade",
        )

        self.assertTrue(self.app._load_project_path(project_path, update_sources=False))
        reloaded = self.app.sequenceView
        self.assertEqual(
            [clip["clipId"] for clip in reloaded["clips"]],
            [second_clip_id, legacy_clip_id],
        )
        self.assertAlmostEqual(float(reloaded["outputDuration"]), 12.5)

    def test_sequence_asset_validation_fails_closed(self) -> None:
        _project_path, _first_video, second_video = self._make_project()
        missing = self.root / "missing.mp4"
        self.assertFalse(self.app.addSequenceAsset(str(missing)))
        self.assertEqual(self.app.mediaBinAssets[0]["id"], "asset-video")
        self.assertTrue(self.app.sequenceError)

        second_video.write_bytes(b"second video fixture")
        with patch("src.gui.probe_media_duration", return_value=0.0):
            self.assertFalse(self.app.addSequenceAsset(str(second_video)))
        self.assertEqual(len(self.app.mediaBinAssets), 1)
        self.assertTrue(self.app.sequenceError)

    def test_main_workflow_exposes_sequence_panel_and_dispatches_backend_actions(self) -> None:
        _project_path, _first_video, second_video = self._make_project()
        second_asset_id = self._add_second_asset(second_video)
        qml_path = Path(__file__).resolve().parents[1] / "src" / "ui" / "Main.qml"
        _engine, window = self.gui.load_qml(qml_path, width=1_280, height=820)

        panel = self.gui.find_item(window, "workspaceSequenceEditor")
        self.assertTrue(panel.isVisible())
        self.gui.find_item(window, "mediaBinPanel")
        self.gui.find_item(window, "sequenceClipList")
        self.gui.find_item(window, "mediaBinDropArea")
        self.gui.find_item(window, "sequenceClipDropArea")

        add_button = self.gui.find_visual_item_by_properties(
            panel,
            {"sequenceAssetId": second_asset_id},
            required_properties=("sequenceAssetId",),
        )
        before = len(self.app.sequenceClips)
        self.gui.click(window, add_button)
        self.gui.wait_until(
            lambda: len(self.app.sequenceClips) == before + 1,
            description="media-bin clip dispatch",
        )
        self.assertEqual(self.app.sequenceClips[-1]["assetId"], second_asset_id)

        undo_button = self.gui.find_item(window, "sequenceUndoButton")
        self.gui.click(window, undo_button)
        self.gui.wait_until(
            lambda: len(self.app.sequenceClips) == before,
            description="sequence undo dispatch",
        )
        redo_button = self.gui.find_item(window, "sequenceRedoButton")
        self.gui.click(window, redo_button)
        self.gui.wait_until(
            lambda: len(self.app.sequenceClips) == before + 1,
            description="sequence redo dispatch",
        )


if __name__ == "__main__":
    unittest.main()
