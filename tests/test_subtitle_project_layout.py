import unittest

from src.subtitle_project import create_project


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


if __name__ == "__main__":
    unittest.main()
