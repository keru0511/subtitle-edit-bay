from __future__ import annotations

import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

from src.channel_presets import (
    ChannelPreset,
    ChannelPresetError,
    ChannelPresetStore,
    apply_channel_preset,
    create_channel_preset,
    diff_channel_preset,
)
from src.data_boundary import is_object_list, is_object_mapping
from tests.typed_case import TypedTestCase


def _mapping(value: object) -> Mapping[object, object]:
    if not is_object_mapping(value):
        raise AssertionError("設定値はオブジェクトである必要があります")
    return value


def _list(value: object) -> list[object]:
    if not is_object_list(value):
        raise AssertionError("設定値は配列である必要があります")
    return value


class ChannelPresetTests(TypedTestCase):
    def test_invalid_category_shapes_are_rejected(self) -> None:
        with self.assertRaisesRegex(ChannelPresetError, "category must be an object"):
            create_channel_preset("invalid", {"subtitle": 42}, categories={"subtitle"})
        with self.assertRaisesRegex(ChannelPresetError, "category must be an object"):
            ChannelPreset.from_json({"schema_version": 1, "name": "invalid", "categories": {"subtitle": []}})

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
            {
                **current,
                "subtitle": {"font_size": 70},
                "audio": {"channels": [{"track_key": "a", "gain": 2}, {"track_key": "missing", "gain": 1}]},
            },
            categories={"subtitle", "audio", "export"},
        )
        self.assertNotIn("private", str(preset.to_json()))
        self.assertNotIn("secret", str(preset.to_json()))
        self.assertIn("subtitle", diff_channel_preset(current, preset))
        result = apply_channel_preset(current, preset, categories={"subtitle", "audio"})
        self.assertEqual(_mapping(result.settings["subtitle"])["font_size"], 70)
        self.assertTrue(result.warnings)
        self.assertNotIn("short", result.changed_categories)
        audio = _mapping(result.settings["audio"])
        self.assertEqual(
            {_mapping(channel)["track_key"] for channel in _list(audio["channels"])},
            {"a", "manual"},
        )

        short_preset = create_channel_preset(
            "short-only",
            {"short": {"clips": [{"segment_id": "s1", "start": 1}]}},
            categories={"short"},
        )
        short_result = apply_channel_preset(current, short_preset)
        short = _mapping(short_result.settings["short"])
        self.assertEqual(
            {_mapping(clip)["segment_id"] for clip in _list(short["clips"])},
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

    def test_store_rejects_malformed_preset_array(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "presets.json"
            path.write_text('{"presets": {}}', encoding="utf-8")
            with self.assertRaisesRegex(ChannelPresetError, "presets must be an array"):
                ChannelPresetStore(path)


if __name__ == "__main__":
    unittest.main()
