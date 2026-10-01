from __future__ import annotations

import unittest

from src.subtitle_review import SubtitleReviewCancelled, SubtitleReviewQueue, generate_review_queue
from src.subtitle_review_rules import review_segment_rules
from tests.typed_case import TypedTestCase


class SubtitleReviewTests(TypedTestCase):
    def setUp(self) -> None:
        self.segments: list[dict[str, object]] = [
            {
                "id": "s1",
                "start": 0.0,
                "end": 0.2,
                "text": "これはとても長い字幕本文です。読む時間が足りない",
                "speaker": "A",
            },
            {"id": "s2", "start": 0.1, "end": 1.0, "text": "次", "speaker": "B", "confidence": 0.3},
        ]

    def test_rules_generate_reasons_without_confidence_field(self) -> None:
        issues = generate_review_queue(self.segments)
        self.assertTrue(issues)
        self.assertTrue(any(item.rule_id == "reading_speed" for item in issues))
        self.assertTrue(any(item.rule_id == "low_confidence" for item in issues))
        self.assertTrue(all(item.reasons and item.severity in {"high", "medium", "low"} for item in issues))

    def test_rules_keep_numeric_string_coercion_and_evidence(self) -> None:
        segment: dict[str, object] = {
            "start": "1.0",
            "end": "1.2",
            "text": "とても長い字幕本文",
            "confidence": "0.4",
            "max_width": "3",
        }
        findings = review_segment_rules(segment)
        by_rule = {finding.rule_id: finding for finding in findings}
        confidence_evidence: dict[str, object] = {"confidence": 0.4}
        width_evidence: dict[str, object] = {"max_width": 3}
        self.assertEqual(by_rule["low_confidence"].evidence, confidence_evidence)
        self.assertIn("reading_speed", by_rule)
        self.assertEqual(by_rule["line_width"].evidence, width_evidence)

    def test_queue_state_and_stale_detection_are_separate_from_generation(self) -> None:
        issue = next(
            item for item in generate_review_queue(self.segments, project_revision=1) if item.segment_ids == ("s1",)
        )
        queue = SubtitleReviewQueue([issue])
        reviewed = queue.update_status(issue.issue_id, "ignored")
        self.assertEqual(reviewed.created_at, issue.created_at)
        self.assertEqual(reviewed.evidence, issue.evidence)
        self.assertTrue(reviewed.reviewed_at)
        stale = queue.mark_stale([{**self.segments[0], "text": "修正後"}, self.segments[1]])
        self.assertEqual(stale[0].status, "stale")
        self.assertEqual(stale[0].reviewed_at, reviewed.reviewed_at)
        self.assertEqual(queue.issues[issue.issue_id].status, "stale")

    def test_reconcile_preserves_status_and_links_changed_content(self) -> None:
        original = generate_review_queue(self.segments, project_revision=1)
        issue = next(item for item in original if item.segment_ids == ("s1",))
        queue = SubtitleReviewQueue(original)
        queue.update_status(issue.issue_id, "ignored")

        same = generate_review_queue(self.segments, project_revision=2)
        queue.reconcile(same)
        self.assertEqual(queue.issues[issue.issue_id].status, "ignored")

        changed_segments = [
            {**self.segments[0], "text": str(self.segments[0]["text"]) + " 追記された修正内容"},
            self.segments[1],
        ]
        changed = generate_review_queue(changed_segments, project_revision=3)
        queue.reconcile(changed)
        replacement = next(
            item
            for item in queue.issues.values()
            if item.logical_key == issue.logical_key and item.issue_id != issue.issue_id
        )
        self.assertEqual(queue.issues[issue.issue_id].status, "stale")
        self.assertEqual(replacement.status, "open")
        self.assertEqual(replacement.supersedes, issue.issue_id)

    def test_filter_and_cancel(self) -> None:
        issues = generate_review_queue(self.segments)
        queue = SubtitleReviewQueue(issues)
        self.assertTrue(queue.filtered(severity="high"))
        with self.assertRaises(SubtitleReviewCancelled):
            generate_review_queue(self.segments, cancel_check=lambda: True)


if __name__ == "__main__":
    unittest.main()
