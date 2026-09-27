import sys
import unittest
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest import mock
from src.transcription_context import TranscriptionContext
from tests.typed_case import TypedTestCase


def _call_kwargs(target: mock.MagicMock) -> Mapping[str, object]:
    call = target.call_args
    if call is None:
        raise AssertionError("expected a call")
    return cast(Mapping[str, object], call.kwargs)


class SubtitleWorkflowContextCliTests(TypedTestCase):
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

            runtime_options: dict[str, object] = {"device": "cpu"}
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch("src.subtitle_workflow.load_command_runtime_config", return_value=dict[str, object]()),
                mock.patch("src.subtitle_workflow.settings_from_config", return_value=object()),
                mock.patch("src.subtitle_workflow.transcribe_runtime_options", return_value=runtime_options),
                mock.patch("src.subtitle_workflow.check_runtime_dependencies", return_value=object()),
                mock.patch("src.subtitle_workflow.format_dependency_error", return_value=None),
                mock.patch("src.subtitle_workflow.parse_track_color_args", return_value=dict[str, object]()),
                mock.patch("src.subtitle_workflow.configured_render_settings", return_value=dict[str, object]()),
                mock.patch(
                    "src.subtitle_workflow.transcribe_to_project_with_context",
                    return_value=Path(temp_dir) / "video.editbay.json",
                ) as transcribe,
            ):
                subtitle_workflow.main()

        call_kwargs = _call_kwargs(transcribe)
        context = call_kwargs["transcription_context"]
        if not isinstance(context, TranscriptionContext):
            self.fail("transcription context must be parsed")
        self.assertEqual(context.game_title, "Splatoon 3")
        self.assertEqual(call_kwargs["video_path"], "video.mkv")
        self.assertEqual(call_kwargs["audio_files"], ["1-alice.flac"])
        self.assertEqual(call_kwargs["device"], "cpu")
        self.assertIsNone(call_kwargs["render_output_dir"])
        self.assertIsNone(call_kwargs["context_base_dir"])

    def test_gui_command_keeps_empty_export_separate_from_work_and_project_paths(self) -> None:
        import src.subtitle_workflow as subtitle_workflow
        from src.gui_state import build_gui_transcribe_command

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_path = root / "projects" / "edit.subtitle-project.json"
            work = project_path.parent / ".edit.work"
            command = build_gui_transcribe_command(
                root / "config.json", video="video.mkv", audio_files=["1-alice.flac"],
                output_dir=str(work), project_path=str(project_path), render_output_dir="",
                context_base_dir=str(project_path.parent),
            )
            with (
                mock.patch.object(sys, "argv", ["subtitle_workflow", *command[4:]]),
                mock.patch("src.subtitle_workflow.load_command_runtime_config", return_value=dict[str, object]()),
                mock.patch("src.subtitle_workflow.check_runtime_dependencies", return_value=object()),
                mock.patch("src.subtitle_workflow.format_dependency_error", return_value=None),
                mock.patch("src.subtitle_workflow.transcribe_to_project_with_context", return_value=project_path) as transcribe,
            ):
                subtitle_workflow.main()
            call_kwargs = _call_kwargs(transcribe)
            self.assertEqual(call_kwargs["output_dir"], str(work))
            self.assertEqual(call_kwargs["project_path"], str(project_path))
            self.assertEqual(call_kwargs["render_output_dir"], "")
            self.assertEqual(call_kwargs["context_base_dir"], str(project_path.parent))

    def test_transcribe_phase_accepts_video_audio_track_without_audio_files(self) -> None:
        import src.subtitle_workflow as subtitle_workflow

        with TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "video.mkv"
            video.write_bytes(b"video")
            argv = [
                "subtitle_workflow",
                "transcribe",
                "--video",
                str(video),
                "--video-audio-track",
                "0:a:0",
                "--output-dir",
                temp_dir,
                "--run",
            ]
            runtime_options: dict[str, object] = {"device": "cpu"}
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch("src.subtitle_workflow.load_command_runtime_config", return_value=dict[str, object]()),
                mock.patch("src.subtitle_workflow.settings_from_config", return_value=object()),
                mock.patch("src.subtitle_workflow.transcribe_runtime_options", return_value=runtime_options),
                mock.patch("src.subtitle_workflow.check_runtime_dependencies", return_value=object()),
                mock.patch("src.subtitle_workflow.format_dependency_error", return_value=None),
                mock.patch("src.subtitle_workflow.parse_track_color_args", return_value=dict[str, object]()),
                mock.patch("src.subtitle_workflow.configured_render_settings", return_value=dict[str, object]()),
                mock.patch(
                    "src.subtitle_workflow.transcribe_to_project_with_context",
                    return_value=Path(temp_dir) / "video.editbay.json",
                ) as transcribe,
            ):
                subtitle_workflow.main()

        call_kwargs = _call_kwargs(transcribe)
        self.assertEqual(call_kwargs["video_audio_track"], "0:a:0")
        self.assertEqual(call_kwargs["audio_files"], [])

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
            mock.patch("src.subtitle_workflow.load_command_runtime_config", return_value=dict[str, object]()),
            mock.patch("src.subtitle_workflow.transcribe_to_project_with_context") as transcribe,
        ):
            subtitle_workflow.main()

        transcribe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
