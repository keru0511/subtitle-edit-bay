from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.channel_presets import (
    ChannelPresetStore,
    apply_channel_preset,
    create_channel_preset,
    diff_channel_preset,
)


class ChannelPresetTests(unittest.TestCase):
    def test_roundtrip_diff_partial_apply_and_secret_path_exclusion(self) -> None:
        current = {
            "subtitle": {"font_size": 50},
            "audio": {"channels": [{"track_key": "a", "gain": 0}]},
            "short": {"clips": [{"segment_id": "s1", "start": 0, "manual_override": True}]},
            "export": {"video_path": "C:/private/video.mp4", "codec": "h264"},
            "api_token": "secret",
        }
        preset = create_channel_preset(
            "実況用",
            {**current, "subtitle": {"font_size": 70}, "audio": {"channels": [{"track_key": "a", "gain": 2}, {"track_key": "missing", "gain": 1}]}},
            categories={"subtitle", "audio", "export"},
        )
        self.assertNotIn("private", str(preset.to_json()))
        self.assertNotIn("secret", str(preset.to_json()))
        self.assertIn("subtitle", diff_channel_preset(current, preset))
        result = apply_channel_preset(current, preset, categories={"subtitle", "audio"})
        self.assertEqual(result.settings["subtitle"]["font_size"], 70)
        self.assertTrue(result.warnings)
        self.assertNotIn("short", result.changed_categories)

    def test_store_rename_delete_and_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ChannelPresetStore(Path(temp_dir) / "presets.json")
            store.add(create_channel_preset("one", {"subtitle": {"font_size": 50}}))
            store.rename("one", "renamed")
            store.default_name = "renamed"
            store.save()
            restored = ChannelPresetStore(store.path)
            self.assertIn("renamed", restored.presets)
            restored.delete("renamed")
            self.assertNotIn("renamed", restored.presets)


if __name__ == "__main__":
    unittest.main()
