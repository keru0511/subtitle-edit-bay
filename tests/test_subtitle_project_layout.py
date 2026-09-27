from copy import deepcopy
import random
import unittest
from typing import TypedDict

from src.subtitle_project import assign_project_layout_rows, create_project
from tests.typed_case import TypedTestCase


class _LayoutSegmentFields(TypedDict):
    id: str
    start: float
    end: float
    text: str
    max_width: int


class LayoutSegment(_LayoutSegmentFields, total=False):
    layout_row: int
    layout_row_span: int


def _layout_rows(raw_segments: object) -> dict[str, tuple[int, int]]:
    if not isinstance(raw_segments, list):
        raise AssertionError("字幕セグメントがリストではありません")
    rows: dict[str, tuple[int, int]] = {}
    for segment in raw_segments:
        if not isinstance(segment, dict):
            raise AssertionError("字幕セグメントが辞書ではありません")
        identifier = segment.get("id")
        row = segment.get("layout_row")
        span = segment.get("layout_row_span")
        if not isinstance(identifier, str) or not isinstance(row, int) or not isinstance(span, int):
            raise AssertionError(f"字幕レイアウトの値が不正です: {segment!r}")
        if identifier in rows:
            raise AssertionError(f"字幕IDが重複しています: {identifier}")
        rows[identifier] = (row, span)
    return rows


def _layout_sort_key(segment: LayoutSegment) -> tuple[float, float, str]:
    return (segment["start"], segment["end"], segment["id"])


def assign_rows_reference(segments: list[LayoutSegment]) -> list[LayoutSegment]:
    row_end_times: list[float] = []
    for segment in sorted(segments, key=_layout_sort_key):
        span = 2 if len(segment["text"]) > int(segment.get("max_width", 24)) else 1
        base_row = 0
        while True:
            while len(row_end_times) < base_row + span:
                row_end_times.append(0.0)
            if all(row_end_times[row] <= segment["start"] for row in range(base_row, base_row + span)):
                break
            base_row += 1
        segment["layout_row"] = base_row
        segment["layout_row_span"] = span
        for row in range(base_row, base_row + span):
            row_end_times[row] = segment["end"]
    return segments


class SubtitleProjectLayoutTests(TypedTestCase):
    def test_edited_overlaps_are_reflowed_without_dropping_captions(self) -> None:
        project = create_project(
            video_path="game.mkv",
            output_dir="out",
            segments=[
                {"id": "a", "start": 0, "end": 3, "text": "A", "speaker": "Oz"},
                {"id": "b", "start": 1, "end": 2, "text": "B", "speaker": "A"},
                {"id": "c", "start": 1.5, "end": 2.5, "text": "C", "speaker": "B"},
                {"id": "d", "start": 1.7, "end": 1.9, "text": "D", "speaker": "C"},
            ],
        )

        rows = _layout_rows(project.get("segments"))
        self.assertEqual(len(rows), 4)
        row_positions = {identifier: row for identifier, (row, _) in rows.items()}
        self.assertEqual(row_positions, {"a": 0, "b": 1, "c": 2, "d": 3})

    def test_two_line_caption_reserves_two_rows(self) -> None:
        project = create_project(
            video_path="game.mkv",
            output_dir="out",
            segments=[
                {
                    "id": "long",
                    "start": 0,
                    "end": 3,
                    "text": "これは二行分の幅を予約するために十分長い字幕です",
                    "speaker": "Oz",
                    "max_width": 12,
                },
                {"id": "short", "start": 1, "end": 2, "text": "短い", "speaker": "A"},
            ],
        )

        rows = _layout_rows(project.get("segments"))
        self.assertEqual(rows["long"][1], 2)
        self.assertEqual(rows["short"][0], 2)

    def test_fast_allocator_matches_first_fit_reference(self) -> None:
        randomizer = random.Random(20260717)
        for iteration in range(100):
            segments: list[LayoutSegment] = []
            for index in range(randomizer.randint(1, 80)):
                start = round(randomizer.random() * 15, 3)
                duration = round(0.05 + randomizer.random() * 4, 3)
                two_lines = randomizer.random() < 0.3
                segments.append(
                    {
                        "id": f"{iteration}-{index}",
                        "start": start,
                        "end": start + duration,
                        "text": "abcdefghijklm" if two_lines else "short",
                        "max_width": 10 if two_lines else 24,
                    }
                )

            expected = assign_rows_reference(deepcopy(segments))
            actual_input: list[dict[object, object]] = []
            for segment in segments:
                copied_segment: dict[object, object] = {}
                copied_segment.update(segment)
                actual_input.append(copied_segment)
            actual = assign_project_layout_rows(actual_input)
            expected_rows = {item["id"]: (item["layout_row"], item["layout_row_span"]) for item in expected}
            actual_rows = _layout_rows(actual)
            self.assertEqual(actual_rows, expected_rows)


if __name__ == "__main__":
    unittest.main()
