from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from src.highlight_visual_signals import (
    VisualSignal,
    VisualSignalSettings,
    blend_visual_scores,
    build_scene_change_command,
    extract_visual_signals,
    visual_signal_cache_key,
)


class HighlightVisualSignalTests(unittest.TestCase):
    def test_feature_flag_disabled_keeps_baseline_without_running_ffmpeg(self) -> None:
        called = []
        result = extract_visual_signals("video.mkv", [(0, 5)], runner=lambda *args, **kwargs: called.append(True))
        self.assertTrue(result.fallback)
        self.assertFalse(called)

    def test_command_uses_low_fps_scaled_frames_and_no_shell(self) -> None:
        command = build_scene_change_command(
            "C:/video.mkv",
            start=10,
            end=20,
            settings=VisualSignalSettings(enabled=True, sample_fps=0.5, scale_width=240),
        )
        self.assertIn("fps=0.5", " ".join(command))
        self.assertIn("scale=240", " ".join(command))
        self.assertEqual(command[-1], "-")

    def test_extract_parses_timestamp_and_reports_progress(self) -> None:
        progress = []

        def runner(command, **kwargs):
            self.assertFalse(kwargs["shell"])
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="",
                stderr="[Parsed_metadata_0] pts_time:2.000 lavfi.scene_score=0.8\n",
            )

        result = extract_visual_signals(
            "video.mkv",
            [(10, 20)],
            settings=VisualSignalSettings(enabled=True),
            runner=runner,
            progress_callback=progress.append,
        )
        self.assertFalse(result.fallback)
        self.assertEqual(result.signals[0], VisualSignal(12.0, 0.8))
        self.assertEqual(progress, [1.0])

    def test_cancel_and_global_budget_fallback_to_empty_signals(self) -> None:
        cancelled = extract_visual_signals(
            "video.mkv",
            [(0, 2)],
            settings=VisualSignalSettings(enabled=True),
            runner=lambda *args, **kwargs: subprocess.CompletedProcess(
                args[0], 0, stdout="", stderr="pts_time:1.0 lavfi.scene_score=0.9"
            ),
            cancel_check=lambda: True,
        )
        self.assertTrue(cancelled.fallback)
        self.assertEqual(cancelled.signals, ())

        exhausted = extract_visual_signals(
            "video.mkv",
            [(0, 2)],
            settings=VisualSignalSettings(enabled=True, max_runtime_seconds=0.0),
            runner=lambda *args, **kwargs: None,
        )
        self.assertTrue(exhausted.fallback)
        self.assertEqual(exhausted.signals, ())

        timeouts = []

        def budget_runner(command, **kwargs):
            timeouts.append(kwargs["timeout"])
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        budgeted = extract_visual_signals(
            "video.mkv",
            [(0, 2), (2, 4)],
            settings=VisualSignalSettings(enabled=True, timeout_seconds=20.0, max_runtime_seconds=1.0),
            runner=budget_runner,
        )
        self.assertLessEqual(timeouts[0], 1.0)
        self.assertFalse(budgeted.fallback)  # both fake windows completed within the budget

    def test_failure_cancels_to_baseline_and_visual_weight_is_bounded(self) -> None:
        result = extract_visual_signals(
            "video.mkv",
            [(0, 2)],
            settings=VisualSignalSettings(enabled=True),
            runner=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("ffmpeg unavailable")),
        )
        self.assertTrue(result.fallback)
        candidates = [{"id": "h1", "start": 0, "end": 5, "score": 0.2}]
        blended = blend_visual_scores(candidates, [VisualSignal(2, 1.0)], settings=VisualSignalSettings(enabled=True, weight=2.0))
        self.assertLessEqual(blended[0]["score"], 1.0)
        self.assertEqual(blended[0]["visual_score"], 1.0)

    def test_cache_key_includes_video_fingerprint_and_signal_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "video.mkv"
            video.write_bytes(b"a")
            first = visual_signal_cache_key(video, [(0, 1)], VisualSignalSettings(enabled=True))
            video.write_bytes(b"changed")
            second = visual_signal_cache_key(video, [(0, 1)], VisualSignalSettings(enabled=True))
            self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
