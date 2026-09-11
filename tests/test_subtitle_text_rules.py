from __future__ import annotations

import unittest

from src.subtitle_text_rules import reattach_leading_punctuation


def segment(
    text: str,
    start: float,
    *,
    speaker: str = "Oz",
    source_track: str = "craig:oz",
    source_file: str = "",
    source_stream_id: str = "",
    words: list[dict] | None = None,
) -> dict:
    result = {
        "start": start,
        "end": start + 1.0,
        "text": text,
        "speaker": speaker,
        "source_track": source_track,
        "source_speaker": speaker,
        "words": words if words is not None else [{"word": text, "start": start, "end": start + 0.8}],
    }
    if source_file:
        result["source_file"] = source_file
    if source_stream_id:
        result["source_stream_id"] = source_stream_id
    return result


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
        punctuation = "、。！？!?"

        repaired = reattach_leading_punctuation(
            [segment("前の字幕", 0.0), segment(punctuation + "次の字幕", 1.0)]
        )

        self.assertEqual(repaired[0]["text"], "前の字幕" + punctuation)
        self.assertEqual(repaired[1]["text"], "次の字幕")

    def test_does_not_move_ascii_period_or_comma(self) -> None:
        repaired = reattach_leading_punctuation(
            [
                segment("Built with", 0.0),
                segment(".NET 9", 1.0),
                segment(", literally", 2.0),
            ]
        )

        self.assertEqual([item["text"] for item in repaired], ["Built with", ".NET 9", ", literally"])

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

    def test_does_not_cross_unique_sources_with_the_same_file_name_and_track(self) -> None:
        repaired = reattach_leading_punctuation(
            [
                segment(
                    "最初の音声",
                    0.0,
                    source_file="1-speaker.flac",
                    source_stream_id="audio-first",
                ),
                segment(
                    "。別の音声",
                    1.0,
                    source_file="1-speaker.flac",
                    source_stream_id="audio-second",
                ),
            ]
        )

        self.assertEqual([item["text"] for item in repaired], ["最初の音声", "。別の音声"])

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

    def test_does_not_remove_later_word_punctuation_when_the_leading_mark_is_unaligned(self) -> None:
        repaired = reattach_leading_punctuation(
            [
                segment("前", 0.0),
                segment(
                    "。次。",
                    1.0,
                    words=[
                        {"word": "次", "start": 1.0, "end": 1.5},
                        {"word": "。", "start": 1.5, "end": 2.0},
                    ],
                ),
            ]
        )

        self.assertEqual(repaired[1]["text"], "次。")
        self.assertEqual("".join(word["word"] for word in repaired[1]["words"]), "次。")

    def test_removes_leading_punctuation_across_multiple_aligned_words(self) -> None:
        repaired = reattach_leading_punctuation(
            [
                segment("前", 0.0),
                segment(
                    "！？次",
                    1.0,
                    words=[
                        {"word": "！", "start": 1.0, "end": 1.1},
                        {"word": "？次", "start": 1.1, "end": 2.0},
                    ],
                ),
            ]
        )

        self.assertEqual(repaired[0]["text"], "前！？")
        self.assertEqual(repaired[0]["words"][0]["word"], "前！？")
        self.assertEqual(repaired[1]["text"], "次")
        self.assertEqual([word["word"] for word in repaired[1]["words"]], ["次"])

    def test_removes_partial_aligned_prefix_when_later_punctuation_is_unaligned(self) -> None:
        original = [
            segment("前", 0.0),
            segment(
                "！？次",
                1.0,
                words=[
                    {"word": "！", "start": 1.0, "end": 1.1},
                    {"word": "次", "start": 1.1, "end": 2.0},
                ],
            ),
        ]

        repaired = reattach_leading_punctuation(original)

        self.assertEqual(repaired[0]["text"], "前！？")
        self.assertEqual(repaired[1]["text"], "次")
        self.assertEqual([word["word"] for word in repaired[1]["words"]], ["次"])
        self.assertEqual([word["word"] for word in original[1]["words"]], ["！", "次"])

    def test_removes_aligned_later_mark_when_the_first_mark_is_unaligned(self) -> None:
        original = [
            segment("前", 0.0),
            segment(
                "！？次",
                1.0,
                words=[{"word": "？次", "start": 1.0, "end": 2.0}],
            ),
        ]

        repaired = reattach_leading_punctuation(original)

        self.assertEqual(repaired[0]["text"], "前！？")
        self.assertEqual(repaired[1]["text"], "次")
        self.assertEqual([word["word"] for word in repaired[1]["words"]], ["次"])
        self.assertEqual([word["word"] for word in original[1]["words"]], ["？次"])

    def test_removes_aligned_later_mark_from_a_separate_word(self) -> None:
        repaired = reattach_leading_punctuation(
            [
                segment("前", 0.0),
                segment(
                    "！？次",
                    1.0,
                    words=[
                        {"word": "？", "start": 1.0, "end": 1.1},
                        {"word": "次", "start": 1.1, "end": 2.0},
                    ],
                ),
            ]
        )

        self.assertEqual(repaired[0]["text"], "前！？")
        self.assertEqual(repaired[1]["text"], "次")
        self.assertEqual([word["word"] for word in repaired[1]["words"]], ["次"])


if __name__ == "__main__":
    unittest.main()
