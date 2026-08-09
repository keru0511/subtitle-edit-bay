from __future__ import annotations

import unittest

from src.gui_state_base import build_gui_runtime_config
from src.runtime_settings import gui_runtime_config_updates


class GuiRuntimeSettingsTests(unittest.TestCase):
    def test_gui_runtime_config_updates_selects_only_typed_gui_keys(self) -> None:
        shared, craig = gui_runtime_config_updates(
            {
                "model": "tiny",
                "device": "cpu",
                "video_codec": "libx264",
                "audio_normalize": False,
                "alignment_offset_adjustment": 0.125,
                "postprocess_workers": 2,
                "unknown_setting": "ignored",
            }
        )

        self.assertEqual(shared, {"model": "tiny", "device": "cpu"})
        self.assertEqual(
            craig,
            {
                "video_codec": "libx264",
                "audio_normalize": False,
                "alignment_offset_adjustment": 0.125,
                "postprocess_workers": 2,
            },
        )

    def test_build_gui_runtime_config_uses_typed_boundary_for_workers(self) -> None:
        base_config = {
            "shared": {
                "model": "large-v3",
                "device": "cuda",
                "compute_type": "float16",
            },
            "craig_pipeline": {
                "video": "old.mkv",
                "audio_file": ["old.flac"],
                "output_dir": "old-output",
                "reference_track": "0:a:0",
                "target": "old-target",
                "track_color": ["old=#000000"],
                "video_codec": "h264_nvenc",
            },
        }
        settings = {
            "model": "tiny",
            "device": "cpu",
            "compute_type": "int8",
            "video_codec": "libx264",
            "audio_normalize": False,
            "postprocess_workers": 2,
            "alignment_offset_adjustment": 0.25,
            "unknown_setting": "ignored",
        }
        speakers = [{"track_key": "craig:alice", "color": "#FF0000"}]

        resolved = build_gui_runtime_config(base_config, settings, speakers)

        self.assertEqual(resolved["shared"]["model"], "tiny")
        self.assertEqual(resolved["shared"]["device"], "cpu")
        self.assertEqual(resolved["shared"]["compute_type"], "int8")
        self.assertEqual(resolved["craig_pipeline"]["video_codec"], "libx264")
        self.assertFalse(resolved["craig_pipeline"]["audio_normalize"])
        self.assertEqual(resolved["craig_pipeline"]["postprocess_workers"], 2)
        self.assertAlmostEqual(resolved["craig_pipeline"]["alignment_offset_adjustment"], 0.25)
        self.assertEqual(resolved["craig_pipeline"]["track_color"], ["craig:alice=#FF0000"])
        self.assertNotIn("unknown_setting", resolved["shared"])
        self.assertNotIn("unknown_setting", resolved["craig_pipeline"])
        for one_shot_key in ("video", "audio_file", "output_dir", "reference_track", "target"):
            self.assertNotIn(one_shot_key, resolved["craig_pipeline"])


if __name__ == "__main__":
    unittest.main()
