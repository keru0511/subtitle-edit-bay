from __future__ import annotations

import unittest

from src.ass_template import (
    apply_subtitle_font_size,
    apply_subtitle_outline,
    build_ass_header,
    build_extra_style_definitions,
    clone_style_definition,
    normalize_ass_color,
)


class AssTemplateTests(unittest.TestCase):
    def test_normalize_ass_color_accepts_and_preserves_andh_format(self) -> None:
        self.assertEqual(normalize_ass_color("&H00FF1234"), "&H00FF1234")

    def test_normalize_ass_color_converts_hash_to_ass_bgr(self) -> None:
        self.assertEqual(normalize_ass_color("#123456"), "&H00563412")
        self.assertEqual(normalize_ass_color("123456"), "&H00563412")

    def test_normalize_ass_color_rejects_invalid_formats(self) -> None:
        invalid = ["blue", "#GGG", "12", "12345G"]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_ass_color(value)

    def test_clone_style_definition_replaces_name_and_color(self) -> None:
        cloned = clone_style_definition("Oz", "Alice", "#AABBCC")
        self.assertTrue(cloned.startswith("Style: Alice"))
        self.assertIn("&H00CCBBAA", cloned)

    def test_build_extra_style_definitions_returns_empty_without_overrides(self) -> None:
        self.assertEqual(build_extra_style_definitions(None), [])
        self.assertEqual(build_extra_style_definitions({}), [])

    def test_build_extra_style_definitions_clones_each_override(self) -> None:
        overrides = {"Alice": ("Oz", "#AABBCC")}
        definitions = build_extra_style_definitions(overrides)
        self.assertEqual(len(definitions), 1)
        self.assertTrue(definitions[0].startswith("Style: Alice"))

    def test_apply_subtitle_font_size_adjusts_font_field(self) -> None:
        updated = apply_subtitle_font_size(
            "Style: Oz,Arial,50,&H00FFFFFF,&H000000FF,&H003030FF,&H66000000,-1,0,0,0,100,100,0,0,1,3,1,2,36,36,34,1",
            60,
        )
        fields = updated.split(",")
        self.assertEqual(fields[2], "60")

    def test_apply_subtitle_font_size_rejects_too_small(self) -> None:
        style = "Style: Oz,Arial,50,&H00FFFFFF,&H000000FF,&H003030FF,&H66000000,-1,0,0,0,100,100,0,0,1,3,1,2,36,36,34,1"
        with self.assertRaises(ValueError):
            apply_subtitle_font_size(style, 2)

    def test_apply_subtitle_outline_updates_color_and_thickness(self) -> None:
        updated = apply_subtitle_outline(
            "Style: Oz,Arial,50,&H00FFFFFF,&H000000FF,&H003030FF,&H66000000,-1,0,0,0,100,100,0,0,1,3,1,2,36,36,34,1",
            "#AABBCC",
            5,
        )
        fields = updated.split(",")
        self.assertEqual(fields[5], "&H00CCBBAA")
        self.assertEqual(fields[16], "5")

    def test_apply_subtitle_outline_rejects_out_of_range_thickness(self) -> None:
        style = "Style: Oz,Arial,50,&H00FFFFFF,&H000000FF,&H003030FF,&H66000000,-1,0,0,0,100,100,0,0,1,3,1,2,36,36,34,1"
        with self.assertRaises(ValueError):
            apply_subtitle_outline(style, "#000000", 21)
        with self.assertRaises(ValueError):
            apply_subtitle_outline(style, "#000000", -1)

    def test_build_ass_header_contains_expected_sections(self) -> None:
        header = build_ass_header()
        self.assertIn("[Script Info]", header)
        self.assertIn("WrapStyle: 2", header)
        self.assertIn("[V4+ Styles]", header)
        self.assertIn("[Events]", header)
        self.assertIn("PlayResX: 1920", header)
        self.assertIn("Style: Oz", header)

    def test_build_ass_header_applies_overrides_size_and_outline(self) -> None:
        header = build_ass_header(
            width=1280,
            height=720,
            style_overrides={"Alice": ("Oz", "#AABBCC")},
            subtitle_font_size=60,
            subtitle_outline_color="#112233",
            subtitle_outline_thickness=5,
        )
        self.assertIn("PlayResX: 1280", header)
        self.assertIn("PlayResY: 720", header)
        self.assertIn("Style: Alice", header)


if __name__ == "__main__":
    unittest.main()
