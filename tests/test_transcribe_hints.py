from __future__ import annotations

import sys
import unittest

from src.transcribe import build_whisperx_command


class TranscribeHintCommandTests(unittest.TestCase):
    def test_hints_are_omitted_by_default(self) -> None:
        command = build_whisperx_command("voice.wav", "out")

        self.assertNotIn("--initial_prompt", command)
        self.assertNotIn("--hotwords", command)

    def test_initial_prompt_and_hotwords_are_passed_to_whisperx(self) -> None:
        command = build_whisperx_command(
            "voice.wav",
            "out",
            initial_prompt="ゲーム: Splatoon 3",
            hotwords=["ナワバリバトル", "スプラシューター"],
        )

        self.assertEqual(command[0], sys.executable)
        self.assertIn("--initial_prompt", command)
        self.assertEqual(command[command.index("--initial_prompt") + 1], "ゲーム: Splatoon 3")
        self.assertIn("--hotwords", command)
        self.assertEqual(command[command.index("--hotwords") + 1], "ナワバリバトル, スプラシューター")

    def test_hotwords_are_cleaned_and_deduplicated(self) -> None:
        command = build_whisperx_command(
            "voice.wav",
            "out",
            initial_prompt="  ",
            hotwords=["", "ナワバリバトル", "ナワバリバトル", "  ガチエリア  "],
        )

        self.assertNotIn("--initial_prompt", command)
        self.assertEqual(command[command.index("--hotwords") + 1], "ナワバリバトル, ガチエリア")


if __name__ == "__main__":
    unittest.main()
