from __future__ import annotations

import unittest
from collections.abc import Mapping, Sequence

from src.data_boundary import is_object_mapping, is_object_sequence
from src.subtitle_text_rules import reattach_leading_punctuation
from tests.typed_case import TypedTestCase


def segment(
    text: str,
    start: float,
    *,
    speaker: str = "Oz",
    source_track: str = "craig:oz",
    source_file: str = "",
    source_stream_id: str = "",
    words: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
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


def word_texts(value: object) -> list[str]:
    if not is_object_mapping(value):
        raise AssertionError("字幕が辞書ではありません")
    segment_value: Mapping[object, object] = value
    words = segment_value["words"]
    if not is_object_sequence(words) or isinstance(words, (str, bytes, bytearray)):
        raise AssertionError("単語一覧がありません")
    result: list[str] = []
    for word in words:
        if not is_object_mapping(word):
            raise AssertionError("単語が辞書ではありません")
        value = word["word"]
        if not isinstance(value, str):
            raise AssertionError("単語が文字列ではありません")
        result.append(value)
    return result


def caption_texts(segments: Sequence[Mapping[object, object]]) -> list[str]:
    result: list[str] = []
    for segment_value in segments:
        value = segment_value["text"]
        if not isinstance(value, str):
            raise AssertionError("字幕が文字列ではありません")
        result.append(value)
    return result


class SubtitleTextRuleTests(TypedTestCase):
    def test_preserves_extension_references_without_mutating_input(self) -> None:
        extension = object()
        aligned: dict[str, object] = {"word": "前", "extension": extension}
        original = [segment("前", 0.0, words=[aligned]), segment("。次", 1.0)]

        repaired = reattach_leading_punctuation(original)

        self.assertIsNot(repaired[0], original[0])
        self.assertIsNot(repaired[0]["words"], original[0]["words"])
        expected_original = ["前"]
        expected_repaired = ["前。"]
        self.assertEqual(word_texts(original[0]), expected_original)
        self.assertEqual(word_texts(repaired[0]), expected_repaired)
        words = repaired[0]["words"]
        if not is_object_sequence(words):
            self.fail("単語一覧がありません")
        word = words[0]
        if not is_object_mapping(word):
            self.fail("単語が辞書ではありません")
        self.assertIs(word["extension"], extension)

    def test_rejects_non_mapping_segment(self) -> None:
        with self.assertRaises(TypeError):
            reattach_leading_punctuation([None])

    def test_moves_leading_period_to_the_previous_caption(self) -> None:
        original = [
            segment("○○だよね", 0.0),
            segment("。○○なんだけど", 1.0),
        ]

        repaired = reattach_leading_punctuation(original)

        expected = ["○○だよね。", "○○なんだけど"]
        self.assertEqual(caption_texts(repaired), expected)
        self.assertEqual(word_texts(repaired[0])[0], "○○だよね。")
        self.assertEqual(word_texts(repaired[1])[0], "○○なんだけど")
        self.assertEqual(original[0]["text"], "○○だよね")
        self.assertEqual(original[1]["text"], "。○○なんだけど")
        self.assertEqual(word_texts(original[0])[0], "○○だよね")
        self.assertEqual(word_texts(original[1])[0], "。○○なんだけど")

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

        expected = ["Built with", ".NET 9", ", literally"]
        self.assertEqual(caption_texts(repaired), expected)

    def test_removes_a_punctuation_only_caption_after_reattaching_it(self) -> None:
        repaired = reattach_leading_punctuation(
            [segment("前の字幕", 0.0), segment("。！？", 1.0)]
        )

        expected = ["前の字幕。！？"]
        self.assertEqual(caption_texts(repaired), expected)

    def test_does_not_cross_source_tracks(self) -> None:
        repaired = reattach_leading_punctuation(
            [
                segment("Ozの字幕", 0.0, speaker="Oz", source_track="craig:oz"),
                segment("。別話者", 1.0, speaker="Guest", source_track="craig:guest"),
            ]
        )

        expected = ["Ozの字幕", "。別話者"]
        self.assertEqual(caption_texts(repaired), expected)

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

        expected = ["最初の音声", "。別の音声"]
        self.assertEqual(caption_texts(repaired), expected)

    def test_can_reattach_across_diarized_speakers_on_the_same_track(self) -> None:
        repaired = reattach_leading_punctuation(
            [
                segment("前の話者", 0.0, speaker="A", source_track="0:a:1"),
                segment("。次の話者", 1.0, speaker="B", source_track="0:a:1"),
            ]
        )

        expected = ["前の話者。", "次の話者"]
        self.assertEqual(caption_texts(repaired), expected)

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
        self.assertEqual("".join(word_texts(repaired[1])), "次。")

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
        self.assertEqual(word_texts(repaired[0])[0], "前！？")
        self.assertEqual(repaired[1]["text"], "次")
        expected = ["次"]
        self.assertEqual(word_texts(repaired[1]), expected)

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
        expected_repaired = ["次"]
        expected_original = ["！", "次"]
        self.assertEqual(word_texts(repaired[1]), expected_repaired)
        self.assertEqual(word_texts(original[1]), expected_original)

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
        expected_repaired = ["次"]
        expected_original = ["？次"]
        self.assertEqual(word_texts(repaired[1]), expected_repaired)
        self.assertEqual(word_texts(original[1]), expected_original)

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
        expected = ["次"]
        self.assertEqual(word_texts(repaired[1]), expected)


if __name__ == "__main__":
    unittest.main()
