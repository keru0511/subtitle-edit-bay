import tempfile
import unittest
from pathlib import Path

from src.subtitle_project import create_project, derive_project_path, load_project, save_project
from src.subtitle_workflow import transcribe_to_project


class SubtitleProjectSafetyTests(unittest.TestCase):
    def test_transcription_refuses_to_overwrite_existing_edits_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "export"
            output.mkdir()
            video = root / "game.mkv"
            audio = root / "1-alice.flac"
            video.write_bytes(b"video")
            audio.write_bytes(b"audio")
            path = derive_project_path(video, output)
            project = create_project(
                video_path=video,
                output_dir=output,
                segments=[{
                    "start": 0,
                    "end": 1,
                    "text": "手動編集を保持",
                    "speaker": "Oz",
                    "manual_text": True,
                }],
            )
            save_project(path, project)

            with self.assertRaisesRegex(SystemExit, "--overwrite-project"):
                transcribe_to_project(
                    video_path=str(video),
                    audio_files=[str(audio)],
                    output_dir=str(output),
                    reference_audio=str(audio),
                )

            self.assertEqual(load_project(path)["segments"][0]["text"], "手動編集を保持")


if __name__ == "__main__":
    unittest.main()
