from __future__ import annotations

import unittest

from src.processing_progress import (
    ProcessingProgress,
    parse_ffmpeg_timestamp,
    parse_progress_events,
    progress_event_line,
)


class ProcessingProgressTests(unittest.TestCase):
    def test_event_protocol_is_path_free_and_round_trips(self) -> None:
        line = progress_event_line("transcribe", "alignment", phase="start")

        self.assertEqual(
            parse_progress_events(line),
            ({"job": "transcribe", "step": "alignment", "phase": "start", "progress": 0.0},),
        )
        self.assertNotIn("/", line)
        self.assertNotIn("\\", line)

        timed_line = progress_event_line("render_short", "encode", phase="metadata", duration=30.0)
        self.assertEqual(parse_progress_events(timed_line)[0]["duration"], 30.0)

    def test_ffmpeg_timestamp_parser_supports_duration_and_time(self) -> None:
        self.assertEqual(parse_ffmpeg_timestamp("Duration: 00:02:03.50, start: 0.0"), 123.5)
        self.assertEqual(parse_ffmpeg_timestamp("frame=10 time=00:00:04.25 speed=1x"), 4.25)
        self.assertIsNone(parse_ffmpeg_timestamp("frame=0 time=-577014:32:22.77"))

    def test_transcription_steps_are_weighted_and_monotonic(self) -> None:
        tracker = ProcessingProgress()
        tracker.start("transcribe")
        tracker.update({"job": "transcribe", "step": "alignment", "progress": 0.5})
        first_value = tracker.value
        tracker.update({"job": "transcribe", "step": "alignment", "progress": 0.1})

        self.assertEqual([step["label"] for step in tracker.as_list()], [
            "準備", "音声同期", "文字起こし", "字幕の統合・整形", "波形生成", "プロジェクト保存",
        ])
        self.assertGreaterEqual(tracker.value, first_value)
        self.assertEqual(tracker.as_list()[1]["state"], "running")

        tracker.update({"job": "transcribe", "step": "transcription", "phase": "start"})
        self.assertEqual(tracker.as_list()[1]["state"], "completed")
        self.assertEqual(tracker.as_list()[2]["state"], "running")

    def test_all_jobs_have_issue_steps_and_success_reaches_one_hundred(self) -> None:
        expected = {
            "transcribe": ["準備", "音声同期", "文字起こし", "字幕の統合・整形", "波形生成", "プロジェクト保存"],
            "render": ["準備", "字幕生成", "音声処理", "動画エンコード", "出力確定"],
            "render_short": ["準備", "クリップ構築", "トランジション・音声処理", "動画エンコード", "出力確定"],
        }
        for job, labels in expected.items():
            with self.subTest(job=job):
                tracker = ProcessingProgress()
                tracker.start(job)
                for step in tracker.as_list():
                    tracker.update({"job": job, "step": step["id"], "phase": "complete", "progress": 1.0})
                tracker.finish("completed")
                self.assertEqual([step["label"] for step in tracker.as_list()], labels)
                self.assertEqual(tracker.value, 1.0)
                self.assertTrue(all(step["state"] == "completed" for step in tracker.as_list()))

    def test_cancel_and_error_keep_progress_and_mark_current_step(self) -> None:
        for outcome, state in (("cancelled", "cancelled"), ("error", "error")):
            with self.subTest(outcome=outcome):
                tracker = ProcessingProgress()
                tracker.start("render_short")
                tracker.update({"job": "render_short", "step": "encode", "progress": 0.4})
                before = tracker.value
                tracker.finish(outcome)
                self.assertGreaterEqual(tracker.value, before)
                self.assertEqual(tracker.value, before)
                self.assertEqual(tracker.as_list()[3]["state"], state)

    def test_unknown_events_are_ignored(self) -> None:
        tracker = ProcessingProgress()
        tracker.start("render")

        self.assertFalse(tracker.update({"job": "other", "step": "encode"}))
        self.assertFalse(tracker.update({"job": "render", "step": "unknown"}))
        self.assertEqual(tracker.value, 0.0)

    def test_unneeded_steps_can_be_omitted_from_sequence(self) -> None:
        tracker = ProcessingProgress()
        tracker.start("render", skip_steps={"subtitle"})

        self.assertNotIn("subtitle", [step["id"] for step in tracker.as_list()])
        tracker.update({"job": "render", "step": "audio", "phase": "start"})
        self.assertEqual(tracker.as_list()[1]["displayStatus"], "実行中")
        for step in tracker.as_list():
            tracker.update({"job": "render", "step": step["id"], "phase": "complete", "progress": 1.0})
        self.assertLess(tracker.value, 1.0)
        tracker.finish("completed")
        self.assertEqual(tracker.value, 1.0)

    def test_final_machine_event_waits_for_process_outcome(self) -> None:
        for outcome, state in (("error", "error"), ("cancelled", "cancelled")):
            with self.subTest(outcome=outcome):
                tracker = ProcessingProgress()
                tracker.start("render")
                for step in ("prepare", "subtitle", "audio", "encode", "finalize"):
                    tracker.update(
                        {"job": "render", "step": step, "phase": "complete", "progress": 1.0}
                    )

                self.assertLess(tracker.value, 1.0)
                self.assertEqual(tracker.current_step, "finalize")
                self.assertEqual(tracker.as_list()[-1]["state"], "running")

                tracker.finish(outcome)
                self.assertLess(tracker.value, 1.0)
                self.assertEqual(tracker.as_list()[-1]["state"], state)


if __name__ == "__main__":
    unittest.main()
