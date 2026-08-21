from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.processing_queue import ProcessingQueue, ProcessingQueueError


class ProcessingQueueTests(unittest.TestCase):
    def test_persistence_resume_stale_and_secret_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "input.mkv"
            source.write_bytes(b"input")
            queue = ProcessingQueue(Path(temp_dir) / "queue.json")
            item = queue.add(
                source,
                settings={
                    "language": "ja",
                    "codex": {
                        "api_token": "hidden",
                        "headers": [{"authorization": "Bearer hidden"}],
                    },
                },
            )
            queue.get(item.item_id).status = "running"
            queue.save()
            restored = ProcessingQueue(queue.path)
            self.assertEqual(restored.mark_interrupted_on_startup()[0].status, "interrupted")
            self.assertNotIn("hidden", queue.path.read_text(encoding="utf-8"))
            self.assertIn("[REDACTED]", queue.path.read_text(encoding="utf-8"))
            self.assertEqual(
                restored.items[0].settings["codex"]["api_token"],
                "[REDACTED]",
            )
            source.write_bytes(b"changed")
            self.assertTrue(restored.mark_stale())

    def test_success_skips_completed_stage_and_validates_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "input.mkv"
            source.write_bytes(b"input")
            output = Path(temp_dir) / "result.mp4"
            queue = ProcessingQueue(Path(temp_dir) / "queue.json")
            item = queue.add(source, stages=("transcribe", "render"))
            calls: list[str] = []

            def runner(item, stage, progress, cancel):
                calls.append(stage.name)
                progress(0.5)
                if stage.name == "render":
                    output.write_bytes(b"output")
                    return output
                return None

            completed = queue.run_item(item.item_id, runner, output_validator=lambda path: path.stat().st_size > 0, allow_overwrite=True)
            self.assertEqual(completed.status, "success")
            self.assertEqual(calls, ["transcribe", "render"])
            completed.stages[0].status = "success"
            completed.stages[1].status = "pending"
            queue.save()
            calls.clear()
            queue.run_item(item.item_id, runner, output_validator=lambda path: True, allow_overwrite=True)
            self.assertEqual(calls, ["render"])

    def test_success_stage_is_reprocessed_when_output_is_missing_or_changed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "input.mkv"
            source.write_bytes(b"input")
            output = Path(temp_dir) / "result.mp4"
            queue = ProcessingQueue(Path(temp_dir) / "queue.json")
            item = queue.add(source, stages=("render",))
            calls: list[str] = []

            def runner(item, stage, progress, cancel):
                calls.append(stage.name)
                output.write_bytes(f"output-{len(calls)}".encode())
                return output

            queue.run_item(item.item_id, runner, output_validator=lambda path: path.stat().st_size > 0, allow_overwrite=True)
            output.unlink()
            queue.run_item(item.item_id, runner, output_validator=lambda path: path.stat().st_size > 0, allow_overwrite=True)
            output.write_bytes(b"external-change")
            queue.run_item(item.item_id, runner, output_validator=lambda path: path.stat().st_size > 0, allow_overwrite=True)
            self.assertEqual(calls, ["render", "render", "render"])

    def test_concurrency_is_capped_until_cancellation_is_per_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue = ProcessingQueue(Path(temp_dir) / "queue.json", max_concurrency=4)
            self.assertEqual(queue.max_concurrency, 1)

    def test_cancel_failure_and_existing_output_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "input.mkv"
            source.write_bytes(b"input")
            output = Path(temp_dir) / "result.mp4"
            output.write_bytes(b"old")
            queue = ProcessingQueue(Path(temp_dir) / "queue.json")
            item = queue.add(source, stages=("render",))
            item.stages[0].output_path = str(output)

            def existing(item, stage, progress, cancel):
                return output

            failed = queue.run_item(item.item_id, existing)
            self.assertEqual(failed.status, "failed")
            queue.get(item.item_id).status = "pending"

            def canceled(item, stage, progress, cancel):
                raise ProcessingQueueError("canceled")

            stopped = queue.run_item(item.item_id, canceled, allow_overwrite=True)
            self.assertEqual(stopped.status, "canceled")


if __name__ == "__main__":
    unittest.main()
