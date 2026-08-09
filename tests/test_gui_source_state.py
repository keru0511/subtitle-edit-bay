from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src import gui_state_base
from src.gui_source_state import SourceSelection, build_speaker_entries_from_files


class GuiSourceStateTests(unittest.TestCase):
    def test_source_selection_serializes_audio_files_as_list(self) -> None:
        selection = SourceSelection(
            video="video.mkv",
            output_dir="out",
            audio_files=("001-alice.flac", "002-bob.wav"),
        )

        self.assertEqual(
            selection.to_dict(),
            {
                "video": "video.mkv",
                "output_dir": "out",
                "audio_files": ["001-alice.flac", "002-bob.wav"],
            },
        )

    def test_source_selection_helpers_are_reexported_for_existing_callers(self) -> None:
        self.assertIs(gui_state_base.SourceSelection, SourceSelection)
        self.assertIs(gui_state_base.build_speaker_entries_from_files, build_speaker_entries_from_files)
        self.assertIn(".flac", gui_state_base.AUDIO_EXTENSIONS)
        self.assertIn(".mkv", gui_state_base.VIDEO_EXTENSIONS)

    def test_build_speaker_entries_from_files_filters_sorts_and_uses_color_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bob = root / "002-bob.wav"
            alice = root / "001-alice.flac"
            ignored = root / "notes.txt"
            bob.write_bytes(b"")
            alice.write_bytes(b"")
            ignored.write_text("not audio", encoding="utf-8")
            color_config = root / "speaker_colors.json"
            color_config.write_text(
                json.dumps(
                    {
                        "speakers": {"alice": "#112233"},
                        "files": {"002-bob.wav": "#445566"},
                    }
                ),
                encoding="utf-8",
            )

            entries = build_speaker_entries_from_files([bob, ignored, alice], color_config)

        self.assertEqual([entry["name"] for entry in entries], ["alice", "bob"])
        self.assertEqual([entry["file_name"] for entry in entries], ["001-alice.flac", "002-bob.wav"])
        self.assertEqual([entry["color"] for entry in entries], ["#112233", "#445566"])
        self.assertEqual([entry["track_key"] for entry in entries], ["craig:alice", "craig:bob"])


if __name__ == "__main__":
    unittest.main()
