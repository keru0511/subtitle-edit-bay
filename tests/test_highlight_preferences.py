from __future__ import annotations

import tempfile
import unittest
from unittest import mock

from src.highlight_feedback import HighlightFeedbackStore
from src.highlight_preferences import HighlightPreferenceModel, PreferenceSettings


class HighlightPreferenceTests(unittest.TestCase):
    def test_feedback_store_excludes_text_and_supports_export_reset_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HighlightFeedbackStore(f"{temp_dir}/feedback.json")
            store.record("h1", "accepted", {"text": 0.8, "intensity": 0.9, "path": "C:/secret/video.mkv"})
            payload = store.export()
            self.assertNotIn("video.mkv", str(payload))
            self.assertNotIn("path", str(payload))
            store.reset()
            self.assertEqual(store.events, [])
            store.record("h2", "rejected", {"intensity": 0.2})
            store.delete()
            self.assertFalse(store.path.exists())

    def test_text_feature_accepts_only_numeric_signal_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HighlightFeedbackStore(f"{temp_dir}/feedback.json")
            store.record("h1", "accepted", {"text": "実際の字幕本文", "intensity": 0.8})

            features = store.events[0].features
            self.assertNotIn("text", features)
            self.assertEqual(features["intensity"], 0.8)
            self.assertNotIn("実際の字幕本文", store.path.read_text(encoding="utf-8"))

    def test_record_does_not_commit_memory_when_atomic_save_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HighlightFeedbackStore(f"{temp_dir}/feedback.json")
            with mock.patch("src.highlight_feedback.os.replace", side_effect=OSError("injected")):
                with self.assertRaises(OSError):
                    store.record("h1", "accepted", {"intensity": 0.8})

            self.assertEqual(store.events, [])
            self.assertFalse(store.path.exists())

    def test_baseline_is_unchanged_until_minimum_history(self) -> None:
        settings = PreferenceSettings(minimum_events=3)
        model = HighlightPreferenceModel([], settings=settings)
        ranked = model.rank([{"id": "h1", "score": 0.4, "intensity": 1.0}])
        self.assertEqual(ranked[0]["score"], 0.4)
        self.assertIn("baseline", ranked[0]["preference_explanation"])

    def test_synthetic_feedback_is_bounded_explainable_and_toggleable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HighlightFeedbackStore(f"{temp_dir}/feedback.json")
            for index in range(4):
                store.record(f"accept-{index}", "accepted", {"intensity": 1.0, "text": 0.8})
            for index in range(2):
                store.record(f"reject-{index}", "rejected", {"intensity": 0.0, "text": 0.0})
            model = HighlightPreferenceModel(store.events, settings=PreferenceSettings(max_weight_delta=0.1))
            ranked = model.rank([{"id": "h1", "score": 0.4, "intensity": 1.0, "text": 0.8}])
            self.assertLessEqual(abs(ranked[0]["preference_adjustment"]), 0.1)
            self.assertTrue(ranked[0]["preference_explanation"])
            model.set_enabled(False)
            self.assertEqual(model.rank([{"id": "h1", "score": 0.4, "intensity": 1.0}])[0]["score"], 0.4)


if __name__ == "__main__":
    unittest.main()
