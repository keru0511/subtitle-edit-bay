from __future__ import annotations

import unittest

from src.subtitle_text_rules import reattach_leading_punctuation


def segment(
    text: str,
    start: float,
    *,
    speaker: str = "Oz",
    source_track: str = "craig:oz",
) -> dict:
    return {
        "start": start,
        "end": start + 1.0,
        "text": text,
        "speaker": speaker,
        "source_track": source_track,
        "source_speaker": speaker,
        "words": [{"word": text, "start": start, "end": start + 0.8}],
    }


class SubtitleTextRuleTests(unittest.TestCase):
    def test_moves_leading_period_to_the_previous_caption(self) -> None:
        original = [
            segment("○○だよね", 0.0),
            segment("。○○なんだけど", 1.0),
        ]

        repaired = reattach_leading_punctuation(original)

        self.assertEqual([item["text"] for item in repaired], ["○○だよね。", "○○なんだけど"])
        self.assertEqual(repaired[0]["words"][0]["word"], "○○だよね。")
        self.assertEqual(repaired[1]["words"][0]["word"], "○○なんだけど")
        self.assertEqual(original[0]["text"], "○○だよね")
        self.assertEqual(original[1]["text"], "。○○なんだけど")
        self.assertEqual(original[0]["words"][0]["word"], "○○だよね")
        self.assertEqual(original[1]["words"][0]["word"], "。○○なんだけど")

    def test_moves_all_supported_leading_closing_punctuation(self) -> None:
        punctuation = "、。！？!?，．,."

        repaired = reattach_leading_punctuation(
            [segment("前の字幕", 0.0), segment(punctuation + "次の字幕", 1.0)]
        )

        self.assertEqual(repaired[0]["text"], "前の字幕" + punctuation)
        self.assertEqual(repaired[1]["text"], "次の字幕")

    def test_removes_a_punctuation_only_caption_after_reattaching_it(self) -> None:
        repaired = reattach_leading_punctuation(
            [segment("前の字幕", 0.0), segment("。！？", 1.0)]
        )

        self.assertEqual([item["text"] for item in repaired], ["前の字幕。！？"])

    def test_does_not_cross_source_tracks(self) -> None:
        repaired = reattach_leading_punctuation(
            [
                segment("Ozの字幕", 0.0, speaker="Oz", source_track="craig:oz"),
                segment("。別話者", 1.0, speaker="Guest", source_track="craig:guest"),
            ]
        )

        self.assertEqual([item["text"] for item in repaired], ["Ozの字幕", "。別話者"])

    def test_can_reattach_across_diarized_speakers_on_the_same_track(self) -> None:
        repaired = reattach_leading_punctuation(
            [
                segment("前の話者", 0.0, speaker="A", source_track="0:a:1"),
                segment("。次の話者", 1.0, speaker="B", source_track="0:a:1"),
            ]
        )

        self.assertEqual([item["text"] for item in repaired], ["前の話者。", "次の話者"])

    def test_uses_time_order_even_when_input_is_grouped_by_track(self) -> None:
        later = segment("。後半", 2.0)
        earlier = segment("前半", 1.0)

        repaired = reattach_leading_punctuation([later, earlier])

        self.assertEqual(repaired[0]["text"], "後半")
        self.assertEqual(repaired[1]["text"], "前半。")

    def test_keeps_leading_punctuation_when_no_safe_predecessor_exists(self) -> None:
        repaired = reattach_leading_punctuation([segment("。最初の字幕", 0.0)])

        self.assertEqual(repaired[0]["text"], "。最初の字幕")


if __name__ == "__main__":
    unittest.main()
