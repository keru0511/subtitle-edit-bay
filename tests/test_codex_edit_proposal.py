from __future__ import annotations

import copy
import tempfile
import unittest

from src.codex_edit_proposal import (
    CodexEditProposal,
    EditProposalError,
    EditProposalRevisionConflict,
    apply_edit_proposal,
    build_undo_entry,
)
from src.subtitle_project import create_project


def _project() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temp_dir:
        return create_project(
            video_path="C:/fixtures/input.mp4",
            output_dir=temp_dir,
            segments=[
                {"id": "s1", "start": 0.0, "end": 1.0, "text": "最初", "speaker": "A", "words": []},
                {"id": "s2", "start": 1.2, "end": 2.0, "text": "次", "speaker": "B", "words": []},
            ],
            duration_seconds=2.0,
        )


class CodexEditProposalTests(unittest.TestCase):
    def test_schema_rejects_unknown_operation_fields(self) -> None:
        with self.assertRaisesRegex(EditProposalError, "unsupported fields"):
            CodexEditProposal.from_json(
                {
                    "summary": "bad",
                    "warnings": [],
                    "operations": [
                        {"type": "update_segment", "segment_id": "s1", "changes": {"html": "x"}}
                    ],
                }
            )

    def test_all_mvp_operations_create_structured_diff(self) -> None:
        project = _project()
        original = copy.deepcopy(project)
        proposal = CodexEditProposal.from_json(
            {
                "summary": "字幕を整理",
                "warnings": [],
                "operations": [
                    {"id": "update", "type": "update_segment", "segment_id": "s1", "changes": {"text": "修正"}},
                    {"id": "add", "type": "add_segment", "segment": {"id": "s3", "start": 2.1, "end": 2.5, "text": "追加", "speaker": "A"}},
                    {"id": "split", "type": "split_segment", "segment_id": "s2", "split_at": 1.6, "new_segment_id": "s2b"},
                ],
            }
        )
        result = apply_edit_proposal(project, proposal)

        self.assertEqual(project, original)
        self.assertEqual(result.applied_operation_ids, ("update", "add", "split"))
        self.assertEqual(len(result.diff["added"]), 2)
        self.assertGreaterEqual(len(result.diff["updated"]), 1)
        self.assertIn("s1", result.changed_segment_ids)
        self.assertEqual(
            [item["id"] for item in result.project["segments"]],
            ["s1", "s2", "s2b", "s3"],
        )

    def test_merge_delete_and_selected_apply_are_atomic(self) -> None:
        project = _project()
        proposal = {
            "summary": "一部適用",
            "warnings": [],
            "operations": [
                {"id": "delete", "type": "delete_segment", "segment_id": "s1"},
                {"id": "merge", "type": "merge_segments", "segment_ids": ["s1", "s2"]},
            ],
        }
        result = apply_edit_proposal(project, proposal, selected_operation_ids={"delete"})
        self.assertEqual([item["id"] for item in result.project["segments"]], ["s2"])
        self.assertEqual(result.applied_operation_ids, ("delete",))

        before = copy.deepcopy(project)
        with self.assertRaises(EditProposalError):
            apply_edit_proposal(
                project,
                {
                    "summary": "失敗",
                    "warnings": [],
                    "operations": [
                        {"type": "update_segment", "segment_id": "s1", "changes": {"text": "変更"}},
                        {"type": "delete_segment", "segment_id": "missing"},
                    ],
                },
            )
        self.assertEqual(project, before)

    def test_revision_conflict_and_undo_entry(self) -> None:
        project = _project()
        with self.assertRaises(EditProposalRevisionConflict):
            apply_edit_proposal(
                project,
                {"summary": "stale", "base_revision": 3, "warnings": [], "operations": [{"type": "delete_segment", "segment_id": "s1"}]},
                current_revision=4,
            )
        after = copy.deepcopy(project)
        after["segments"][0]["text"] = "after"
        entry = build_undo_entry(project, after)
        after["segments"][0]["text"] = "mutated"
        self.assertEqual(entry["kind"], "codex_proposal")
        self.assertEqual(entry["after"]["segments"][0]["text"], "after")

    def test_split_partitions_words_and_merge_restores_sorted_words(self) -> None:
        project = _project()
        project["segments"][0]["words"] = [
            {"word": "最初", "start": 0.1, "end": 0.4},
        ]
        project["segments"][1]["words"] = [
            {"word": "次", "start": 1.2, "end": 1.4},
            {"word": "です", "start": 1.6, "end": 1.9},
        ]
        split = CodexEditProposal.from_json(
            {
                "summary": "単語を分割",
                "warnings": [],
                "operations": [
                    {"type": "split_segment", "segment_id": "s2", "split_at": 1.5}
                ],
            }
        )
        split_result = apply_edit_proposal(project, split)
        split_segments = split_result.project["segments"][1:]
        self.assertEqual([item["word"] for item in split_segments[0]["words"]], ["次"])
        self.assertEqual([item["word"] for item in split_segments[1]["words"]], ["です"])

        merge = CodexEditProposal.from_json(
            {
                "summary": "単語を統合",
                "warnings": [],
                "operations": [
                    {"type": "merge_segments", "segment_ids": ["s1", "s2"]}
                ],
            }
        )
        merged_project = apply_edit_proposal(project, merge).project
        self.assertEqual(
            [item["word"] for item in merged_project["segments"][0]["words"]],
            ["最初", "次", "です"],
        )

    def test_base_revision_and_operation_requirements_are_strict(self) -> None:
        for revision in (True, 1.0, "1"):
            with self.subTest(revision=revision):
                with self.assertRaisesRegex(EditProposalError, "base_revision"):
                    CodexEditProposal.from_json(
                        {
                            "summary": "strict",
                            "base_revision": revision,
                            "warnings": [],
                            "operations": [{"type": "delete_segment", "segment_id": "s1"}],
                        }
                    )
        for operation in (
            {"type": "update_segment", "segment_id": "s1"},
            {"type": "delete_segment"},
            {"type": "add_segment"},
            {"type": "split_segment", "segment_id": "s1"},
            {"type": "merge_segments", "segment_ids": ["s1"]},
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(EditProposalError):
                    CodexEditProposal.from_json(
                        {"summary": "required", "warnings": [], "operations": [operation]}
                    )


if __name__ == "__main__":
    unittest.main()
