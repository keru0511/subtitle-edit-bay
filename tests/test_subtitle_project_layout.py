from copy import deepcopy
import random
import unittest

from src.subtitle_project import assign_project_layout_rows, create_project


def assign_rows_reference(segments: list[dict]) -> list[dict]:
    row_end_times: list[float] = []
    for segment in sorted(segments, key=lambda item: (item["start"], item["end"], item["id"])):
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


class SubtitleProjectLayoutTests(unittest.TestCase):
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

        self.assertEqual(len(project["segments"]), 4)
        rows = {segment["id"]: segment["layout_row"] for segment in project["segments"]}
        self.assertEqual(rows, {"a": 0, "b": 1, "c": 2, "d": 3})

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

        rows = {segment["id"]: segment for segment in project["segments"]}
        self.assertEqual(rows["long"]["layout_row_span"], 2)
        self.assertEqual(rows["short"]["layout_row"], 2)

    def test_multiline_caption_reserves_all_rows_before_overlapping_caption(self) -> None:
        project = create_project(
            video_path="game.mkv",
            output_dir="out",
            segments=[
                {
                    "id": "four-lines",
                    "start": 0,
                    "end": 3,
                    "text": "abcdefgh\nijklmnop",
                    "speaker": "Oz",
                    "max_width": 4,
                },
                {"id": "short", "start": 1, "end": 2, "text": "short", "speaker": "A"},
            ],
        )

        rows = {segment["id"]: segment for segment in project["segments"]}
        self.assertEqual(rows["four-lines"]["layout_row_span"], 4)
        self.assertEqual(rows["short"]["layout_row"], 4)

    def test_fast_allocator_matches_first_fit_reference(self) -> None:
        randomizer = random.Random(20260717)
        for iteration in range(100):
            segments = []
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
            actual = assign_project_layout_rows(deepcopy(segments))
            expected_rows = {
                item["id"]: (item["layout_row"], item["layout_row_span"])
                for item in expected
            }
            actual_rows = {
                item["id"]: (item["layout_row"], item["layout_row_span"])
                for item in actual
            }
            self.assertEqual(actual_rows, expected_rows)


if __name__ == "__main__":
    unittest.main()
