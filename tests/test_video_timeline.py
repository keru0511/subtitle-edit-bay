from __future__ import annotations

import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch

from src.subtitle_project import SubtitleProjectError, create_project, load_project, save_project
from src.video_timeline import (
    VideoTimelineError,
    timeline_from_project,
)


class VideoTimelineTests(unittest.TestCase):
    def test_unknown_media_duration_does_not_use_last_subtitle_as_video_end(self) -> None:
        project = create_project(
            video_path="source.mp4",
            output_dir=".",
            segments=[{"start": 0.0, "end": 2.0, "text": "early caption"}],
        )
        timeline = timeline_from_project(project)

        self.assertEqual(timeline.source_duration, 0.0)
        with self.assertRaisesRegex(VideoTimelineError, "duration is required"):
            timeline.add_cut(0.5, 1.0)

    def test_load_resolves_missing_duration_before_validating_existing_cuts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "source.mp4"
            video.touch()
            project = create_project(
                video_path=video,
                output_dir=root,
                segments=[{"start": 0.0, "end": 2.0, "text": "early caption"}],
            )
            # Also recover files written by the old subtitle-duration fallback.
            project["timeline"] = {"cuts": [{"id": "cut", "source_start": 0.5, "source_end": 1.0}]}
            path = root / "legacy.json"
            path.write_text(json.dumps(project), encoding="utf-8")
            with patch("src.subtitle_project.probe_media_duration", return_value=5.0) as probe:
                loaded = load_project(path, resolve_video_duration=True)
            probe.assert_called_once_with(str(video))
            self.assertEqual(timeline_from_project(loaded).keep_ranges, [(0.0, 0.5), (1.0, 5.0)])
            self.assertEqual(loaded["segments"], project["segments"])

            with patch("src.subtitle_project.probe_media_duration", side_effect=OSError("unavailable")):
                with self.assertRaisesRegex(SubtitleProjectError, "duration is required"):
                    load_project(path, resolve_video_duration=True)

    def test_project_round_trip_persists_timeline_and_migrates_legacy_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            video.write_bytes(b"video")
            project = create_project(
                video_path=video,
                output_dir=root,
                segments=[],
                duration_seconds=10.0,
                timeline={"cuts": [{"id": "saved-cut", "source_start": 2.0, "source_end": 4.0}]},
            )
            path = save_project(root / "project.json", project)

            loaded = load_project(path)

            self.assertEqual(loaded["timeline"]["cuts"][0]["id"], "saved-cut")
            legacy = dict(loaded)
            legacy.pop("timeline")
            legacy_path = root / "legacy.json"
            legacy_path.write_text("{}", encoding="utf-8")
            save_project(legacy_path, legacy)
            self.assertEqual(load_project(legacy_path)["timeline"]["cuts"], [])


if __name__ == "__main__":
    unittest.main()
