from __future__ import annotations

import unittest

from src.gui_runtime_state import build_gui_command as build_legacy_gui_command
from src.gui_state import build_gui_command, build_gui_transcribe_command


class GuiStateCommandTests(unittest.TestCase):
    def test_gui_transcribe_command_uses_subtitle_workflow(self) -> None:
        command = build_gui_command(
            "runtime.json",
            video="video.mkv",
            audio_files=("alice.flac", "bob.wav"),
            output_dir="out",
            reference_audio="alice.flac",
            reference_track="0:a:1",
            alignment_offset_adjustment=0.25,
        )

        self.assertIn("src.subtitle_workflow", command)
        self.assertIn("transcribe", command)
        self.assertNotIn("src.craig_pipeline", command)
        self.assertEqual(command[-1], "--run")
        self.assertIn("runtime.json", command)
        self.assertIn("video.mkv", command)
        self.assertIn("alice.flac", command)
        self.assertIn("bob.wav", command)
        self.assertIn("0:a:1", command)
        self.assertIn("0.25", command)

    def test_named_transcribe_helper_matches_gui_command(self) -> None:
        direct = build_gui_transcribe_command(
            "runtime.json",
            video="video.mkv",
            audio_files=("alice.flac",),
            output_dir="out",
        )
        public = build_gui_command(
            "runtime.json",
            video="video.mkv",
            audio_files=("alice.flac",),
            output_dir="out",
        )

        self.assertEqual(public, direct)

    def test_transcribe_command_supports_video_audio_track(self) -> None:
        command = build_gui_transcribe_command(
            "runtime.json",
            video="video.mkv",
            audio_files=(),
            output_dir="out",
            video_audio_track="0:a:0",
        )

        self.assertIn("--video-audio-track", command)
        self.assertNotIn("--audio-file", command)

    def test_legacy_runtime_command_helper_stays_available(self) -> None:
        command = build_legacy_gui_command(
            "runtime.json",
            video="video.mkv",
            audio_files=("alice.flac",),
            output_dir="out",
        )

        self.assertIn("src.craig_pipeline", command)
        self.assertNotIn("src.subtitle_workflow", command)


if __name__ == "__main__":
    unittest.main()
