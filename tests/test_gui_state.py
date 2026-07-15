import json
import tempfile
import unittest
from pathlib import Path

from src.gui_state import (
    SourceSelection,
    build_gui_command,
    build_gui_runtime_config,
    build_speaker_entries_from_files,
    write_gui_runtime_config,
)


class GuiStateTests(unittest.TestCase):
    def test_source_selection_starts_empty_and_serializes_audio_files_as_list(self) -> None:
        selection = SourceSelection()

        self.assertEqual(selection.video, "")
        self.assertEqual(selection.output_dir, "")
        self.assertEqual(selection.audio_files, ())
        self.assertEqual(selection.to_dict()["audio_files"], [])

    def test_build_speaker_entries_uses_file_color_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio = root / "1-speaker-a.flac"
            audio.write_bytes(b"audio")
            colors = root / "colors.json"
            colors.write_text(json.dumps({
                "files": {"1-speaker-a.aac": {"color": "#FFD966", "aliases": ["1-speaker-a.flac"]}}
            }), encoding="utf-8")

            speakers = build_speaker_entries_from_files([audio], colors)

            self.assertEqual(speakers[0]["color"], "#FFD966")
            self.assertEqual(speakers[0]["track_key"], "craig:speaker-a")

    def test_build_speaker_entries_accepts_files_from_different_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "one" / "1-speaker-a.flac"
            second = root / "two" / "2-speaker-b.aac"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_bytes(b"audio")
            second.write_bytes(b"audio")

            speakers = build_speaker_entries_from_files([second, first])

            self.assertEqual([speaker["file_name"] for speaker in speakers], ["1-speaker-a.flac", "2-speaker-b.aac"])
            self.assertEqual([speaker["name"] for speaker in speakers], ["speaker-a", "speaker-b"])

    def test_build_gui_runtime_config_does_not_persist_source_paths(self) -> None:
        payload = build_gui_runtime_config(
            {
                "shared": {"model": "small"},
                "craig_pipeline": {
                    "video": "old.mkv",
                    "audio_dir": "old-audio",
                    "audio_file": ["old.flac"],
                    "output_dir": "old-output",
                    "reference_audio": "old.flac",
                    "reference_track": "0:a:1",
                    "target": "old-project",
                },
            },
            {
                "model": "large-v3",
                "nvenc_cq": 17,
                "x264_crf": 16,
                "cut_no_speech": True,
                "alignment_offset_adjustment": -0.125,
            },
            [{"track_key": "craig:speaker-a", "color": "#FFD966"}],
        )

        self.assertEqual(payload["shared"]["model"], "large-v3")
        self.assertEqual(payload["shared"]["nvenc_cq"], 17)
        self.assertEqual(payload["shared"]["x264_crf"], 16)
        self.assertTrue(payload["craig_pipeline"]["cut_no_speech"])
        self.assertEqual(payload["craig_pipeline"]["alignment_offset_adjustment"], -0.125)
        self.assertEqual(payload["craig_pipeline"]["track_color"], ["craig:speaker-a=#FFD966"])
        for key in ("video", "audio_dir", "audio_file", "output_dir", "reference_audio", "reference_track", "target"):
            self.assertNotIn(key, payload["craig_pipeline"])

    def test_write_config_and_build_source_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = write_gui_runtime_config(Path(temp_dir) / "gui" / "config.json", {"shared": {}})
            command = build_gui_command(
                config_path,
                video="game.mkv",
                audio_files=["1-speaker-a.flac", "2-speaker-b.flac"],
                output_dir="export",
                reference_audio="1-speaker-a.flac",
                reference_track="0:a:1",
                alignment_offset_adjustment=0.25,
            )

            self.assertTrue(config_path.exists())
            self.assertEqual(command.count("--audio-file"), 2)
            self.assertIn("--video", command)
            self.assertIn("--output-dir", command)
            self.assertIn("--reference-track", command)
            self.assertIn("0.25", command)
            self.assertEqual(command[-1], "--run")

    def test_build_source_command_requires_all_inputs(self) -> None:
        with self.assertRaises(ValueError):
            build_gui_command("gui.json", video="", audio_files=[], output_dir="")


if __name__ == "__main__":
    unittest.main()