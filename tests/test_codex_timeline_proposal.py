from __future__ import annotations

from copy import deepcopy
import tempfile
import unittest

from src.codex_timeline_proposal import (
    TIMELINE_PROPOSAL_OUTPUT_SCHEMA,
    TimelineProposal,
    TimelineProposalConfirmationRequired,
    TimelineProposalError,
    TimelineProposalRevisionConflict,
    apply_timeline_proposal,
    build_timeline_proposal_context,
    timeline_state_revision,
)
from src.short_video_schema import ShortVideo
from src.subtitle_project import create_project


def project() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as directory:
        result = create_project(
            video_path="C:/private/source.mp4",
            output_dir=directory,
            duration_seconds=120.0,
            segments=[
                {"id": "s1", "start": 0.0, "end": 20.0, "text": "opening", "speaker": "A"},
                {"id": "s2", "start": 30.0, "end": 50.0, "text": "topic", "speaker": "B"},
                {"id": "s3", "start": 80.0, "end": 110.0, "text": "ending", "speaker": "A"},
            ],
        )
    result["short_video"] = {
        "enabled": True,
        "clips": [
            {"proposal_id": "clip-a", "segment_id": "s1", "start": 2.0, "end": 10.0},
            {"proposal_id": "clip-b", "segment_id": "s2", "start": 32.0, "end": 40.0},
        ],
    }
    return result


def proposal(
    target: str,
    state: dict[str, object],
    operations: list[dict[str, object]],
    *,
    revision: int = 7,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "summary": "構成案",
        "target": target,
        "operations": operations,
        "warnings": [],
        "base_revision": revision,
        "base_state_revision": timeline_state_revision(state, target),
    }


