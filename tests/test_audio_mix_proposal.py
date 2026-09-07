from __future__ import annotations

from copy import deepcopy
import unittest

from src.audio_mix_proposal import (
    AudioMixProposalError,
    apply_audio_mix_proposal,
    build_audio_mix_context,
    build_audio_mix_proposal,
)


def channels() -> list[dict]:
    return [
        {
            "id": "voice-main",
            "kind": "external",
            "label": "メイン音声",
            "path": "C:/private/voice.wav",
            "enabled": True,
            "muted": False,
            "solo": False,
            "volume_percent": 100.0,
        },
        {
            "id": "bgm",
            "kind": "external",
            "label": "BGM",
            "path": "C:/private/bgm.wav",
            "enabled": True,
            "muted": False,
            "solo": False,
            "volume_percent": 100.0,
        },
    ]


class AudioMixProposalTests(unittest.TestCase):
    def test_context_is_path_free_and_includes_levels_and_stable_ids(self) -> None:
        context = build_audio_mix_context(
            channels(),
            preview_levels={"voice-main": 0.7, "bgm": 0.2},
            master_level=0.8,
            limiter_reduction_db=1.5,
            playhead_seconds=12.25,
            range_seconds=(10.0, 15.0),
        )

        self.assertEqual(context["channels"][0]["id"], "voice-main")
        self.assertEqual(context["channels"][0]["preview_level"], 0.7)
        self.assertEqual(context["master_level"], 0.8)
        self.assertEqual(context["limiter_reduction_db"], 1.5)
        self.assertEqual(context["playhead_seconds"], 12.25)
        self.assertNotIn("path", context["channels"][0])
        self.assertNotIn("C:/", repr(context))

    def test_bgm_down_and_voice_up_generate_only_target_operations_without_mutation(self) -> None:
        current = channels()
        before = deepcopy(current)

        bgm = build_audio_mix_proposal("BGMを少し下げて", current, project_revision=8)
        voice = build_audio_mix_proposal("声を上げて", current, project_revision=8)

        self.assertEqual(current, before)
        self.assertEqual(bgm["base_revision"], 8)
        self.assertEqual(bgm["operations"][0]["channel_id"], "bgm")
        self.assertEqual(bgm["operations"][0]["changes"], {"volume_percent": 85.0})
        self.assertEqual(voice["operations"][0]["channel_id"], "voice-main")
        self.assertEqual(voice["operations"][0]["changes"], {"volume_percent": 120.0})

    def test_selected_operation_apply_revalidates_and_keeps_other_channel(self) -> None:
        current = channels()
        current[1]["label"] = "サブ音声"
        proposal = build_audio_mix_proposal("音声をミュート", current, project_revision=3)
        # Both voice channels are proposed. Select only one operation.
        selected_id = proposal["operations"][0]["id"]
        updated, changed = apply_audio_mix_proposal(
            {"version": 1, "customized": False, "channels": current},
            proposal,
            current_revision=3,
            selected_operation_ids={selected_id},
        )

        self.assertEqual(changed, (proposal["operations"][0]["channel_id"],))
        self.assertTrue(updated["channels"][0]["muted"])
        self.assertFalse(updated["channels"][1]["muted"])
        self.assertTrue(updated["customized"])

        with self.assertRaisesRegex(AudioMixProposalError, "no audio operations"):
            apply_audio_mix_proposal(
                {"version": 1, "customized": False, "channels": current},
                proposal,
                current_revision=3,
                selected_operation_ids=set(),
            )

    def test_stale_project_or_audio_state_is_rejected(self) -> None:
        current = channels()
        proposal = build_audio_mix_proposal("BGMを下げて", current, project_revision=5)
        mix = {"version": 1, "customized": False, "channels": current}
        with self.assertRaisesRegex(AudioMixProposalError, "stale"):
            apply_audio_mix_proposal(mix, proposal, current_revision=6)

        changed = deepcopy(mix)
        changed["channels"][1]["volume_percent"] = 90.0
        with self.assertRaisesRegex(AudioMixProposalError, "state changed"):
            apply_audio_mix_proposal(changed, proposal, current_revision=5)

    def test_invalid_channel_operation_and_volume_are_rejected(self) -> None:
        current = channels()
        mix = {"version": 1, "customized": False, "channels": current}
        proposal = build_audio_mix_proposal("BGMを下げて", current, project_revision=5)
        cases = []
        missing = deepcopy(proposal)
        missing["operations"][0]["channel_id"] = "missing"
        cases.append(missing)
        unknown = deepcopy(proposal)
        unknown["operations"][0]["type"] = "delete_audio_channel"
        cases.append(unknown)
        invalid_volume = deepcopy(proposal)
        invalid_volume["operations"][0]["changes"]["volume_percent"] = 201
        cases.append(invalid_volume)

        for candidate in cases:
            with self.subTest(candidate=candidate):
                with self.assertRaises(AudioMixProposalError):
                    apply_audio_mix_proposal(mix, candidate, current_revision=5)

    def test_muting_every_output_requires_separate_confirmation(self) -> None:
        current = [channels()[0]]
        proposal = build_audio_mix_proposal("音声をミュート", current, project_revision=2)
        mix = {"version": 1, "customized": False, "channels": current}

        with self.assertRaisesRegex(AudioMixProposalError, "every output"):
            apply_audio_mix_proposal(mix, proposal, current_revision=2)
        updated, _changed = apply_audio_mix_proposal(
            mix,
            proposal,
            current_revision=2,
            allow_silence=True,
        )
        self.assertTrue(updated["channels"][0]["muted"])

    def test_ambiguous_target_and_unrecognized_change_are_rejected(self) -> None:
        with self.assertRaisesRegex(AudioMixProposalError, "unique channel"):
            build_audio_mix_proposal("バランスを良くして", channels(), project_revision=1)
        with self.assertRaisesRegex(AudioMixProposalError, "supported change"):
            build_audio_mix_proposal("BGMをいい感じに", channels(), project_revision=1)


if __name__ == "__main__":
    unittest.main()
