from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.color_config import load_speaker_color_map, normalize_rgb_color, save_speaker_color
from src.data_boundary import decode_json, is_object_mapping
from tests.typed_case import TypedTestCase


class ColorConfigTests(TypedTestCase):
    def test_normalize_rgb_color_accepts_qml_rgb_and_argb_values(self) -> None:
        self.assertEqual(normalize_rgb_color("#12abef"), "#12ABEF")
        self.assertEqual(normalize_rgb_color("#FF12ABEF"), "#12ABEF")
        with self.assertRaises(ValueError):
            normalize_rgb_color("blue")

    def test_save_speaker_color_preserves_aliases_and_updates_both_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "speaker_colors.json"
            aliases = ["1-alice.aac"]
            file_entry: dict[str, object] = {"color": "#FFFFFF", "aliases": aliases}
            files: dict[str, object] = {"1-alice.flac": file_entry}
            initial: dict[str, object] = {"files": files, "speakers": dict[str, object]()}
            path.write_text(
                json.dumps(initial),
                encoding="utf-8",
            )

            save_speaker_color(
                path,
                file_name="1-alice.flac",
                speaker_name="alice",
                color="#123456",
            )

            payload = decode_json(path.read_text(encoding="utf-8"))
            if not is_object_mapping(payload):
                self.fail("話者色設定が辞書ではありません")
            saved_files = payload["files"]
            saved_speakers = payload["speakers"]
            if not is_object_mapping(saved_files) or not is_object_mapping(saved_speakers):
                self.fail("話者色設定のファイルまたは話者が辞書ではありません")
            saved_file = saved_files["1-alice.flac"]
            saved_speaker = saved_speakers["alice"]
            if not is_object_mapping(saved_file) or not is_object_mapping(saved_speaker):
                self.fail("話者色設定の項目が辞書ではありません")
            self.assertEqual(saved_file["aliases"], aliases)
            self.assertEqual(saved_file["color"], "#123456")
            self.assertEqual(saved_speaker["color"], "#123456")
            mapping = load_speaker_color_map(path)
            self.assertEqual(mapping["1-alice.aac"], "#123456")
            self.assertEqual(mapping["alice"], "#123456")


if __name__ == "__main__":
    unittest.main()
