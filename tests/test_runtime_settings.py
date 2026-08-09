from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.runtime_config import DEFAULT_RUNTIME_CONFIG, load_command_runtime_config
from src.runtime_settings import (
    DEFAULT_POSTPROCESS_WORKERS,
    configured_render_settings,
    load_runtime_settings,
    render_runtime_options,
    settings_from_config,
    settings_to_flat_dict,
    transcribe_runtime_options,
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
        self.assertEqual(settings.pipeline.postprocess_workers, 4)

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
                "postprocess_workers": 2,
            },
            "craig_pipeline": {
                "video_codec": "h264_nvenc",
                "audio_codec": "copy",
                "skip_existing_transcripts": False,
                "alignment_offset_adjustment": 0.25,
                "postprocess_workers": 3,
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
        self.assertEqual(settings.pipeline.postprocess_workers, 3)

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
        self.assertEqual(settings.pipeline.postprocess_workers, DEFAULT_POSTPROCESS_WORKERS)

    def test_settings_flat_dict_exposes_runtime_config_keys(self) -> None:
        settings = settings_from_config(
            {
                "model": "tiny",
                "device": "cpu",
                "compute_type": "int8",
                "subtitle_font_size": 42,
                "audio_target_lufs": -18.0,
                "postprocess_workers": 2,
            }
        )

        flat = settings_to_flat_dict(settings)

        self.assertEqual(flat["model"], "tiny")
        self.assertEqual(flat["device"], "cpu")
        self.assertEqual(flat["compute_type"], "int8")
        self.assertEqual(flat["subtitle_font_size"], 42)
        self.assertAlmostEqual(flat["audio_target_lufs"], -18.0)
        self.assertEqual(flat["postprocess_workers"], 2)
        self.assertIn("alignment_sample_rate", flat)
        self.assertIn("speech_threshold_db", flat)

    def test_transcribe_runtime_options_match_workflow_keyword_names(self) -> None:
        settings = settings_from_config(
            {
                "model": "tiny",
                "device": "cpu",
                "compute_type": "int8",
                "alignment_sample_rate": 80,
                "alignment_offset_adjustment": 0.125,
                "skip_existing_transcripts": False,
                "postprocess_workers": 2,
                "subtitle_font_size": 44,
                "subtitle_max_gap_seconds": 0.2,
            }
        )

        options = transcribe_runtime_options(settings)

        self.assertEqual(options["model"], "tiny")
        self.assertEqual(options["device"], "cpu")
        self.assertEqual(options["compute_type"], "int8")
        self.assertEqual(options["alignment_sample_rate"], 80)
        self.assertAlmostEqual(options["alignment_offset_adjustment"], 0.125)
        self.assertFalse(options["skip_existing_transcripts"])
        self.assertEqual(options["postprocess_workers"], 2)
        self.assertEqual(options["subtitle_font_size"], 44)
        self.assertAlmostEqual(options["subtitle_max_gap_seconds"], 0.2)
        self.assertNotIn("video_codec", options)
        self.assertNotIn("audio_normalize", options)

    def test_render_runtime_options_match_video_render_keyword_names(self) -> None:
        settings = settings_from_config(
            {
                "video_codec": "h264_nvenc",
                "audio_codec": "copy",
                "output_audio_track": "0:a:2",
                "nvenc_cq": 20,
                "x264_crf": 19,
                "audio_normalize": False,
                "cut_no_speech": True,
                "speech_threshold_db": "-42dB",
            }
        )

        options = render_runtime_options(settings)

        self.assertEqual(options["video_codec"], "h264_nvenc")
        self.assertEqual(options["audio_codec"], "copy")
        self.assertEqual(options["output_audio_track"], "0:a:2")
        self.assertEqual(options["nvenc_cq"], 20)
        self.assertEqual(options["x264_crf"], 19)
        self.assertFalse(options["audio_normalize"])
        self.assertTrue(options["cut_no_speech"])
        self.assertEqual(options["speech_threshold_db"], "-42dB")
        self.assertNotIn("model", options)
        self.assertNotIn("subtitle_font_size", options)

    def test_configured_render_settings_preserve_existing_only_if_configured_behavior(self) -> None:
        config = {
            "video_codec": "h264_nvenc",
            "audio_codec": "copy",
            "audio_normalize": False,
            "speech_threshold_db": "-42dB",
        }
        settings = settings_from_config(config)

        persisted = configured_render_settings(settings, config)

        self.assertEqual(
            persisted,
            {
                "video_codec": "h264_nvenc",
                "audio_codec": "copy",
                "audio_normalize": False,
            },
        )
        self.assertNotIn("speech_threshold_db", persisted)
        self.assertNotIn("output_audio_track", persisted)
        self.assertNotIn("nvenc_cq", persisted)

    def test_invalid_types_raise_explicit_errors(self) -> None:
        with self.assertRaisesRegex(ValueError, "vad_onset"):
            settings_from_config({"vad_onset": "fast"})

        with self.assertRaisesRegex(ValueError, "audio_normalize"):
            settings_from_config({"audio_normalize": "yes"})

        with self.assertRaisesRegex(ValueError, "width"):
            settings_from_config({"width": 1920.0})

        with self.assertRaisesRegex(ValueError, "postprocess_workers"):
            settings_from_config({"postprocess_workers": 2.5})


if __name__ == "__main__":
    unittest.main()
