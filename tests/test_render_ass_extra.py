from __future__ import annotations

import unittest
from unittest import mock

from src.render_ass import (
    SubtitleEvent,
    build_track_style_overrides,
    infer_style,
    parse_track_color_args,
    resolve_event_style_override,
    style_name_for_speaker,
    style_name_for_track,
)


class RenderAssExtraTests(unittest.TestCase):
    def test_parse_track_color_args_parses_multiple_tracks(self) -> None:
        result = parse_track_color_args(["Track1=#AABBCC", "  Track2 = #DDEEFF "])
        self.assertEqual(result, {"Track1": "#AABBCC", "Track2": "#DDEEFF"})

    def test_parse_track_color_args_rejects_missing_equals(self) -> None:
        with self.assertRaises(SystemExit):
            parse_track_color_args(["Track1#AABBCC"])

    def test_style_name_for_track(self) -> None:
        self.assertEqual(style_name_for_track("Track 1"), "Track_Track_1")
        self.assertEqual(style_name_for_track(""), "Track_track")

    def test_style_name_for_speaker_escapes_and_hashes(self) -> None:
        self.assertTrue(style_name_for_speaker("A/B C").startswith("Speaker_A_B_C"))
        special = style_name_for_speaker("🎮")
        self.assertTrue(special.startswith("Speaker_speaker_"))

    def test_resolve_event_style_override_prefers_speaker_file_then_speaker(self) -> None:
        event = SubtitleEvent(
            start=0.0,
            end=1.0,
            text="hi",
            speaker="Oz",
            metadata={
                "source_file": " clip1.mp4 ",
                "source_speaker": "Alice",
                "source_track": "Track1",
            },
        )
        speaker_map = {"clip1.mp4": "#AABBCC", "alice": "#DDEEFF"}
        track_map = {"Track1": "#112233"}
        override = resolve_event_style_override(event, speaker_color_map=speaker_map, track_color_map=track_map)
        self.assertIsNotNone(override)
        style, color = override
        self.assertEqual(color, "#AABBCC")

    def test_resolve_event_style_override_falls_back_to_track(self) -> None:
        event = SubtitleEvent(
            start=0.0,
            end=1.0,
            text="hi",
            speaker="Oz",
            metadata={"source_track": "Track1"},
        )
        track_map = {"Track1": "#112233"}
        override = resolve_event_style_override(event, track_color_map=track_map)
        self.assertEqual(override, ("Track_Track1", "#112233"))

    def test_resolve_event_style_override_returns_none(self) -> None:
        event = SubtitleEvent(start=0.0, end=1.0, text="hi", speaker="Oz", metadata={})
        self.assertIsNone(resolve_event_style_override(event))

    def test_infer_style_applies_speaker_color_override(self) -> None:
        event = SubtitleEvent(
            start=0.0,
            end=1.0,
            text="hi",
            speaker="Oz",
            metadata={"source_file": "clip1"},
        )
        result = infer_style(event, speaker_color_map={"clip1": "#AABBCC"})
        self.assertEqual(result, "Speaker_clip1")

    def test_infer_style_applies_shout_to_override(self) -> None:
        event = SubtitleEvent(
            start=0.0,
            end=1.0,
            text="hi",
            speaker="Oz",
            emphasis="shout",
            metadata={"source_file": "clip1"},
        )
        result = infer_style(event, speaker_color_map={"clip1": "#AABBCC"})
        self.assertEqual(result, "ShoutSpeaker_clip1")

    def test_build_track_style_overrides_collects_unique_pairs(self) -> None:
        events = [
            SubtitleEvent(
                start=0.0,
                end=1.0,
                text="a",
                speaker="Oz",
                metadata={"source_file": "clip1"},
            ),
            SubtitleEvent(
                start=1.0,
                end=2.0,
                text="b",
                speaker="Oz",
                emphasis="shout",
                metadata={"source_file": "clip1"},
            ),
        ]
        overrides = build_track_style_overrides(events, speaker_color_map={"clip1": "#AABBCC"})
        self.assertEqual(overrides["Speaker_clip1"], ("Oz", "#AABBCC"))
        self.assertEqual(overrides["ShoutSpeaker_clip1"], ("ShoutOz", "#AABBCC"))


if __name__ == "__main__":
    unittest.main()
