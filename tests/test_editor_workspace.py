from __future__ import annotations

import unittest

from src.editor_workspace import (
    EditorWorkspaceState,
    build_edit_mode_capabilities,
)


class OffsetTimeMapping:
    def source_to_output(self, position_ms: int) -> int:
        return position_ms - 1_000

    def output_to_source(self, position_ms: int) -> int:
        return position_ms + 1_000


class EditorWorkspaceStateTests(unittest.TestCase):
    def test_capabilities_are_independent_per_editing_feature(self) -> None:
        capabilities = build_edit_mode_capabilities(
            project_loaded=True,
            preview_available=True,
            audio_available=False,
        )

        self.assertTrue(capabilities.can_preview)
        self.assertTrue(capabilities.can_edit_subtitles)
        self.assertFalse(capabilities.can_cut)
        self.assertFalse(capabilities.can_mix_audio)
        self.assertEqual(capabilities.as_dict()["cutReason"], "カット編集は準備中です")
        self.assertIn("音声トラック", str(capabilities.as_dict()["audioReason"]))

    def test_missing_transcription_runtime_does_not_enter_capability_contract(self) -> None:
        capability_keys = build_edit_mode_capabilities(
            project_loaded=True,
            preview_available=True,
            audio_available=True,
        ).as_dict()

        self.assertNotIn("dependenciesReady", capability_keys)
        self.assertTrue(capability_keys["canEditSubtitles"])
        self.assertTrue(capability_keys["canMixAudio"])

    def test_missing_preview_does_not_disable_unrelated_editing_features(self) -> None:
        capabilities = build_edit_mode_capabilities(
            project_loaded=True,
            preview_available=False,
            audio_available=True,
        )

        self.assertFalse(capabilities.can_preview)
        self.assertTrue(capabilities.can_edit_subtitles)
        self.assertTrue(capabilities.can_mix_audio)

    def test_only_one_available_mode_can_be_selected(self) -> None:
        state = EditorWorkspaceState()
        capabilities = build_edit_mode_capabilities(
            project_loaded=True,
            preview_available=True,
            audio_available=True,
        )

        self.assertTrue(state.select_mode("audio", capabilities))
        self.assertEqual(state.current_mode, "audio")
        self.assertFalse(state.select_mode("cut", capabilities))
        self.assertEqual(state.current_mode, "audio")
        self.assertFalse(state.select_mode("unknown", capabilities))

    def test_mode_switch_preserves_the_shared_playhead(self) -> None:
        state = EditorWorkspaceState()
        capabilities = build_edit_mode_capabilities(
            project_loaded=True,
            preview_available=True,
            audio_available=True,
        )
        state.set_playhead(12_345, "source")

        state.select_mode("audio", capabilities)
        state.select_mode("subtitle", capabilities)

        self.assertEqual(state.playhead["sourcePositionMs"], 12_345)
        self.assertEqual(state.playhead["outputPositionMs"], 12_345)

    def test_source_and_output_positions_use_the_mapping_boundary(self) -> None:
        state = EditorWorkspaceState(OffsetTimeMapping())

        self.assertTrue(state.set_playhead(4_000, "source"))
        self.assertEqual(
            state.playhead,
            {"basis": "source", "sourcePositionMs": 4_000, "outputPositionMs": 3_000},
        )
        self.assertTrue(state.set_playhead(2_000, "output"))
        self.assertEqual(
            state.playhead,
            {"basis": "output", "sourcePositionMs": 3_000, "outputPositionMs": 2_000},
        )

    def test_mode_falls_back_when_its_capability_disappears(self) -> None:
        state = EditorWorkspaceState()
        available = build_edit_mode_capabilities(
            project_loaded=True,
            preview_available=True,
            audio_available=True,
        )
        state.select_mode("audio", available)

        changed = state.ensure_available_mode(
            build_edit_mode_capabilities(
                project_loaded=True,
                preview_available=True,
                audio_available=False,
            )
        )

        self.assertTrue(changed)
        self.assertEqual(state.current_mode, "subtitle")


if __name__ == "__main__":
    unittest.main()
