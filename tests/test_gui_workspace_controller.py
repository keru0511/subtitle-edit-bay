from __future__ import annotations

import unittest

from src.gui_workspace_controller import (
    NORMAL_VIDEO_WORKSPACE,
    SHORT_ARTIFACT_WORKSPACE,
    WorkspaceNavigationController,
)


class WorkspaceNavigationControllerTests(unittest.TestCase):
    def test_round_trip_restores_normal_position_and_keeps_short_position_separate(self) -> None:
        controller = WorkspaceNavigationController()

        self.assertTrue(
            controller.update_player_state(
                NORMAL_VIDEO_WORKSPACE,
                12_345,
                playing=True,
            )
        )
        opened = controller.switch_workspace(SHORT_ARTIFACT_WORKSPACE)

        self.assertTrue(opened.accepted)
        self.assertTrue(opened.changed)
        self.assertEqual(controller.current_workspace, SHORT_ARTIFACT_WORKSPACE)
        self.assertEqual(opened.restored_position_ms, 0)
        self.assertEqual(controller.normal_video_player.position_ms, 12_345)
        self.assertFalse(controller.normal_video_player.playing)

        self.assertTrue(
            controller.update_player_state(
                SHORT_ARTIFACT_WORKSPACE,
                98_765,
                playing=True,
            )
        )
        closed = controller.switch_workspace(NORMAL_VIDEO_WORKSPACE)

        self.assertTrue(closed.accepted)
        self.assertTrue(closed.changed)
        self.assertEqual(closed.restored_position_ms, 12_345)
        self.assertEqual(controller.current_workspace, NORMAL_VIDEO_WORKSPACE)
        self.assertEqual(controller.normal_video_player.position_ms, 12_345)
        self.assertEqual(controller.short_artifact_player.position_ms, 98_765)
        self.assertFalse(controller.short_artifact_player.playing)

    def test_running_or_active_job_fails_closed_without_mutating_workspace(self) -> None:
        controller = WorkspaceNavigationController()
        controller.update_player_state(NORMAL_VIDEO_WORKSPACE, 4_000)

        running = controller.switch_workspace(SHORT_ARTIFACT_WORKSPACE, running=True)
        active_job = controller.switch_workspace(
            SHORT_ARTIFACT_WORKSPACE,
            active_job="render",
        )

        for result in (running, active_job):
            self.assertFalse(result.accepted)
            self.assertFalse(result.changed)
            self.assertIn("処理中", result.reason)
            self.assertEqual(result.current, NORMAL_VIDEO_WORKSPACE)
        self.assertEqual(controller.current_workspace, NORMAL_VIDEO_WORKSPACE)
        self.assertEqual(controller.normal_video_player.position_ms, 4_000)

    def test_unknown_workspace_is_rejected_without_touching_player_state(self) -> None:
        controller = WorkspaceNavigationController()
        controller.update_player_state(NORMAL_VIDEO_WORKSPACE, 8_000)

        result = controller.switch_workspace("short")

        self.assertFalse(result.accepted)
        self.assertFalse(result.changed)
        self.assertIn("未知", result.reason)
        self.assertEqual(controller.current_workspace, NORMAL_VIDEO_WORKSPACE)
        self.assertEqual(controller.normal_video_player.position_ms, 8_000)

    def test_player_state_updates_are_workspace_scoped(self) -> None:
        controller = WorkspaceNavigationController()

        self.assertFalse(controller.update_player_state("missing", 100))
        self.assertTrue(controller.update_player_state(SHORT_ARTIFACT_WORKSPACE, 2_500))
        self.assertEqual(controller.normal_video_player.position_ms, 0)
        self.assertEqual(controller.short_artifact_player.position_ms, 2_500)
        self.assertEqual(
            controller.player_states(),
            {
                NORMAL_VIDEO_WORKSPACE: {"positionMs": 0, "playing": False},
                SHORT_ARTIFACT_WORKSPACE: {"positionMs": 2_500, "playing": False},
            },
        )


if __name__ == "__main__":
    unittest.main()
