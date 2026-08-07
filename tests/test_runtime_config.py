import json
import tempfile
import unittest
from pathlib import Path

from src.runtime_config import (
    load_command_runtime_config,
    load_runtime_config,
    resolve_bool_option,
    resolve_list_option,
    resolve_option,
)


class RuntimeConfigTests(unittest.TestCase):
    def test_load_runtime_config_reads_utf8_sig_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "runtime_config.json"
            config_path.write_text(json.dumps({"shared": {"device": "cuda"}}), encoding="utf-8-sig")

            loaded = load_runtime_config(config_path)

            self.assertEqual(loaded["shared"]["device"], "cuda")

    def test_load_command_runtime_config_merges_shared_and_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "runtime_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "shared": {"device": "cuda", "compute_type": "float16"},
                        "batch": {"device": "cpu", "video_codec": "h264_nvenc"},
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_command_runtime_config("batch", config_path)

            self.assertEqual(loaded["device"], "cpu")
            self.assertEqual(loaded["compute_type"], "float16")
            self.assertEqual(loaded["video_codec"], "h264_nvenc")

    def test_default_craig_config_contains_audio_postprocess_settings(self) -> None:
        loaded = load_command_runtime_config("craig_pipeline")

        self.assertTrue(loaded["audio_normalize"])
        self.assertFalse(loaded["cut_no_speech"])
        self.assertEqual(loaded["audio_target_lufs"], -16.0)
        self.assertEqual(loaded["no_speech_min_seconds"], 1.2)
        self.assertEqual(loaded["speech_threshold_db"], "-40dB")
        self.assertEqual(loaded["nvenc_cq"], 18)
        self.assertEqual(loaded["x264_crf"], 18)
        self.assertEqual(loaded["subtitle_font_size"], 50)
        self.assertEqual(loaded["subtitle_outline_color"], "#000000")
        self.assertEqual(loaded["subtitle_outline_thickness"], 3)
        self.assertEqual(loaded["subtitle_volume_scale_percent"], 20.0)

    def test_resolve_option_prefers_cli_value(self) -> None:
        resolved = resolve_option("cpu", {"device": "cuda"}, "device", "int8")
        self.assertEqual(resolved, "cpu")

    def test_resolve_list_option_reads_array(self) -> None:
        resolved = resolve_list_option(None, {"audio_track": ["0:a:1", "0:a:3"]}, "audio_track", ["0:a:0"])
        self.assertEqual(resolved, ["0:a:1", "0:a:3"])

    def test_resolve_bool_option_uses_default_when_missing(self) -> None:
        resolved = resolve_bool_option(None, {}, "audio_normalize", True)
        self.assertTrue(resolved)


if __name__ == "__main__":
    unittest.main()
