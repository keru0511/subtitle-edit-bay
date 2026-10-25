"""Contract tests for read-only sequence-clip subtitle filtering."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.subtitle_clip_filter import is_primary_video_asset, overlaps_source_window


class SubtitleClipFilterTests(unittest.TestCase):
    def test_filters_source_time_without_mapping_to_sequence_output(self) -> None:
        self.assertTrue(overlaps_source_window(8.0, 12.0, 10.0, 16.0))
        self.assertTrue(overlaps_source_window(14.0, 20.0, 10.0, 16.0))
        self.assertFalse(overlaps_source_window(0.0, 10.0, 10.0, 16.0))
        self.assertFalse(overlaps_source_window(16.0, 19.0, 10.0, 16.0))

    def test_primary_video_asset_is_supported_and_other_assets_are_not(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            primary = str(Path(folder) / "main.mp4")
            other = str(Path(folder) / "secondary.mp4")
            project = {"video": {"path": primary}}
            self.assertTrue(is_primary_video_asset(project, primary))
            self.assertFalse(is_primary_video_asset(project, other))

    def test_missing_project_or_video_never_claims_transcript(self) -> None:
        self.assertFalse(is_primary_video_asset(None, "/media/movie.mp4"))
        self.assertFalse(is_primary_video_asset({}, "/media/movie.mp4"))
        self.assertFalse(is_primary_video_asset({"video": {}}, "/media/movie.mp4"))
        self.assertFalse(is_primary_video_asset({"video": {"path": "/media/movie.mp4"}}, ""))


if __name__ == "__main__":
    unittest.main()
