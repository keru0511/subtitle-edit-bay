from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.gui_source_selection_controller import SourceSelectionController


class SourceSelectionControllerTests(unittest.TestCase):
    @staticmethod
    def _validator(source: Path, required_streams: set[str], _label: str) -> tuple[bool, str]:
        extension = source.suffix.lower()
        if extension == ".mkv":
            return "video" in required_streams, ""
        if extension == ".flac":
            return "audio" in required_streams, ""
        return False, "unsupported"

    def test_selection_builds_speakers_and_keeps_audio_order_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "game.mkv"
            bob = root / "2-bob.flac"
            alice = root / "1-alice.flac"
            output = root / "export"
            for path in (video, bob, alice):
                path.write_bytes(b"source")
            output.mkdir()

            controller = SourceSelectionController(root)
            video_update = controller.set_video_file(
                video,
                ffprobe_available=True,
                validator=self._validator,
            )
            audio_update = controller.set_audio_files(
                [bob, alice],
                False,
                ffprobe_available=True,
                validator=self._validator,
            )
            output_update = controller.set_output_directory(output)

            self.assertTrue(video_update.accepted)
            self.assertTrue(audio_update.accepted)
            self.assertTrue(output_update.accepted)
            self.assertEqual(
                controller.source_selection.to_dict(),
                {
                    "video": str(video.resolve()),
                    "output_dir": str(output.resolve()),
                    "audio_files": [str(alice.resolve()), str(bob.resolve())],
                },
            )
            self.assertEqual([item["name"] for item in controller.speakers], ["alice", "bob"])
            self.assertEqual(controller.audio_tracks, [{"selector": "", "label": "自動検出（推奨）"}])

    def test_drop_validation_selects_one_video_and_appends_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video_one = root / "one.mkv"
            video_two = root / "two.mkv"
            audio = root / "1-alice.flac"
            unsupported = root / "notes.txt"
            for path in (video_one, video_two, audio):
                path.write_bytes(b"source")
            unsupported.write_text("ignore", encoding="utf-8")

            controller = SourceSelectionController(root)
            result = controller.import_dropped_source_files(
                [video_one, video_two, audio, unsupported, root / "missing.flac"],
                ffprobe_available=True,
                validator=self._validator,
            )

            self.assertTrue(result.accepted)
            self.assertEqual(result.skipped_videos, 1)
            self.assertEqual(result.ignored_count, 2)
            self.assertEqual(len(result.updates), 2)
            self.assertEqual(controller.source_selection.video, str(video_one.resolve()))
            self.assertEqual(controller.source_selection.audio_files, (str(audio.resolve()),))

    def test_audio_track_probe_publishes_success_and_falls_back_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            video = Path(temporary) / "game.mkv"
            video.write_bytes(b"video")

            def probe(_path: str) -> list[dict[str, object]]:
                return [
                    {"codec_name": "aac", "channels": 2, "tags": {"title": "game"}},
                    {"codec_name": "opus", "channels": 1},
                ]

            controller = SourceSelectionController(temporary, audio_stream_probe=probe)
            tracks, error = controller.probe_audio_tracks(str(video), ffprobe_available=True)

            self.assertEqual(error, "")
            self.assertEqual(
                tracks,
                [
                    {"selector": "", "label": "自動検出（推奨）"},
                    {"selector": "0:a:0", "label": "0:a:0  game"},
                    {"selector": "0:a:1", "label": "0:a:1  opus / 1ch"},
                ],
            )

            def failing_probe(_path: str) -> list[dict[str, object]]:
                raise ValueError("bad ffprobe output")

            controller = SourceSelectionController(temporary, audio_stream_probe=failing_probe)
            tracks, error = controller.probe_audio_tracks(str(video), ffprobe_available=True)
            self.assertEqual(tracks, [{"selector": "", "label": "自動検出（推奨）"}])
            self.assertIn("動画音声トラックを取得できません", error)

    def test_invalid_selection_does_not_mutate_owned_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            controller = SourceSelectionController(temporary)
            update = controller.set_video_file(
                Path(temporary) / "not-video.txt",
                ffprobe_available=False,
            )

            self.assertFalse(update.accepted)
            self.assertEqual(controller.source_selection.to_dict(), {"video": "", "output_dir": "", "audio_files": []})
            self.assertEqual(controller.speakers, [])


if __name__ == "__main__":
    unittest.main()
