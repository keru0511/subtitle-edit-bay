from __future__ import annotations

import copy
import tempfile
import unittest

from src.subtitle_bulk_edit import (
    BulkEditAction,
    BulkEditError,
    BulkEditQuery,
    apply_bulk_edit,
    find_matching_segment_ids,
    preview_bulk_edit,
)
from src.subtitle_project import create_project


class SubtitleBulkEditTests(unittest.TestCase):
    def _project(self) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as temp_dir:
            return create_project(
                video_path="C:/input.mp4",
                output_dir=temp_dir,
                segments=[
                    {"id": "s1", "start": 0.0, "end": 1.0, "text": "Alpha", "speaker": "A"},
                    {"id": "s2", "start": 1.1, "end": 2.0, "text": "Beta", "speaker": "B"},
                ],
                duration_seconds=2.0,
            )

    def test_query_supports_regex_speaker_time_and_stable_ids(self) -> None:
        project = self._project()
        self.assertEqual(find_matching_segment_ids(project, BulkEditQuery(text="^alp", regex=True)), ["s1"])
        self.assertEqual(find_matching_segment_ids(project, BulkEditQuery(speaker="B", start=1.0, end=2.0)), ["s2"])
        self.assertEqual(find_matching_segment_ids(project, BulkEditQuery(segment_ids=frozenset({"s1"}))), ["s1"])
        self.assertEqual(find_matching_segment_ids(project, BulkEditQuery(start=1.0)), ["s2"])
        self.assertEqual(find_matching_segment_ids(project, BulkEditQuery(end=1.0)), ["s1"])
        self.assertEqual(find_matching_segment_ids(project, BulkEditQuery(end=0.0)), [])

    def test_preview_and_atomic_apply_support_replace_rename_style_and_shift(self) -> None:
        project = self._project()
        original = copy.deepcopy(project)
        query = BulkEditQuery(text="Alpha")
        action = BulkEditAction(
            text_replace_from="Alpha",
            text_replace_to="ALPHA",
            speaker_rename={"A": "Player"},
            style={"subtitle_font_scale": 1.2},
            time_shift=0.1,
        )
        preview = preview_bulk_edit(project, query, action)
        self.assertEqual(preview.segment_ids, ("s1",))
        result = apply_bulk_edit(project, query, action)
        self.assertEqual(project, original)
        updated = result.project["segments"][0]
        self.assertEqual(updated["text"], "ALPHA")
        self.assertEqual(updated["speaker"], "Player")
        self.assertEqual(updated["start"], 0.1)
        self.assertTrue(updated["manual_timing"])

    def test_replacement_uses_the_query_literal_and_case_semantics(self) -> None:
        project = self._project()
        result = apply_bulk_edit(
            project,
            BulkEditQuery(text="alpha", case_sensitive=False),
            BulkEditAction(text_replace_from="alpha", text_replace_to="ALPHA"),
        )
        self.assertEqual(result.project["segments"][0]["text"], "ALPHA")

        regex_result = apply_bulk_edit(
            project,
            BulkEditQuery(text=r"a(l)pha", regex=True, case_sensitive=False),
            BulkEditAction(text_replace_from=r"a(l)pha", text_replace_to=r"A\1PHA"),
        )
        self.assertEqual(regex_result.project["segments"][0]["text"], "ALPHA")

    def test_invalid_regex_and_cancel_do_not_change_project(self) -> None:
        project = self._project()
        with self.assertRaises(BulkEditError):
            preview_bulk_edit(project, BulkEditQuery(text="[", regex=True), BulkEditAction(text_replace_from="["))
        with self.assertRaises(BulkEditError):
            apply_bulk_edit(project, BulkEditQuery(text="Alpha"), BulkEditAction(text_replace_from="Alpha", text_replace_to="X"), cancel_check=lambda: True)


if __name__ == "__main__":
    unittest.main()
