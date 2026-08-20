from __future__ import annotations

import unittest

from src.subtitle_review import SubtitleReviewCancelled, SubtitleReviewQueue, generate_review_queue


class SubtitleReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.segments = [
            {"id": "s1", "start": 0.0, "end": 0.2, "text": "これはとても長い字幕本文です。読む時間が足りない", "speaker": "A"},
            {"id": "s2", "start": 0.1, "end": 1.0, "text": "次", "speaker": "B", "confidence": 0.3},
        ]

    def test_rules_generate_reasons_without_confidence_field(self) -> None:
        issues = generate_review_queue(self.segments)
        self.assertTrue(issues)
        self.assertTrue(any(item.rule_id == "reading_speed" for item in issues))
        self.assertTrue(any(item.rule_id == "low_confidence" for item in issues))
        self.assertTrue(all(item.reasons and item.severity in {"high", "medium", "low"} for item in issues))

    def test_queue_state_and_stale_detection_are_separate_from_generation(self) -> None:
        issue = next(
            item
            for item in generate_review_queue(self.segments, project_revision=1)
            if item.segment_ids == ("s1",)
        )
        queue = SubtitleReviewQueue([issue])
        queue.update_status(issue.issue_id, "ignored")
        stale = queue.mark_stale([{**self.segments[0], "text": "修正後"}, self.segments[1]])
        self.assertEqual(stale[0].status, "stale")
        self.assertEqual(queue.issues[issue.issue_id].status, "stale")

    def test_filter_and_cancel(self) -> None:
        issues = generate_review_queue(self.segments)
        queue = SubtitleReviewQueue(issues)
        self.assertTrue(queue.filtered(severity="high"))
        with self.assertRaises(SubtitleReviewCancelled):
            generate_review_queue(self.segments, cancel_check=lambda: True)


if __name__ == "__main__":
    unittest.main()
