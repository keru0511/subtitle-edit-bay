import unittest

from src.subtitle_line_count_config import normalize_subtitle_line_count, subtitle_line_count_max_lines


class SubtitleLineCountConfigTests(unittest.TestCase):
    def test_line_count_normalizer_accepts_auto_one_and_two_only(self) -> None:
        self.assertEqual(normalize_subtitle_line_count(None), "auto")
        self.assertEqual(normalize_subtitle_line_count(""), "auto")
        self.assertEqual(normalize_subtitle_line_count(1), "1")
        self.assertEqual(normalize_subtitle_line_count("2"), "2")
        with self.assertRaises(ValueError):
            normalize_subtitle_line_count(True)
        with self.assertRaises(ValueError):
            normalize_subtitle_line_count("3")

    def test_normalizer_preserves_case_whitespace_and_numeric_rules(self) -> None:
        cases: tuple[tuple[object, str], ...] = ((" AUTO ", "auto"), (" 1 ", "1"), (2, "2"), (None, "auto"))
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(normalize_subtitle_line_count(value), expected)
        invalid_values: tuple[object, ...] = (False, 0, 3, 1.0, "1.0", [], {})
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_subtitle_line_count(value)

    def test_max_lines_preserves_auto_and_explicit_limits(self) -> None:
        self.assertIsNone(subtitle_line_count_max_lines())
        self.assertIsNone(subtitle_line_count_max_lines(" AUTO "))
        self.assertEqual(subtitle_line_count_max_lines(1), 1)
        self.assertEqual(subtitle_line_count_max_lines("2"), 2)
        with self.assertRaises(ValueError):
            subtitle_line_count_max_lines("3")


if __name__ == "__main__":
    unittest.main()
