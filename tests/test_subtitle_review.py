from __future__ import annotations

import copy
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

    def test_reconcile_preserves_status_and_links_changed_content(self) -> None:
        original = generate_review_queue(self.segments, project_revision=1)
        issue = next(item for item in original if item.segment_ids == ("s1",))
        queue = SubtitleReviewQueue(original)
        queue.update_status(issue.issue_id, "ignored")

        same = generate_review_queue(self.segments, project_revision=2)
        queue.reconcile(same)
        self.assertEqual(queue.issues[issue.issue_id].status, "ignored")

        changed_segments = [
            {**self.segments[0], "text": self.segments[0]["text"] + " 追記された修正内容"},
            self.segments[1],
        ]
        changed = generate_review_queue(changed_segments, project_revision=3)
        queue.reconcile(changed)
        replacement = next(
            item for item in queue.issues.values()
            if item.logical_key == issue.logical_key and item.issue_id != issue.issue_id
        )
        self.assertEqual(queue.issues[issue.issue_id].status, "stale")
        self.assertEqual(replacement.status, "open")
        self.assertEqual(replacement.supersedes, issue.issue_id)

    def test_repeated_character_is_reviewed_without_changing_subtitles(self) -> None:
        segments = [{"id": "repeat", "start": 2.0, "end": 4.0, "text": "いいい\nいい", "speaker": "A"}]
        original = copy.deepcopy(segments)
        issues = [item for item in generate_review_queue(segments) if item.rule_id == "repeated_character"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].evidence, {"character": "い", "count": 5})
        self.assertEqual(issues[0].segment_ids, ("repeat",))
        self.assertEqual(segments, original)

    def test_normal_text_numbers_and_punctuation_do_not_trigger_repetition(self) -> None:
        for text in ["いいですね", "ああああ", "1000000", "!!!!!", "……", "すごーーーーーい", "々々々々々"]:
            with self.subTest(text=text):
                issues = generate_review_queue([{"id": "normal", "start": 0, "end": 5, "text": text}])
                self.assertFalse(any(item.rule_id == "repeated_character" for item in issues))

    def test_real_shouts_can_be_marked_false_positive_and_keep_that_decision(self) -> None:
        segments = [{"id": "shout", "start": 0, "end": 3, "text": "あああああ！"}]
        issue = next(item for item in generate_review_queue(segments) if item.rule_id == "repeated_character")
        queue = SubtitleReviewQueue([issue])
        queue.update_status(issue.issue_id, "false_positive")
        queue.reconcile(generate_review_queue(segments))
        self.assertEqual(queue.issues[issue.issue_id].status, "false_positive")

    def test_filter_and_cancel(self) -> None:
        issues = generate_review_queue(self.segments)
        queue = SubtitleReviewQueue(issues)
        self.assertTrue(queue.filtered(severity="high"))
        with self.assertRaises(SubtitleReviewCancelled):
            generate_review_queue(self.segments, cancel_check=lambda: True)


if __name__ == "__main__":
    unittest.main()
