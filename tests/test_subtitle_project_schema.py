from __future__ import annotations

import unittest
from collections import UserDict
from collections.abc import Callable

from src.data_boundary import is_object_sequence
from src.subtitle_project_schema import (
    AudioMix,
    AudioMixChannel,
    SpeakerInfo,
    SubtitleSegment,
    WaveformInfo,
    SubtitleProjectError,
    normalize_segment,
)


class SubtitleProjectSchemaTests(unittest.TestCase):
    def test_subtitle_segment_model_round_trip(self) -> None:
        model = SubtitleSegment.from_json(
            {
                "id": "seg-1",
                "start": 0.1,
                "end": 0.9,
                "text": " hello ",
                "speaker": "Oz",
                "source_speaker": "alice",
            },
            index=0,
        )
        restored = SubtitleSegment.from_json(model.to_json(), index=0)
        self.assertEqual(restored.to_json(), model.to_json())

    def test_speaker_and_waveform_models_round_trip(self) -> None:
        speaker = SpeakerInfo.from_json(
            {
                "name": "alice",
                "style": "Speaker_Alice",
                "track_key": "craig:alice",
                "file_name": "1-alice.flac",
                "path": "/tmp/1-alice.flac",
                "color": "#445566",
            }
        )
        expected_speaker: dict[object, object] = {
            "name": "alice",
            "style": "Speaker_Alice",
            "track_key": "craig:alice",
            "file_name": "1-alice.flac",
            "path": "/tmp/1-alice.flac",
            "color": "#445566",
        }
        self.assertEqual(speaker.to_json(), expected_speaker)
        waveform = WaveformInfo.from_json(
            {
                "speaker": "Oz",
                "style": "Oz",
                "color": "#445566",
                "source_path": "/tmp/audio.wav",
                "offset_seconds": 0.2,
                "duration_seconds": 1.5,
                "sample_rate": 400,
                "peaks": [0.1, 0.2],
            }
        )
        self.assertEqual(waveform.source_path, "/tmp/audio.wav")
        expected_peaks = [0.1, 0.2]
        self.assertEqual(waveform.peaks, expected_peaks)

    def test_audio_mix_model_round_trip(self) -> None:
        mix = AudioMix.from_json(
            {
                "version": 1,
                "customized": True,
                "channels": [
                    {
                        "id": "video:0:a:0",
                        "kind": "video",
                        "label": "0:a:0",
                        "selector": "0:a:0",
                        "enabled": True,
                        "muted": False,
                        "solo": False,
                        "volume_percent": 100.0,
                    }
                ],
            }
        )
        payload = mix.to_json()
        self.assertEqual(payload["version"], 1)
        channels = payload["channels"]
        if not is_object_sequence(channels):
            self.fail("チャンネル配列を保存する必要があります")
        self.assertEqual(len(channels), 1)
        self.assertIsInstance(AudioMixChannel.from_json(channels[0]), AudioMixChannel)

    def test_segment_normalization_preserves_editing_rules(self) -> None:
        segment = SubtitleSegment.from_json(
            {
                "start": "-1",
                "end": "-2",
                "text": "  first\r\nsecond\\Nthird  ",
                "speaker": " ",
                "layout_row": "-1",
                "layout_row_span": "0",
                "max_width": "2",
                "line_count_override": 2,
                "subtitle_font_scale": "9",
                "subtitle_font_family": "  A\x00\tB\x7f  ",
                "subtitle_volume_level": "0.25",
                "manual_text": True,
                "manual_timing": True,
                "manual_speaker": True,
            },
            index=4,
        )
        self.assertEqual(segment.id, "subtitle-000005")
        self.assertEqual((segment.start, segment.end), (0.0, 0.05))
        self.assertEqual(segment.text, "first\nsecond\nthird")
        self.assertEqual(segment.speaker, "Oz")
        self.assertEqual((segment.layout_row, segment.layout_row_span, segment.max_width), (0, 1, 4))
        self.assertEqual(segment.subtitle_font_scale, 4.0)
        self.assertEqual(segment.subtitle_font_family, "AB")
        self.assertEqual(segment.subtitle_volume_level, 0.25)
        self.assertEqual(segment.subtitle_line_count, "2")
        self.assertTrue(segment.manual_line_count)
        self.assertTrue(segment.manual_text and segment.manual_timing and segment.manual_speaker)
        self.assertTrue(segment.layout_packed)
        literal = SubtitleSegment.from_json({"text": r"first\nsecond", "subtitle_font_scale": "-1"})
        self.assertEqual(literal.text, r"first\nsecond")
        self.assertEqual(literal.subtitle_font_scale, 0.1)

    def test_normalize_segment_keeps_unknown_fields_and_does_not_mutate_input(self) -> None:
        extra = ["original"]
        payload: dict[object, object] = {17: extra, "text": " text "}
        normalized = normalize_segment(payload, 0)
        self.assertEqual(payload["text"], " text ")
        self.assertEqual(normalized["text"], "text")
        extra.append("changed")
        expected = ["original"]
        self.assertEqual(normalized[17], expected)
        self.assertNotIn("words", normalized)
        self.assertNotIn("layout_row_span", normalized)

    def test_segment_words_and_extensions_are_copied_at_input(self) -> None:
        annotations = ["original"]
        word: dict[object, object] = {"start": 0.1, "end": 0.2, "word": "a", 7: annotations}
        words = [word]
        segment = SubtitleSegment.from_json({"words": words, "future": annotations})
        words.clear()
        annotations.append("changed")
        expected = ["original"]
        self.assertEqual(len(segment.words), 1)
        self.assertEqual(segment.words[0][7], expected)
        self.assertEqual(segment.extras["future"], expected)
        self.assertIsNot(segment.words[0], word)
        saved = segment.to_json()
        self.assertIsNot(saved["future"], segment.extras["future"])
        restored = SubtitleSegment.from_json(saved)
        self.assertEqual(restored.to_json(), saved)

    def test_segment_numeric_errors_keep_project_error(self) -> None:
        invalid_values: tuple[object, ...] = (None, [], "invalid", float("nan"), float("inf"))
        for field in ("start", "end", "subtitle_font_scale"):
            for invalid in invalid_values:
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaisesRegex(SubtitleProjectError, field):
                        SubtitleSegment.from_json({field: invalid})
        with self.assertRaisesRegex(SubtitleProjectError, "subtitle_line_count"):
            SubtitleSegment.from_json({"subtitle_line_count": True})

    def test_model_boundaries_reject_invalid_shapes(self) -> None:
        loaders: tuple[Callable[[object], object], ...] = (
            SubtitleSegment.from_json,
            SpeakerInfo.from_json,
            WaveformInfo.from_json,
            AudioMixChannel.from_json,
            AudioMix.from_json,
        )
        invalid_roots: tuple[object, ...] = (None, [], "invalid", 1)
        for loader in loaders:
            for value in invalid_roots:
                with self.subTest(loader=loader, value=value):
                    with self.assertRaisesRegex(SubtitleProjectError, "must be an object"):
                        loader(value)
        invalid_words: tuple[object, ...] = (None, {}, "invalid", [None], [1])
        for value in invalid_words:
            with self.subTest(words=value):
                with self.assertRaisesRegex(SubtitleProjectError, "segment.words"):
                    SubtitleSegment.from_json({"words": value})
        invalid_peaks: tuple[object, ...] = (None, [None], ["0.1"], [[]])
        for value in invalid_peaks:
            with self.subTest(peaks=value):
                with self.assertRaisesRegex(SubtitleProjectError, "waveform.peaks"):
                    WaveformInfo.from_json({"peaks": value})

    def test_speaker_and_waveform_preserve_extensions_and_numeric_values(self) -> None:
        future = ["original"]
        speaker = SpeakerInfo.from_json(UserDict({"speaker": "legacy", "future": future}))
        self.assertEqual((speaker.name, speaker.style), ("Oz", "legacy"))
        peaks = [0.0, 1.0, 0.25]
        waveform = WaveformInfo.from_json(
            {
                "offset_seconds": "-0.2",
                "duration_seconds": "1.5",
                "sample_rate": 400.9,
                "peaks": peaks,
                "future": future,
            }
        )
        peaks.clear()
        future.append("changed")
        expected = ["original"]
        expected_peaks = [0.0, 1.0, 0.25]
        self.assertEqual(speaker.to_json()["future"], expected)
        self.assertEqual(waveform.to_json()["future"], expected)
        self.assertEqual(waveform.peaks, expected_peaks)
        self.assertEqual(waveform.offset_seconds, -0.2)
        self.assertEqual(waveform.duration_seconds, 1.5)
        self.assertEqual(waveform.sample_rate, 400)
        self.assertEqual(WaveformInfo.from_json(waveform.to_json()).to_json(), waveform.to_json())

    def test_audio_mix_preserves_filtering_optional_fields_and_extension_policy(self) -> None:
        mix = AudioMix.from_json(
            {
                "version": "1",
                "customized": 1,
                "future": "ignored",
                "channels": [None, 1, {"id": 7, "volume_percent": "75.5", "path": "a.wav", "future": "ignored"}],
            }
        )
        self.assertEqual(len(mix.channels), 1)
        channel = mix.channels[0]
        self.assertEqual(channel.id, "7")
        self.assertEqual(channel.volume_percent, 75.5)
        self.assertIsNone(channel.selector)
        self.assertEqual(channel.path, "a.wav")
        saved = channel.to_json()
        self.assertNotIn("selector", saved)
        self.assertNotIn("future", saved)
        self.assertNotIn("future", mix.to_json())
        channel.extras["custom"] = "explicit"
        self.assertEqual(channel.to_json()["custom"], "explicit")
        with self.assertRaisesRegex(SubtitleProjectError, "audio_mix.channels"):
            AudioMix.from_json({"channels": None})


if __name__ == "__main__":
    unittest.main()
