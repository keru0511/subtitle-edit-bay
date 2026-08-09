from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.runtime_config import DEFAULT_RUNTIME_CONFIG, load_command_runtime_config
from src.runtime_settings import (
    settings_from_config,
    load_runtime_settings,
    settings_to_flat_dict,
)


class RuntimeSettingsTests(unittest.TestCase):
    def test_default_craig_runtime_config_maps_to_typed_settings(self) -> None:
        settings = load_runtime_settings("craig_pipeline", DEFAULT_RUNTIME_CONFIG)

        self.assertEqual(settings.transcription.model, "large-v3")
        self.assertEqual(settings.transcription.device, "cuda")
        self.assertEqual(settings.transcription.compute_type, "float16")
        self.assertEqual(settings.transcription.language, "ja")
        self.assertAlmostEqual(settings.transcription.vad_onset, 0.35)
        self.assertAlmostEqual(settings.transcription.vad_offset, 0.2)
        self.assertTrue(settings.transcription.skip_existing_transcripts)

        self.assertEqual(settings.video_export.width, 1920)
        self.assertEqual(settings.video_export.height, 1080)
        self.assertEqual(settings.video_export.video_codec, "h264_nvenc")
        self.assertEqual(settings.video_export.audio_codec, "copy")
        self.assertEqual(settings.video_export.output_audio_track, "0:a:0")
        self.assertEqual(settings.video_export.nvenc_cq, 18)
        self.assertEqual(settings.video_export.x264_crf, 18)

        self.assertEqual(settings.alignment.alignment_sample_rate, 120)
        self.assertAlmostEqual(settings.alignment.alignment_offset_adjustment, 0.0)
        self.assertTrue(settings.audio_normalize.audio_normalize)
        self.assertAlmostEqual(settings.audio_normalize.audio_target_lufs, -16.0)
        self.assertFalse(settings.silence_cut.cut_no_speech)

    def test_typed_settings_use_same_shared_command_merge_as_runtime_config(self) -> None:
        payload = {
            "shared": {
                "model": "tiny",
                "device": "cpu",
                "compute_type": "int8",
                "language": "ja",
                "width": 1280,
                "height": 720,
                "video_codec": "libx264",
                "audio_codec": "aac",
                "audio_normalize": False,
            },
            "craig_pipeline": {
                "video_codec": "h264_nvenc",
                "audio_codec": "copy",
                "skip_existing_transcripts": False,
                "alignment_offset_adjustment": 0.25,
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "runtime_config.json"
            config_path.write_text(json.dumps(payload), encoding="utf-8")

            merged = load_command_runtime_config("craig_pipeline", config_path)
            settings = load_runtime_settings("craig_pipeline", config_path)

        self.assertEqual(settings.transcription.model, merged["model"])
        self.assertEqual(settings.transcription.device, merged["device"])
        self.assertEqual(settings.video_export.width, merged["width"])
        self.assertEqual(settings.video_export.height, merged["height"])
        self.assertEqual(settings.video_export.video_codec, "h264_nvenc")
        self.assertEqual(settings.video_export.audio_codec, "copy")
        self.assertFalse(settings.transcription.skip_existing_transcripts)
        self.assertFalse(settings.audio_normalize.audio_normalize)
        self.assertAlmostEqual(settings.alignment.alignment_offset_adjustment, 0.25)

    def test_empty_config_keeps_existing_python_fallback_defaults(self) -> None:
        settings = settings_from_config({})

        self.assertEqual(settings.transcription.model, "large-v3")
        self.assertEqual(settings.transcription.device, "cpu")
        self.assertEqual(settings.transcription.compute_type, "int8")
        self.assertEqual(settings.video_export.video_codec, "libx264")
        self.assertEqual(settings.video_export.audio_codec, "copy")
        self.assertAlmostEqual(settings.subtitle_layout.subtitle_max_gap_seconds, 0.32)
        self.assertTrue(settings.audio_normalize.audio_normalize)
        self.assertFalse(settings.silence_cut.cut_no_speech)

    def test_settings_flat_dict_exposes_runtime_config_keys(self) -> None:
        settings = settings_from_config(
            {
                "model": "tiny",
                "device": "cpu",
                "compute_type": "int8",
                "subtitle_font_size": 42,
                "audio_target_lufs": -18.0,
            }
        )

        flat = settings_to_flat_dict(settings)

        self.assertEqual(flat["model"], "tiny")
        self.assertEqual(flat["device"], "cpu")
        self.assertEqual(flat["compute_type"], "int8")
        self.assertEqual(flat["subtitle_font_size"], 42)
        self.assertAlmostEqual(flat["audio_target_lufs"], -18.0)
        self.assertIn("alignment_sample_rate", flat)
        self.assertIn("speech_threshold_db", flat)

    def test_invalid_types_raise_explicit_errors(self) -> None:
        with self.assertRaisesRegex(ValueError, "vad_onset"):
            settings_from_config({"vad_onset": "fast"})

        with self.assertRaisesRegex(ValueError, "audio_normalize"):
            settings_from_config({"audio_normalize": "yes"})

        with self.assertRaisesRegex(ValueError, "width"):
            settings_from_config({"width": 1920.0})


if __name__ == "__main__":
    unittest.main()
