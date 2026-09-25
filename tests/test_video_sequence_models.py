from __future__ import annotations

import unittest
from collections import UserDict

from src.data_boundary import is_object_sequence

from src.video_sequence import (
    SequenceAsset,
    SequenceClip,
    SequencePositionPayload,
    SequenceTransition,
    VideoSequence,
    VideoSequenceError,
)


class VideoSequenceModelTests(unittest.TestCase):
    def _sequence(self) -> VideoSequence:
        return VideoSequence.from_json(
            {
                "schema_version": 1,
                "assets": [
                    {"id": "asset-a", "path": "a.mp4", "duration_seconds": 10.0},
                    {"id": "asset-b", "path": "b.mp4", "duration_seconds": 8.0},
                ],
                "clips": [
                    {
                        "id": "clip-a",
                        "asset_id": "asset-a",
                        "source_start": 1.0,
                        "source_end": 5.0,
                    },
                ],
            }
        )

    def test_legacy_single_video_maps_to_stable_sequence_identity(self) -> None:
        sequence = VideoSequence.from_json(
            None,
            legacy_video={"path": "capture.mp4", "duration_seconds": 12.0},
        )

        asset_ids = [asset.id for asset in sequence.assets]
        expected_asset_ids = ["asset-video"]
        self.assertEqual(asset_ids, expected_asset_ids)
        clip_ids = [clip.id for clip in sequence.clips]
        expected_clip_ids = ["clip-video"]
        self.assertEqual(clip_ids, expected_clip_ids)
        self.assertEqual(sequence.clips[0].asset_id, "asset-video")
        self.assertEqual(sequence.output_duration, 12.0)

    def test_add_trim_reorder_and_audio_mutations_preserve_clip_ids(self) -> None:
        sequence = self._sequence()
        sequence = sequence.add_clip(
            "asset-b",
            0.5,
            4.5,
            clip_id="clip-b",
            audio_linked=False,
            volume=1.25,
            audio_offset_seconds=-0.1,
            muted=True,
        )
        sequence = sequence.set_transition("clip-b", "crossfade", 0.5)
        sequence = sequence.trim_clip("clip-b", 1.0, 4.0)
        sequence = sequence.reorder_clip("clip-b", 0)

        clip_ids = [clip.id for clip in sequence.clips]
        expected_clip_ids = ["clip-b", "clip-a"]
        self.assertEqual(clip_ids, expected_clip_ids)
        clip = sequence.clips[0]
        self.assertEqual((clip.source_start, clip.source_end), (1.0, 4.0))
        self.assertFalse(clip.audio_linked)
        self.assertEqual(clip.volume, 1.25)
        self.assertEqual(clip.audio_offset_seconds, -0.1)
        self.assertTrue(clip.muted)
        self.assertEqual(clip.transition.type, "cut")

    def test_crossfade_mapping_is_deterministic_at_overlap_boundary(self) -> None:
        sequence = self._sequence().add_clip(
            "asset-b",
            2.0,
            6.0,
            clip_id="clip-b",
            transition=SequenceTransition(type="crossfade", duration=1.0),
        )

        timeline = sequence.timeline
        positions = [(entry.output_start, entry.output_end, entry.overlap) for entry in timeline.clips]
        expected_positions = [(0.0, 4.0, 0.0), (3.0, 7.0, 1.0)]
        self.assertEqual(positions, expected_positions)
        self.assertEqual(timeline.total_duration, 7.0)
        outgoing: SequencePositionPayload = {
            "clip_id": "clip-a",
            "source_time": 3.5,
        }
        self.assertEqual(timeline.output_to_source_seconds(2.5).to_json(), outgoing)
        incoming: SequencePositionPayload = {
            "clip_id": "clip-b",
            "source_time": 2.0,
        }
        self.assertEqual(timeline.output_to_source_seconds(3.0).to_json(), incoming)
        self.assertEqual(timeline.source_to_output_seconds("clip-b", 3.5), 4.5)

    def test_round_trip_preserves_unknown_sequence_fields_and_clip_state(self) -> None:
        payload = {
            "schema_version": 1,
            "future_sequence_field": {"keep": True},
            "assets": [
                {"id": "asset-a", "path": "a.mp4", "duration_seconds": 5.0, "asset_note": "keep"},
            ],
            "clips": [
                {
                    "id": "clip-a",
                    "asset_id": "asset-a",
                    "source_start": 0.0,
                    "source_end": 2.0,
                    "transition": {"type": "cut", "duration": 0.0, "transition_note": "keep"},
                    "audio_linked": False,
                    "volume": 0.75,
                    "audio_offset_seconds": 0.2,
                    "muted": True,
                    "clip_note": "keep",
                },
            ],
        }

        restored = VideoSequence.from_json(payload).to_json()

        self.assertEqual(restored, payload)

    def test_invalid_references_ranges_transitions_and_full_removal_fail_closed(self) -> None:
        with self.assertRaisesRegex(VideoSequenceError, "unknown asset"):
            self._sequence().add_clip("missing", 0.0, 1.0)
        with self.assertRaisesRegex(VideoSequenceError, "clip ids must be unique"):
            VideoSequence.from_json(
                {
                    "assets": [{"id": "asset", "path": "a.mp4", "duration_seconds": 5}],
                    "clips": [
                        {"id": "same", "asset_id": "asset", "source_start": 0, "source_end": 1},
                        {"id": "same", "asset_id": "asset", "source_start": 1, "source_end": 2},
                    ],
                }
            )
        with self.assertRaisesRegex(VideoSequenceError, "exceeds the asset duration"):
            self._sequence().add_clip("asset-a", 0.0, 11.0)
        with self.assertRaisesRegex(VideoSequenceError, "first clip"):
            VideoSequence.from_json(
                {
                    "assets": [{"id": "asset", "path": "a.mp4", "duration_seconds": 5}],
                    "clips": [
                        {
                            "id": "clip",
                            "asset_id": "asset",
                            "source_start": 0,
                            "source_end": 1,
                            "transition": {"type": "fade", "duration": 0.2},
                        }
                    ],
                }
            )
        with self.assertRaisesRegex(VideoSequenceError, "exceeds adjacent"):
            self._sequence().add_clip(
                "asset-b",
                0.0,
                0.1,
                clip_id="short-clip",
                transition=SequenceTransition(type="crossfade", duration=0.2),
            )

        with self.assertRaisesRegex(VideoSequenceError, "cannot remove all"):
            self._sequence().remove_asset("asset-a", remove_clips=True)
        with self.assertRaisesRegex(VideoSequenceError, "last sequence clip"):
            self._sequence().remove_clip("clip-a")

    def test_untrusted_shapes_are_rejected_at_the_boundary(self) -> None:
        for value in ([], "invalid", 1, object()):
            with self.subTest(value=value):
                with self.assertRaisesRegex(VideoSequenceError, "must be an object"):
                    VideoSequence.from_json(value)
        for field in ("assets", "clips"):
            for value in (None, {}, "invalid", ()):
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(VideoSequenceError, "must be an array"):
                        VideoSequence.from_json({field: value})
            with self.assertRaisesRegex(VideoSequenceError, "must be an object"):
                VideoSequence.from_json({field: [None]})
        with self.assertRaisesRegex(VideoSequenceError, "clip.transition must be an object"):
            SequenceTransition.from_json([])

    def test_aliases_numeric_strings_and_mapping_inputs_are_normalized(self) -> None:
        asset = SequenceAsset.from_json(UserDict({"id": 1, "file": " a.mp4 ", "duration": "5.1254"}))
        self.assertEqual((asset.id, asset.path, asset.duration_seconds), ("1", "a.mp4", 5.125))
        clip = SequenceClip.from_json(
            {
                "id": " c ",
                "source_asset_id": 1,
                "start": "0.5",
                "end": "2.25",
                "audioLinked": 0,
                "audio_volume": "1.25",
                "offset_seconds": "-0.1254",
                "audio_muted": 1,
                "transition": {"type": " CUT ", "duration_seconds": "0"},
            }
        )
        self.assertEqual((clip.id, clip.asset_id), ("c", "1"))
        self.assertEqual((clip.source_start, clip.source_end), (0.5, 2.25))
        self.assertFalse(clip.audio_linked)
        self.assertTrue(clip.muted)
        self.assertEqual(clip.volume, 1.25)
        self.assertEqual(clip.audio_offset_seconds, -0.125)
        sequence = VideoSequence.from_json(UserDict({"schema_version": "1"}))
        self.assertEqual(sequence.schema_version, 1)

    def test_mutations_normalize_numeric_inputs_without_changing_original(self) -> None:
        original = self._sequence()
        updated = original.add_asset("c.mp4", "8", asset_id="asset-c")
        updated = updated.add_clip("asset-c", "1", "4", clip_id="clip-c", volume="0.75")
        updated = updated.set_transition("clip-c", "fade", "0.5")
        updated = updated.set_transition("clip-c", SequenceTransition(type="fade"), "0.25")
        updated = updated.set_clip_audio("clip-c", volume="1.5", audio_offset_seconds="-0.2")
        self.assertEqual(updated.clips[1].transition.duration, 0.25)
        self.assertEqual(updated.clips[1].volume, 1.5)
        self.assertEqual(updated.clips[1].audio_offset_seconds, -0.2)
        self.assertEqual(len(original.assets), 2)
        self.assertEqual(len(original.clips), 1)
        view = updated.as_view()
        self.assertEqual(view["assets"][2]["duration"], 8.0)
        self.assertEqual(view["clips"][1]["clipId"], "clip-c")
        self.assertEqual(view["outputDuration"], 6.75)
        self.assertEqual(updated.output_to_source_seconds("4").clip_id, "clip-c")
        self.assertEqual(updated.source_to_output_seconds("clip-c", "2"), 4.75)

    def test_extensions_are_deep_copied_and_keep_non_string_keys(self) -> None:
        extension = ["keep"]
        payload: dict[object, object] = {17: extension}
        sequence = VideoSequence.from_json(payload)
        extension.append("changed")
        expected = ["keep"]
        self.assertEqual(sequence.to_json()[17], expected)
        saved = sequence.to_json()
        value = saved[17]
        if not isinstance(value, list) or not is_object_sequence(value):
            self.fail("拡張データの配列を保持する必要があります")
        self.assertIsNot(value, sequence.extras[17])
        self.assertEqual(sequence.to_json()[17], expected)

    def test_invalid_numeric_values_keep_domain_errors(self) -> None:
        invalid_values: tuple[object, ...] = (None, [], "invalid", float("nan"), float("inf"))
        for invalid in invalid_values:
            with self.subTest(invalid=invalid):
                with self.assertRaises(VideoSequenceError):
                    self._sequence().add_asset("c.mp4", invalid)
                with self.assertRaises(VideoSequenceError):
                    self._sequence().add_clip("asset-b", 0, 1, volume=invalid)
                with self.assertRaises(VideoSequenceError):
                    self._sequence().output_to_source_seconds(invalid)
        with self.assertRaisesRegex(VideoSequenceError, "between 0 and 2"):
            self._sequence().set_clip_audio("clip-a", volume="2.1")
        with self.assertRaisesRegex(VideoSequenceError, "non-negative"):
            self._sequence().set_transition("clip-a", "fade", "-1")


if __name__ == "__main__":
    unittest.main()
