from __future__ import annotations

import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch

from src.subtitle_project import SubtitleProjectError, create_project, load_project, save_project
from src.video_timeline import (
    VideoTimeline,
    VideoTimelineError,
    intersect_ranges,
    timeline_from_project,
)


class VideoTimelineTests(unittest.TestCase):
    def test_add_cut_merges_overlaps_and_preserves_existing_id(self) -> None:
        timeline = VideoTimeline.from_json(None, source_duration=10.0)
        timeline = timeline.add_cut(2.0, 4.0, cut_id="cut-a")

        merged = timeline.add_cut(3.0, 5.0, cut_id="cut-b")

        self.assertEqual(
            [cut.to_json() for cut in merged.cuts],
            [{"id": "cut-a", "source_start": 2.0, "source_end": 5.0}],
        )
        self.assertEqual(merged.keep_ranges, [(0.0, 2.0), (5.0, 10.0)])
        self.assertEqual(merged.output_duration, 7.0)

    def test_add_overlapping_cut_without_explicit_id_keeps_existing_id(self) -> None:
        timeline = VideoTimeline.from_json(None, source_duration=10.0).add_cut(
            2.0,
            4.0,
            cut_id="cut-a",
        )

        merged = timeline.add_cut(3.0, 5.0)

        self.assertEqual([cut.id for cut in merged.cuts], ["cut-a"])

    def test_update_cut_keeps_stable_id_and_normalizes_overlap(self) -> None:
        timeline = VideoTimeline.from_json(
            {
                "cuts": [
                    {"id": "first", "source_start": 1.0, "source_end": 2.0},
                    {"id": "second", "source_start": 4.0, "source_end": 5.0},
                ]
            },
            source_duration=8.0,
        )

        updated = timeline.update_cut("second", 1.5, 4.5)

        self.assertEqual(len(updated.cuts), 1)
        self.assertEqual(updated.cuts[0].id, "second")
        self.assertEqual((updated.cuts[0].source_start, updated.cuts[0].source_end), (1.0, 4.5))

    def test_restore_range_can_split_a_cut_with_stable_left_id(self) -> None:
        timeline = VideoTimeline.from_json(
            {"cuts": [{"id": "whole", "source_start": 2.0, "source_end": 8.0}]},
            source_duration=10.0,
        )

        restored = timeline.restore_range(4.0, 6.0, id_factory=lambda: "right")

        self.assertEqual(
            [cut.to_json() for cut in restored.cuts],
            [
                {"id": "whole", "source_start": 2.0, "source_end": 4.0},
                {"id": "right", "source_start": 6.0, "source_end": 8.0},
            ],
        )

    def test_source_output_mapping_clamps_cut_positions_and_uses_next_clip_at_boundary(self) -> None:
        timeline = VideoTimeline.from_json(
            {
                "cuts": [
                    {"id": "first", "source_start": 1.0, "source_end": 3.0},
                    {"id": "second", "source_start": 6.0, "source_end": 7.0},
                ]
            },
            source_duration=10.0,
        )

        self.assertEqual(timeline.source_to_output_seconds(0.5), 0.5)
        self.assertEqual(timeline.source_to_output_seconds(2.0), 1.0)
        self.assertEqual(timeline.source_to_output_seconds(5.0), 3.0)
        self.assertEqual(timeline.source_to_output_seconds(10.0), 7.0)
        self.assertEqual(timeline.output_to_source_seconds(1.0), 3.0)
        self.assertEqual(timeline.output_to_source_seconds(4.0), 7.0)
        self.assertEqual(timeline.output_to_source_seconds(7.0), 10.0)
        self.assertEqual(timeline.source_to_output(5_000), 3_000)
        self.assertEqual(timeline.output_to_source(4_000), 7_000)

    def test_preview_skips_only_while_source_position_is_inside_a_cut(self) -> None:
        timeline = VideoTimeline.from_json(
            {"cuts": [{"id": "cut", "source_start": 2.0, "source_end": 4.0}]},
            source_duration=8.0,
        )

        self.assertEqual(timeline.next_playable_source_seconds(1.5), 1.5)
        self.assertEqual(timeline.next_playable_source_seconds(2.0), 4.0)
        self.assertEqual(timeline.next_playable_source_seconds(3.5), 4.0)
        self.assertEqual(timeline.next_playable_source_seconds(4.0), 4.0)

    def test_invalid_and_full_length_cuts_are_rejected(self) -> None:
        timeline = VideoTimeline.from_json(None, source_duration=5.0)

        with self.assertRaisesRegex(VideoTimelineError, "at least"):
            timeline.add_cut(2.0, 2.01)
        with self.assertRaisesRegex(VideoTimelineError, "entire video"):
            timeline.add_cut(0.0, 5.0)

    def test_json_round_trip_preserves_unknown_timeline_and_cut_fields(self) -> None:
        payload = {
            "schema_version": 1,
            "mode": "source",
            "cuts": [
                {
                    "id": "cut-a",
                    "source_start": 1.0,
                    "source_end": 2.0,
                    "note": "keep metadata",
                }
            ],
        }

        self.assertEqual(
            VideoTimeline.from_json(payload, source_duration=4.0).to_json(),
            payload,
        )

    def test_intersect_ranges_combines_manual_and_automatic_keep_ranges(self) -> None:
        self.assertEqual(
            intersect_ranges(
                [(0.0, 2.0), (4.0, 8.0)],
                [(1.0, 5.0), (6.0, 7.0)],
            ),
            [(1.0, 2.0), (4.0, 5.0), (6.0, 7.0)],
        )

    def test_project_video_duration_is_not_extended_by_out_of_range_subtitles(self) -> None:
        timeline = timeline_from_project(
            {
                "video": {"duration_seconds": 4.0},
                "segments": [{"start": 10.0, "end": 11.0, "text": "late"}],
                "timeline": {"cuts": [{"id": "cut", "source_start": 1.0, "source_end": 2.0}]},
            }
        )

        self.assertEqual(timeline.source_duration, 4.0)
        self.assertEqual(timeline.output_duration, 3.0)

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
