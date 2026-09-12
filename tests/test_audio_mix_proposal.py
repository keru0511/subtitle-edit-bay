from __future__ import annotations

from copy import deepcopy
import json
import unittest

from src.audio_mix_proposal import (
    AUDIO_MIX_PROPOSAL_OUTPUT_SCHEMA,
    AudioMixProposal,
    AudioMixProposalError,
    apply_audio_mix_proposal,
    audio_mix_state_revision,
    build_audio_mix_context,
    build_audio_mix_proposal,
    build_audio_mix_proposal_prompt,
)
from src.audio_mixer import reconcile_audio_mix


VOICE_ID = "audio:" + "1" * 32
BGM_ID = "audio:" + "2" * 32


def channels() -> list[dict]:
    return [
        {
            "id": VOICE_ID,
            "kind": "external",
            "label": "メイン音声",
            "path": "C:/Users/alice/private/voice.wav",
            "enabled": True,
            "muted": False,
            "solo": False,
            "volume_percent": 100.0,
        },
        {
            "id": BGM_ID,
            "kind": "external",
            "label": "BGM",
            "path": "C:/Users/alice/private/bgm.wav",
            "enabled": True,
            "muted": False,
            "solo": False,
            "volume_percent": 100.0,
        },
    ]


def codex_output(
    current: list[dict],
    operations: list[dict],
    *,
    revision: int = 7,
) -> dict:
    return {
        "schema_version": 1,
        "summary": "音量バランスを調整します",
        "warnings": [],
        "base_revision": revision,
        "audio_state_revision": audio_mix_state_revision(current),
        "operations": operations,
    }


def operation(
    operation_id: str,
    channel_id: str,
    changes: dict,
    *,
    reason: str = "現在のレベル差を整えるため",
) -> dict:
    return {
        "id": operation_id,
        "type": "update_audio_channel",
        "channel_id": channel_id,
        "changes": changes,
        "reason": reason,
    }


def stored_proposal(
    current: list[dict],
    operations: list[dict],
    *,
    revision: int = 7,
) -> dict:
    return build_audio_mix_proposal(
        codex_output(current, operations, revision=revision),
        current,
        project_revision=revision,
    )