class CodexTimelineProposalTests(unittest.TestCase):
    def test_context_is_path_free_uses_source_time_and_stable_ids(self) -> None:
        state = project()
        state["short_video"]["clips"][0].pop("proposal_id")
        context = build_timeline_proposal_context(
            state,
            target="short",
            project_revision=7,
            highlight_candidates=[
                {
                    "id": "highlight-1",
                    "start": 80.0,
                    "end": 90.0,
                    "score": 0.9,
                    "reason": "盛り上がり",
                    "source_segment_ids": ["s3"],
                    "path": "C:/private/cache.json",
                }
            ],
            selection={"basis": "source", "sourcePositionMs": 1000, "path": "C:/private/selection.json"},
        )

        self.assertEqual(context["time_basis"], "source")
        self.assertTrue(context["target_state"]["clips"][0]["proposal_id"].startswith("short-clip-"))
        self.assertNotIn("path", context["highlight_candidates"][0])
        self.assertNotIn("path", context["selection"])
        self.assertNotIn("C:/private", str(context))

    def test_normal_operations_reuse_video_timeline_without_mutating_input(self) -> None:
        state = project()
        state["timeline"] = {"cuts": [{"id": "old", "source_start": 10.0, "source_end": 12.0}]}
        original = deepcopy(state)
        payload = proposal(
            "normal",
            state,
            [
                {"id": "add", "type": "remove_range", "source_start": 20.0, "source_end": 25.0},
                {
                    "id": "update",
                    "type": "update_cut_range",
                    "cut_id": "old",
                    "source_start": 8.0,
                    "source_end": 13.0,
                },
            ],
        )

        result = apply_timeline_proposal(state, payload, current_revision=7)

        self.assertEqual(state, original)
        self.assertEqual(result.applied_operation_ids, ("add", "update"))
        self.assertEqual(
            result.project["timeline"]["cuts"],
            [
                {"id": "old", "source_start": 8.0, "source_end": 13.0},
                {"id": "add", "source_start": 20.0, "source_end": 25.0},
            ],
        )

    def test_output_schema_describes_operation_fields(self) -> None:
        operation_schema = TIMELINE_PROPOSAL_OUTPUT_SCHEMA["properties"]["operations"]["items"]

        self.assertFalse(operation_schema["additionalProperties"])
        self.assertIn("remove_range", operation_schema["properties"]["type"]["enum"])
        self.assertIn("clip_id", operation_schema["properties"])

    def test_normal_restore_and_selected_apply_are_atomic(self) -> None:
        state = project()
        state["timeline"] = {
            "cuts": [
                {"id": "first", "source_start": 10.0, "source_end": 20.0},
                {"id": "second", "source_start": 40.0, "source_end": 50.0},
            ]
        }
        payload = proposal(
            "normal",
            state,
            [
                {"id": "restore-first", "type": "restore_cut", "cut_id": "first"},
                {"id": "restore-part", "type": "restore_range", "source_start": 44.0, "source_end": 46.0},
            ],
        )

        result = apply_timeline_proposal(
            state,
            payload,
            current_revision=7,
            selected_operation_ids={"restore-first"},
        )
        split = apply_timeline_proposal(
            state,
            payload,
            current_revision=7,
            selected_operation_ids={"restore-part"},
        )

        self.assertEqual([cut["id"] for cut in result.project["timeline"]["cuts"]], ["second"])
        self.assertEqual(
            [cut["id"] for cut in split.project["timeline"]["cuts"]],
            ["first", "second", "restore-part-split-1"],
        )
        with self.assertRaisesRegex(TimelineProposalError, "no proposal operations"):
            apply_timeline_proposal(state, payload, current_revision=7, selected_operation_ids=set())
        with self.assertRaisesRegex(TimelineProposalError, "unknown selected"):
            apply_timeline_proposal(state, payload, current_revision=7, selected_operation_ids={"missing"})

    def test_stale_project_or_target_state_is_rejected(self) -> None:
        state = project()
        payload = proposal(
            "normal",
            state,
            [{"id": "add", "type": "add_cut", "source_start": 10.0, "source_end": 12.0}],
        )

        with self.assertRaisesRegex(TimelineProposalRevisionConflict, "project"):
            apply_timeline_proposal(state, payload, current_revision=8)
        state["timeline"] = {"cuts": [{"id": "manual", "source_start": 1.0, "source_end": 2.0}]}
        with self.assertRaisesRegex(TimelineProposalRevisionConflict, "timeline state"):
            apply_timeline_proposal(state, payload, current_revision=7)

    def test_invalid_ranges_and_full_video_removal_are_rejected(self) -> None:
        state = project()
        too_short = proposal(
            "short",
            state,
            [
                {
                    "id": "bad",
                    "type": "add_clip_by_range",
                    "clip_id": "clip-bad",
                    "source_start": 5.0,
                    "source_end": 5.0,
                }
            ],
        )
        whole = proposal(
            "normal",
            state,
            [{"id": "whole", "type": "add_cut", "source_start": 0.0, "source_end": 120.0}],
        )

        with self.assertRaisesRegex(TimelineProposalError, "at least"):
            apply_timeline_proposal(state, too_short, current_revision=7)
        with self.assertRaises(TimelineProposalConfirmationRequired):
            apply_timeline_proposal(state, whole, current_revision=7)
        with self.assertRaisesRegex(TimelineProposalError, "entire video"):
            apply_timeline_proposal(state, whole, current_revision=7, confirmed_large_change=True)

    def test_short_add_update_move_and_remove_use_stable_clip_ids(self) -> None:
        state = project()
        payload = proposal(
            "short",
            state,
            [
                {
                    "id": "add-op",
                    "type": "add_clip_by_range",
                    "clip_id": "clip-c",
                    "source_start": 82.0,
                    "source_end": 90.0,
                },
                {
                    "id": "trim",
                    "type": "update_clip_range",
                    "clip_id": "clip-b",
                    "source_start": 34.0,
                    "source_end": 39.0,
                },
                {"id": "move", "type": "move_clip", "clip_id": "clip-c", "before_clip_id": "clip-a"},
                {"id": "remove", "type": "remove_clip", "clip_id": "clip-a"},
            ],
        )

        result = apply_timeline_proposal(state, payload, current_revision=7)
        clips = result.project["short_video"]["clips"]

        self.assertEqual([clip["proposal_id"] for clip in clips], ["clip-c", "clip-b"])
        self.assertEqual((clips[1]["start"], clips[1]["end"]), (34.0, 39.0))
        self.assertEqual(ShortVideo.from_json(result.project["short_video"]).to_json(), result.project["short_video"])

    def test_short_segment_clip_cannot_expand_outside_segment(self) -> None:
        state = project()
        payload = proposal(
            "short",
            state,
            [
                {
                    "id": "trim",
                    "type": "update_clip_range",
                    "clip_id": "clip-a",
                    "source_start": 2.0,
                    "source_end": 25.0,
                }
            ],
        )

        with self.assertRaisesRegex(TimelineProposalError, "inside its segment"):
            apply_timeline_proposal(state, payload, current_revision=7)

    def test_highlight_candidate_and_duration_target_are_validated(self) -> None:
        state = project()
        payload = proposal(
            "short",
            state,
            [
                {
                    "id": "highlight-op",
                    "type": "use_highlight_candidate",
                    "clip_id": "clip-highlight",
                    "highlight_candidate_id": "highlight-1",
                },
                {"id": "target", "type": "set_short_duration_target", "target_seconds": 60.0},
            ],
        )
        highlights = [
            {
                "id": "highlight-1",
                "start": 82.0,
                "end": 92.0,
                "source_segment_ids": ["s3"],
            }
        ]

        result = apply_timeline_proposal(
            state,
            payload,
            current_revision=7,
            highlight_candidates=highlights,
        )

        section = result.project["short_video"]
        self.assertEqual(section["duration_target_seconds"], 60.0)
        self.assertEqual(section["clips"][-1]["highlight_candidate_id"], "highlight-1")
        with self.assertRaisesRegex(TimelineProposalError, "not found"):
            apply_timeline_proposal(state, payload, current_revision=7, highlight_candidates=[])

    def test_removing_every_short_clip_requires_large_change_confirmation(self) -> None:
        state = project()
        payload = proposal(
            "short",
            state,
            [
                {"id": "remove-a", "type": "remove_clip", "clip_id": "clip-a"},
                {"id": "remove-b", "type": "remove_clip", "clip_id": "clip-b"},
            ],
        )

        with self.assertRaises(TimelineProposalConfirmationRequired):
            apply_timeline_proposal(state, payload, current_revision=7)
        result = apply_timeline_proposal(state, payload, current_revision=7, confirmed_large_change=True)
        self.assertEqual(result.project["short_video"]["clips"], [])

    def test_schema_separates_target_specific_operations(self) -> None:
        state = project()
        payload = proposal(
            "normal",
            state,
            [{"id": "wrong", "type": "remove_clip", "clip_id": "clip-a"}],
        )

        with self.assertRaisesRegex(TimelineProposalError, "unsupported normal operation"):
            TimelineProposal.from_json(payload)

    def test_parser_rejects_non_string_text_and_irrelevant_operation_fields(self) -> None:
        state = project()
        payload = proposal(
            "normal",
            state,
            [{"id": "clear", "type": "clear_cuts", "clip_id": "ignored"}],
        )

        with self.assertRaisesRegex(TimelineProposalError, "clear_cuts.*clip_id"):
            TimelineProposal.from_json(payload)

        payload["operations"] = [{"id": "clear", "type": "clear_cuts", "reason": ["invalid"]}]
        with self.assertRaisesRegex(TimelineProposalError, "reason must be a string"):
            TimelineProposal.from_json(payload)

        payload["operations"] = [{"id": "clear", "type": "clear_cuts"}]
        payload["summary"] = {"text": "invalid"}
        with self.assertRaisesRegex(TimelineProposalError, "summary must be a string"):
            TimelineProposal.from_json(payload)


if __name__ == "__main__":
    unittest.main()
