from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.color_config import load_speaker_color_map, normalize_rgb_color, save_speaker_color


class ColorConfigTests(unittest.TestCase):
    def test_normalize_rgb_color_accepts_qml_rgb_and_argb_values(self) -> None:
        self.assertEqual(normalize_rgb_color("#12abef"), "#12ABEF")
        self.assertEqual(normalize_rgb_color("#FF12ABEF"), "#12ABEF")
        with self.assertRaises(ValueError):
            normalize_rgb_color("blue")

    def test_save_speaker_color_preserves_aliases_and_updates_both_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "speaker_colors.json"
            path.write_text(
                json.dumps({
                    "files": {"1-alice.flac": {"color": "#FFFFFF", "aliases": ["1-alice.aac"]}},
                    "speakers": {},
                }),
                encoding="utf-8",
            )

            save_speaker_color(
                path,
                file_name="1-alice.flac",
                speaker_name="alice",
                color="#123456",
            )

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["files"]["1-alice.flac"]["aliases"], ["1-alice.aac"])
            self.assertEqual(payload["files"]["1-alice.flac"]["color"], "#123456")
            self.assertEqual(payload["speakers"]["alice"]["color"], "#123456")
            mapping = load_speaker_color_map(path)
            self.assertEqual(mapping["1-alice.aac"], "#123456")
            self.assertEqual(mapping["alice"], "#123456")


if __name__ == "__main__":
    unittest.main()