class AudioMixProposalTests(unittest.TestCase):
    def test_context_is_path_free_and_includes_levels_revision_and_opaque_ids(self) -> None:
        current = channels()
        current[0]["label"] = r"C:\Users\alice\private\voice.wav"
        context = build_audio_mix_context(
            current,
            preview_levels={VOICE_ID: 0.7, BGM_ID: 0.2},
            master_level=0.8,
            limiter_reduction_db=1.5,
            playhead_seconds=12.25,
            range_seconds=(10.0, 15.0),
            project_revision=8,
        )

        self.assertEqual(context["channels"][0]["id"], VOICE_ID)
        self.assertEqual(context["channels"][0]["label"], "外部音声 1")
        self.assertEqual(context["channels"][0]["preview_level"], 0.7)
        self.assertEqual(context["master_level"], 0.8)
        self.assertEqual(context["limiter_reduction_db"], 1.5)
        self.assertEqual(context["playhead_seconds"], 12.25)
        self.assertEqual(context["project_revision"], 8)
        self.assertNotIn("path", context["channels"][0])
        self.assertNotIn("C:\\Users", repr(context))

    def test_legacy_project_without_track_key_never_exposes_absolute_source_path(self) -> None:
        source_path = r"C:\Users\alice\private\workspace\voice.wav"
        project = {
            "audio_sources": [{"path": source_path}],
            "audio_mix": {
                "version": 1,
                "customized": False,
                "channels": [
                    {
                        "id": f"external:{source_path}",
                        "kind": "external",
                        "label": source_path,
                        "path": source_path,
                        "enabled": True,
                        "muted": False,
                        "solo": False,
                        "volume_percent": 100.0,
                    }
                ],
            },
        }
        mix = reconcile_audio_mix(project, video_tracks=[])

        encoded = json.dumps(build_audio_mix_context(mix["channels"]), ensure_ascii=False)

        self.assertNotIn(source_path, encoded)
        self.assertNotIn("Users", encoded)
        self.assertNotIn("workspace", encoded)

    def test_codex_output_uses_state_dependent_changes_and_backend_adds_before_values(self) -> None:
        current = channels()
        before = deepcopy(current)
        raw = codex_output(
            current,
            [
                operation("voice-up", VOICE_ID, {"volume_percent": 112.0}),
                operation("bgm-down", BGM_ID, {"volume_percent": 72.0}),
            ],
        )

        proposal = build_audio_mix_proposal(raw, current, project_revision=7)

        self.assertEqual(current, before)
        self.assertEqual(proposal["operations"][0]["before"], {"volume_percent": 100.0})
        self.assertEqual(proposal["operations"][1]["changes"], {"volume_percent": 72.0})
        self.assertIn("preview_level", build_audio_mix_proposal_prompt("声を聞きやすくして"))

    def test_output_schema_is_closed_at_root_operation_and_changes(self) -> None:
        operation_schema = AUDIO_MIX_PROPOSAL_OUTPUT_SCHEMA["properties"]["operations"]["items"]

        self.assertFalse(AUDIO_MIX_PROPOSAL_OUTPUT_SCHEMA["additionalProperties"])
        self.assertFalse(operation_schema["additionalProperties"])
        self.assertFalse(operation_schema["properties"]["changes"]["additionalProperties"])
        self.assertEqual(
            set(operation_schema["properties"]["changes"]["properties"]),
            {"volume_percent", "muted", "solo", "enabled"},
        )

    def test_selected_operation_apply_uses_canonical_update_and_keeps_other_channel(self) -> None:
        current = channels()
        proposal = stored_proposal(
            current,
            [
                operation("mute-voice", VOICE_ID, {"muted": True}),
                operation("mute-bgm", BGM_ID, {"muted": True}),
            ],
            revision=3,
        )
        mix = {"version": 1, "customized": False, "channels": current}

        updated, changed = apply_audio_mix_proposal(
            mix,
            proposal,
            current_revision=3,
            selected_operation_ids={"mute-voice"},
        )

        self.assertEqual(changed, (VOICE_ID,))
        self.assertTrue(updated["channels"][0]["muted"])
        self.assertFalse(updated["channels"][1]["muted"])
        self.assertTrue(updated["customized"])
        self.assertFalse(mix["customized"])

        with self.assertRaisesRegex(AudioMixProposalError, "no audio operations"):
            apply_audio_mix_proposal(
                mix,
                proposal,
                current_revision=3,
                selected_operation_ids=set(),
            )

    def test_stale_project_or_audio_state_is_rejected(self) -> None:
        current = channels()
        proposal = stored_proposal(
            current,
            [operation("bgm-down", BGM_ID, {"volume_percent": 75.0})],
            revision=5,
        )
        mix = {"version": 1, "customized": False, "channels": current}
        with self.assertRaisesRegex(AudioMixProposalError, "stale"):
            apply_audio_mix_proposal(mix, proposal, current_revision=6)

        changed = deepcopy(mix)
        changed["channels"][1]["volume_percent"] = 90.0
        with self.assertRaisesRegex(AudioMixProposalError, "state changed"):
            apply_audio_mix_proposal(changed, proposal, current_revision=5)

    def test_invalid_channel_operation_and_volume_are_rejected(self) -> None:
        current = channels()
        missing = codex_output(
            current,
            [operation("missing", "audio:" + "f" * 32, {"muted": True})],
        )
        unknown = codex_output(
            current,
            [operation("unknown", BGM_ID, {"muted": True})],
        )
        unknown["operations"][0]["type"] = "delete_audio_channel"
        invalid_volume = codex_output(
            current,
            [operation("too-loud", BGM_ID, {"volume_percent": 201})],
        )

        for candidate in (missing, unknown, invalid_volume):
            with self.subTest(candidate=candidate):
                with self.assertRaises(AudioMixProposalError):
                    build_audio_mix_proposal(candidate, current, project_revision=7)

    def test_schema_version_and_state_revision_are_exact(self) -> None:
        current = channels()
        raw = codex_output(
            current,
            [operation("bgm-down", BGM_ID, {"volume_percent": 75.0})],
        )
        invalid_version = deepcopy(raw)
        invalid_version["schema_version"] = True
        invalid_revision = deepcopy(raw)
        invalid_revision["audio_state_revision"] = "sha256:" + "z" * 64

        for candidate in (invalid_version, invalid_revision):
            with self.subTest(candidate=candidate):
                with self.assertRaises(AudioMixProposalError):
                    AudioMixProposal.from_json(candidate)

    def test_non_finite_channel_volume_is_not_sent_to_codex(self) -> None:
        current = channels()
        current[0]["volume_percent"] = float("nan")

        with self.assertRaisesRegex(AudioMixProposalError, "volume_percent"):
            build_audio_mix_context(current)

    def test_unknown_root_operation_change_and_stored_before_fields_are_rejected(self) -> None:
        current = channels()
        raw = codex_output(
            current,
            [operation("bgm-down", BGM_ID, {"volume_percent": 75.0})],
        )
        candidates = []
        unknown_root = deepcopy(raw)
        unknown_root["method"] = "saveProject"
        candidates.append(unknown_root)
        unknown_operation = deepcopy(raw)
        unknown_operation["operations"][0]["method"] = "updateAudioMixChannel"
        candidates.append(unknown_operation)
        unknown_change = deepcopy(raw)
        unknown_change["operations"][0]["changes"]["path"] = "C:/private/audio.wav"
        candidates.append(unknown_change)

        for candidate in candidates:
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(AudioMixProposalError, "unsupported"):
                    build_audio_mix_proposal(candidate, current, project_revision=7)

        stored = stored_proposal(current, raw["operations"])
        stored["operations"][0]["before"]["method"] = "ignored"
        with self.assertRaisesRegex(AudioMixProposalError, "unsupported"):
            apply_audio_mix_proposal(
                {"version": 1, "customized": False, "channels": current},
                stored,
                current_revision=7,
            )

    def test_path_bearing_or_duplicate_channel_ids_are_rejected(self) -> None:
        unsafe = channels()
        unsafe[0]["id"] = r"external:C:\Users\alice\voice.wav"
        with self.assertRaisesRegex(AudioMixProposalError, "safe opaque"):
            build_audio_mix_context(unsafe)

        duplicate_channels = channels()
        duplicate_channels[1]["id"] = VOICE_ID
        with self.assertRaisesRegex(AudioMixProposalError, "duplicate"):
            build_audio_mix_context(duplicate_channels)

        raw = codex_output(
            channels(),
            [
                operation("first", VOICE_ID, {"volume_percent": 110.0}),
                operation("second", VOICE_ID, {"muted": False}),
            ],
        )
        with self.assertRaisesRegex(AudioMixProposalError, "at most one"):
            AudioMixProposal.from_json(raw)

        raw["operations"] = [operation("empty-reason", VOICE_ID, {"muted": True}, reason="")]
        with self.assertRaisesRegex(AudioMixProposalError, "reason"):
            AudioMixProposal.from_json(raw)

    def test_muting_every_output_requires_separate_confirmation(self) -> None:
        current = [channels()[0]]
        proposal = stored_proposal(
            current,
            [operation("mute", VOICE_ID, {"muted": True})],
            revision=2,
        )
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


if __name__ == "__main__":
    unittest.main()
