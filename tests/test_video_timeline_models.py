from __future__ import annotations

import unittest

from src.video_timeline import (
    VideoTimeline,
    VideoTimelineError,
    VideoTimelineView,
    intersect_ranges,
    timeline_from_project,
)
from tests.typed_case import TypedTestCase


class VideoTimelineModelTests(TypedTestCase):
    def test_add_cut_merges_overlaps_and_preserves_existing_id(self) -> None:
        timeline = VideoTimeline.from_json(None, source_duration=10.0)
        timeline = timeline.add_cut(2.0, 4.0, cut_id="cut-a")

        merged = timeline.add_cut(3.0, 5.0, cut_id="cut-b")

        expected_cuts: list[dict[object, object]] = [{"id": "cut-a", "source_start": 2.0, "source_end": 5.0}]
        actual_cuts = [cut.to_json() for cut in merged.cuts]
        self.assertEqual(
            actual_cuts,
            expected_cuts,
        )
        expected_ranges: list[tuple[float, float]] = [(0.0, 2.0), (5.0, 10.0)]
        self.assertEqual(merged.keep_ranges, expected_ranges)
        self.assertEqual(merged.output_duration, 7.0)

    def test_add_overlapping_cut_without_explicit_id_keeps_existing_id(self) -> None:
        timeline = VideoTimeline.from_json(None, source_duration=10.0).add_cut(
            2.0,
            4.0,
            cut_id="cut-a",
        )

        merged = timeline.add_cut(3.0, 5.0)

        expected_ids: list[str] = ["cut-a"]
        actual_ids = [cut.id for cut in merged.cuts]
        self.assertEqual(actual_ids, expected_ids)

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

        expected_cuts: list[dict[object, object]] = [
            {"id": "whole", "source_start": 2.0, "source_end": 4.0},
            {"id": "right", "source_start": 6.0, "source_end": 8.0},
        ]
        actual_cuts = [cut.to_json() for cut in restored.cuts]
        self.assertEqual(
            actual_cuts,
            expected_cuts,
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
        expected_ranges: list[tuple[float, float]] = [(1.0, 2.0), (4.0, 5.0), (6.0, 7.0)]
        self.assertEqual(
            intersect_ranges(
                [(0.0, 2.0), (4.0, 8.0)],
                [(1.0, 5.0), (6.0, 7.0)],
            ),
            expected_ranges,
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

    def test_view_has_typed_ranges_and_omits_extension_data(self) -> None:
        timeline = VideoTimeline.from_json(
            {"custom": "private", "cuts": [{"id": "cut", "source_start": "1", "source_end": "2", "note": "private"}]},
            source_duration=4.0,
        )
        expected: VideoTimelineView = {
            "schemaVersion": 1,
            "sourceDuration": 4.0,
            "outputDuration": 3.0,
            "removedDuration": 1.0,
            "hasCuts": True,
            "cuts": [{"id": "cut", "source_start": 1.0, "source_end": 2.0, "duration": 1.0}],
            "keepRanges": [
                {"source_start": 0.0, "source_end": 1.0, "output_start": 0.0, "output_end": 1.0},
                {"source_start": 2.0, "source_end": 4.0, "output_start": 1.0, "output_end": 3.0},
            ],
        }
        self.assertEqual(timeline.as_view(), expected)

    def test_extensions_are_copied_during_load_save_and_edits(self) -> None:
        notes: list[object] = ["original"]
        payload: dict[object, object] = {
            "custom": notes,
            "cuts": [{"id": "cut", "source_start": 1, "source_end": 2, "note": notes}],
            42: "legacy key",
        }
        timeline = VideoTimeline.from_json(payload, source_duration=4.0)
        notes.append("changed")
        edited = timeline.update_cut("cut", 1.0, 2.5)
        saved = edited.to_json()
        expected: list[object] = ["original"]
        self.assertEqual(saved["custom"], expected)
        self.assertEqual(edited.cuts[0].extras["note"], expected)
        self.assertEqual(saved[42], "legacy key")
        edited.cuts[0].extras["note"] = "changed again"
        self.assertEqual(timeline.cuts[0].extras["note"], expected)

    def test_numeric_validation_preserves_schema_conversion_and_rejects_nonfinite_times(self) -> None:
        for version in ("1", 1.9, True):
            with self.subTest(version=version):
                timeline = VideoTimeline.from_json({"schema_version": version}, source_duration=4.0)
                self.assertEqual(timeline.schema_version, 1)
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(VideoTimelineError, "finite"):
                    VideoTimeline.from_json(None, source_duration=value)
        invalid_versions: tuple[object, ...] = (None, [], "bad")
        for invalid_version in invalid_versions:
            with self.subTest(version=invalid_version):
                with self.assertRaisesRegex(VideoTimelineError, "must be an integer"):
                    VideoTimeline.from_json({"schema_version": invalid_version}, source_duration=4.0)
        timeline = VideoTimeline.from_json(None, source_duration=4.0)
        with self.assertRaisesRegex(VideoTimelineError, "must be a number"):
            timeline.add_cut([], 2)


if __name__ == "__main__":
    unittest.main()
