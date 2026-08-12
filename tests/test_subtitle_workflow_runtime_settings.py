from __future__ import annotations

import unittest

from src.runtime_settings import settings_from_config
from src.subtitle_workflow import _transcribe_options_with_cli_overrides


class SubtitleWorkflowRuntimeSettingsTests(unittest.TestCase):
    def test_transcribe_options_use_typed_settings_by_default(self) -> None:
        settings = settings_from_config(
            {
                "model": "tiny",
                "device": "cpu",
                "compute_type": "int8",
                "alignment_offset_adjustment": 0.125,
                "skip_existing_transcripts": True,
                "subtitle_font_size": 42,
            }
        )

        options = _transcribe_options_with_cli_overrides(
            settings,
            alignment_offset_adjustment=None,
            skip_existing_transcripts=None,
        )

        self.assertEqual(options["model"], "tiny")
        self.assertEqual(options["device"], "cpu")
        self.assertEqual(options["compute_type"], "int8")
        self.assertEqual(options["alignment_offset_adjustment"], 0.125)
        self.assertTrue(options["skip_existing_transcripts"])
        self.assertEqual(options["subtitle_font_size"], 42)

    def test_transcribe_cli_values_override_typed_settings(self) -> None:
        settings = settings_from_config(
            {
                "alignment_offset_adjustment": 0.125,
                "skip_existing_transcripts": True,
            }
        )

        options = _transcribe_options_with_cli_overrides(
            settings,
            alignment_offset_adjustment=0.5,
            skip_existing_transcripts=False,
        )

        self.assertEqual(options["alignment_offset_adjustment"], 0.5)
        self.assertFalse(options["skip_existing_transcripts"])


if __name__ == "__main__":
    unittest.main()
