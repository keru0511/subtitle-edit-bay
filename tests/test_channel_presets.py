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
    def test_absolute_media_paths_are_excluded_on_every_platform(self) -> None:
        preset = create_channel_preset(
            "paths",
            {
                "export": {
                    "windows_path": "C:/private/video.mp4",
                    "windows_backslash_path": r"C:\private\video.mp4",
                    "unc_path": r"\\server\share\video.mp4",
                    "posix_path": "/private/video.mp4",
                    "relative_path": "assets/video.mp4",
                }
            },
            categories={"export"},
        )

        export = preset.categories["export"]
        self.assertNotIn("windows_path", export)
        self.assertNotIn("windows_backslash_path", export)
        self.assertNotIn("unc_path", export)
        self.assertNotIn("posix_path", export)
        self.assertEqual(export["relative_path"], "assets/video.mp4")

    def test_roundtrip_diff_partial_apply_and_secret_path_exclusion(self) -> None:
        current = {
            "subtitle": {"font_size": 50},
            "audio": {"channels": [{"track_key": "a", "gain": 0}, {"track_key": "manual", "gain": 4}]},
            "short": {
                "clips": [
                    {"segment_id": "s1", "start": 0, "manual_override": True},
                    {"segment_id": "manual-only", "start": 4, "manual_override": True},
                ]
            },
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
        self.assertEqual(
            {channel["track_key"] for channel in result.settings["audio"]["channels"]},
            {"a", "manual"},
        )

        short_preset = create_channel_preset(
            "short-only",
            {"short": {"clips": [{"segment_id": "s1", "start": 1}]}},
            categories={"short"},
        )
        short_result = apply_channel_preset(current, short_preset)
        self.assertEqual(
            {clip["segment_id"] for clip in short_result.settings["short"]["clips"]},
            {"s1", "manual-only"},
        )

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
