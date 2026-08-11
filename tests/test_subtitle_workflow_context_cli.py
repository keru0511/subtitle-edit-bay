import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


class SubtitleWorkflowContextCliTests(unittest.TestCase):
    def test_transcribe_phase_passes_cli_context_file_to_context_entrypoint(self) -> None:
        import src.subtitle_workflow as subtitle_workflow

        with TemporaryDirectory() as temp_dir:
            context_file = Path(temp_dir) / "context.json"
            context_file.write_text('{"game_title": "Splatoon 3"}', encoding="utf-8")
            argv = [
                "subtitle_workflow",
                "transcribe",
                "--video",
                "video.mkv",
                "--audio-file",
                "1-alice.flac",
                "--output-dir",
                temp_dir,
                "--transcription-context-file",
                str(context_file),
                "--run",
            ]

            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch("src.subtitle_workflow.load_command_runtime_config", return_value={}),
                mock.patch("src.subtitle_workflow.settings_from_config", return_value=object()),
                mock.patch("src.subtitle_workflow.transcribe_runtime_options", return_value={"device": "cpu"}),
                mock.patch("src.subtitle_workflow.check_runtime_dependencies", return_value=object()),
                mock.patch("src.subtitle_workflow.format_dependency_error", return_value=None),
                mock.patch("src.subtitle_workflow.parse_track_color_args", return_value={}),
                mock.patch("src.subtitle_workflow.configured_render_settings", return_value={}),
                mock.patch(
                    "src.subtitle_workflow.transcribe_to_project_with_context",
                    return_value=Path(temp_dir) / "video.editbay.json",
                ) as transcribe,
            ):
                subtitle_workflow.main()

        self.assertEqual(transcribe.call_args.kwargs["transcription_context"].game_title, "Splatoon 3")
        self.assertEqual(transcribe.call_args.kwargs["video_path"], "video.mkv")
        self.assertEqual(transcribe.call_args.kwargs["audio_files"], ["1-alice.flac"])
        self.assertEqual(transcribe.call_args.kwargs["device"], "cpu")

    def test_transcribe_plan_mode_does_not_require_context_file(self) -> None:
        import src.subtitle_workflow as subtitle_workflow

        argv = [
            "subtitle_workflow",
            "transcribe",
            "--video",
            "video.mkv",
            "--audio-file",
            "1-alice.flac",
            "--output-dir",
            "out",
            "--transcription-context-file",
            "missing.json",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch("src.subtitle_workflow.load_command_runtime_config", return_value={}),
            mock.patch("src.subtitle_workflow.transcribe_to_project_with_context") as transcribe,
        ):
            subtitle_workflow.main()

        transcribe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
