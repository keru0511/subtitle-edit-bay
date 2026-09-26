import json
import tempfile
import unittest
from pathlib import Path
from collections.abc import Mapping

from src.data_boundary import is_object_mapping

from src.runtime_config_schema import validate_runtime_config_payload

from src.runtime_config import (
    load_command_runtime_config,
    load_runtime_config,
    resolve_bool_option,
    resolve_list_option,
    resolve_option,
)


def config_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload)


def section(payload: Mapping[str, object], key: str) -> Mapping[object, object]:
    value = payload[key]
    assert is_object_mapping(value), f"{key} must be an object"
    return value


class RuntimeConfigTests(unittest.TestCase):
    def test_load_runtime_config_reads_utf8_sig_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "runtime_config.json"
            config_path.write_text(config_json({"shared": {"device": "cuda"}}), encoding="utf-8-sig")

            loaded = load_runtime_config(config_path)

            self.assertEqual(section(loaded, "shared")["device"], "cuda")

    def test_load_command_runtime_config_merges_shared_and_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "runtime_config.json"
            config_path.write_text(
                config_json(
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

    def test_shared_schema_accepts_gui_model_and_rejects_invalid_nullable_setting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "runtime_config.json"
            config_path.write_text(
                config_json(
                    {
                        "shared": {"codex_model": "gpt-fast"},
                        "craig_pipeline": {"reference_audio": None},
                    }
                ),
                encoding="utf-8",
            )
            loaded = load_runtime_config(config_path)
            self.assertEqual(section(loaded, "shared")["codex_model"], "gpt-fast")
            self.assertIsNone(section(loaded, "craig_pipeline")["reference_audio"])

            config_path.write_text(
                config_json({"craig_pipeline": {"reference_audio": {}}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SystemExit, "reference_audio"):
                load_runtime_config(config_path)

    def test_schema_preserves_supported_null_transcription_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "runtime_config.json"
            config_path.write_text(
                config_json(
                    {
                        "shared": {"language": None, "vad_onset": None, "vad_offset": None},
                        "pipeline": {"min_speakers": None, "max_speakers": None},
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_runtime_config(config_path)

            self.assertIsNone(section(loaded, "shared")["language"])
            self.assertIsNone(section(loaded, "shared")["vad_onset"])
            self.assertIsNone(section(loaded, "shared")["vad_offset"])
            self.assertIsNone(section(loaded, "pipeline")["min_speakers"])
            self.assertIsNone(section(loaded, "pipeline")["max_speakers"])

    def test_schema_preserves_null_optional_clips(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "runtime_config.json"
            config_path.write_text(
                config_json({"batch": {"op_file": None, "ed_file": None}}),
                encoding="utf-8",
            )

            loaded = load_command_runtime_config("batch", config_path)

            self.assertIsNone(loaded["op_file"])
            self.assertIsNone(loaded["ed_file"])

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

    def test_schema_preserves_unknown_data_without_sharing_mutable_values(self) -> None:
        unknown_items: list[object] = ["retained"]
        payload: dict[str, object] = {
            "shared": {"custom": unknown_items},
            "custom_section": {"enabled": True},
        }
        preserved = validate_runtime_config_payload(payload, discard_unknown=False)
        unknown_items.append("changed")
        expected = ["retained"]
        self.assertEqual(section(preserved, "shared")["custom"], expected)
        self.assertTrue(section(preserved, "custom_section")["enabled"])
        filtered = validate_runtime_config_payload(payload, discard_unknown=True)
        self.assertNotIn("custom_section", filtered)
        self.assertNotIn("custom", section(filtered, "shared"))

    def test_schema_retains_non_object_sections_only_for_application_loading(self) -> None:
        payload: dict[str, object] = {"shared": None, "batch": "ignored"}
        self.assertEqual(validate_runtime_config_payload(payload, discard_unknown=False), payload)
        with self.assertRaisesRegex(ValueError, "section must be an object"):
            validate_runtime_config_payload(payload, discard_unknown=True)
        with self.assertRaisesRegex(ValueError, "root must be an object"):
            validate_runtime_config_payload([], discard_unknown=False)

    def test_schema_rejects_invalid_numeric_values_and_array_elements(self) -> None:
        invalid_settings: tuple[dict[str, object], ...] = (
            {"width": True},
            {"audio_target_lufs": False},
            {"audio_target_lufs": None},
            {"audio_target_lufs": float("nan")},
            {"audio_target_lufs": float("inf")},
            {"min_speakers": True},
            {"vad_onset": False},
            {"vad_onset": float("-inf")},
            {"audio_track": ["0:a:0", 1]},
            {"audio_track": ("0:a:0",)},
        )
        for settings in invalid_settings:
            with self.subTest(settings=settings):
                with self.assertRaisesRegex(ValueError, "invalid type"):
                    validate_runtime_config_payload({"batch": settings}, discard_unknown=False)

    def test_resolve_option_keeps_explicit_null_and_uses_default_only_for_missing_key(self) -> None:
        settings: dict[str, str | None] = {"device": None}
        self.assertIsNone(resolve_option(None, settings, "device", "cpu"))
        resolved = resolve_option(None, settings, "model", "small")
        self.assertEqual(resolved, "small")

    def test_resolve_list_and_bool_reject_invalid_shapes(self) -> None:
        with self.assertRaisesRegex(SystemExit, "JSON array"):
            resolve_list_option(None, {"audio_track": ("0:a:0",)}, "audio_track")
        with self.assertRaisesRegex(SystemExit, "true or false"):
            resolve_bool_option(None, {"enabled": "true"}, "enabled", False)
        expected = ["1", "False"]
        self.assertEqual(resolve_list_option(None, {"items": [1, False]}, "items"), expected)

    def test_resolve_option_prefers_cli_value(self) -> None:
        resolved = resolve_option("cpu", {"device": "cuda"}, "device", "int8")
        self.assertEqual(resolved, "cpu")

    def test_resolve_list_option_reads_array(self) -> None:
        resolved = resolve_list_option(None, {"audio_track": ["0:a:1", "0:a:3"]}, "audio_track", ["0:a:0"])
        expected = ["0:a:1", "0:a:3"]
        self.assertEqual(resolved, expected)

    def test_resolve_bool_option_uses_default_when_missing(self) -> None:
        resolved = resolve_bool_option(None, {}, "audio_normalize", True)
        self.assertTrue(resolved)


if __name__ == "__main__":
    unittest.main()
