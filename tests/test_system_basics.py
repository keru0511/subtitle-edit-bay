import sys
import tempfile
import unittest
from pathlib import Path
from typing import Mapping

from src.data_boundary import is_object_list, is_string_object_mapping
from src.gui_state import build_gui_render_command, build_gui_transcribe_command
from src.gui_state_base import build_gui_runtime_config
from src.runtime_config import DEFAULT_RUNTIME_CONFIG, load_command_runtime_config, load_runtime_config
from src.runtime_dependencies import RuntimeDependencyStatus, format_dependency_error
from src.subtitle_project import (
    derive_ass_path,
    derive_project_path,
    derive_render_path,
    load_project,
    project_to_transcript,
    save_project,
    create_project,
)
from tests.typed_case import TypedTestCase


def _mapping(value: object) -> Mapping[str, object]:
    assert is_string_object_mapping(value)
    return value


def _list(value: object) -> list[object]:
    assert is_object_list(value)
    return value


class SystemBehaviorTests(TypedTestCase):
    def test_default_runtime_config_resolves_core_transcription_settings(self) -> None:
        config = load_runtime_config(DEFAULT_RUNTIME_CONFIG)
        shared = _mapping(config.get("shared"))
        self.assertEqual(shared.get("model"), "large-v3")
        self.assertEqual(shared.get("language"), "ja")
        devices: set[str] = {"cuda", "cpu"}
        compute_types: set[str] = {"float16", "int8"}
        self.assertIn(shared.get("device"), devices)
        self.assertIn(shared.get("compute_type"), compute_types)

        craig = load_command_runtime_config("craig_pipeline", DEFAULT_RUNTIME_CONFIG)
        self.assertEqual(craig["model"], shared["model"])
        self.assertEqual(craig["language"], shared["language"])
        self.assertIn("input_root", craig)
        self.assertIn("export_root", craig)
        self.assertIn("video_codec", craig)
        self.assertIn("skip_existing_transcripts", craig)

    def test_runtime_dependency_status_reports_missing_tools_consistently(self) -> None:
        status = RuntimeDependencyStatus(ffmpeg=True, ffprobe=True, whisperx=False, cuda=False)

        self.assertFalse(status.ready)
        self.assertEqual(status.missing(), ["whisperx"])
        self.assertEqual(status.missing(require_whisperx=False), [])
        self.assertIn("whisperx", _list(status.to_dict()["missing"]))
        self.assertIn("python -m pip install whisperx", format_dependency_error(status))

        cuda_status = RuntimeDependencyStatus(ffmpeg=True, ffprobe=True, whisperx=True, cuda=False)
        self.assertTrue(cuda_status.ready)
        self.assertIn("CUDA-enabled PyTorch", format_dependency_error(cuda_status, device="cuda"))
        self.assertEqual(format_dependency_error(cuda_status, device="cpu"), "")

    def test_gui_runtime_config_does_not_persist_one_shot_source_selection(self) -> None:
        base_config = {
            "shared": {
                "model": "tiny",
                "device": "cpu",
                "compute_type": "int8",
                "language": "ja",
            },
            "craig_pipeline": {
                "video": "old.mkv",
                "audio_file": ["old.flac"],
                "output_dir": "old-output",
                "reference_track": "0:a:0",
                "target": "old-target",
                "track_color": ["old=#000000"],
            },
        }
        settings = {
            "model": "large-v3",
            "device": "cuda",
            "compute_type": "float16",
            "language": "ja",
            "video_codec": "libx264",
            "audio_normalize": False,
            "audio_target_lufs": -18.0,
            "alignment_offset_adjustment": 0.25,
        }
        speakers = [{"track_key": "craig:alice", "color": "#FF0000"}]

        resolved = build_gui_runtime_config(base_config, settings, speakers)
        shared = _mapping(resolved["shared"])
        craig = _mapping(resolved["craig_pipeline"])

        self.assertEqual(shared["model"], "large-v3")
        self.assertEqual(shared["device"], "cuda")
        self.assertEqual(shared["compute_type"], "float16")
        self.assertEqual(craig["video_codec"], "libx264")
        self.assertFalse(craig["audio_normalize"])
        self.assertEqual(craig["track_color"], ["craig:alice=#FF0000"])
        for one_shot_key in ("video", "audio_file", "output_dir", "reference_track", "target"):
            self.assertNotIn(one_shot_key, craig)

    def test_gui_commands_target_expected_workflow_entrypoints(self) -> None:
        transcribe = build_gui_transcribe_command(
            "config.json",
            video="input.mkv",
            audio_files=["1-alice.flac", "2-bob.flac"],
            output_dir="out",
            reference_audio="1-alice.flac",
            reference_track="0:a:1",
            alignment_offset_adjustment=0.125,
        )

        self.assertEqual(transcribe[0], sys.executable)
        self.assertEqual(transcribe[1:5], ["-u", "-m", "src.subtitle_workflow", "transcribe"])
        self.assertIn("--run", transcribe)
        self.assertEqual(transcribe.count("--audio-file"), 2)
        self.assertIn("--reference-audio", transcribe)
        self.assertIn("--reference-track", transcribe)
        self.assertIn("--alignment-offset-adjustment", transcribe)

        render = build_gui_render_command("config.json", project_path="session.subtitle-project.json")
        self.assertEqual(render[0], sys.executable)
        self.assertEqual(render[1:5], ["-u", "-m", "src.subtitle_workflow", "render"])
        self.assertIn("--project", render)
        self.assertIn("--config", render)
        self.assertEqual(render[-1], "--run")

    def test_project_roundtrip_preserves_editable_transcript_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "recording.mkv"
            audio = root / "1-alice.flac"
            output = root / "out"
            video.write_bytes(b"video")
            audio.write_bytes(b"audio")

            project = create_project(
                video_path=video,
                output_dir=output,
                duration_seconds=12.5,
                audio_sources=[{"name": "alice", "path": str(audio)}],
                speakers=[
                    {
                        "name": "alice",
                        "style": "A",
                        "track_key": "craig:alice",
                        "file_name": audio.name,
                        "path": str(audio),
                        "color": "#FFFFFF",
                    }
                ],
                segments=[
                    {
                        "id": "segment-1",
                        "start": 1.0,
                        "end": 2.0,
                        "text": "こんにちは",
                        "speaker": "A",
                    }
                ],
                transcription={"model": "large-v3", "language": "ja"},
            )
            project_path = derive_project_path(video, output)

            save_project(project_path, project)
            loaded = load_project(project_path)
            transcript = project_to_transcript(loaded)

            self.assertEqual(project_path.name, "recording.subtitle-project.json")
            self.assertEqual(derive_ass_path(project_path).name, "recording.edited.ass")
            self.assertEqual(derive_render_path(project_path).name, "recording.edited.subtitled.mp4")
            self.assertEqual(loaded["project_type"], "subtitle-edit-project")
            self.assertEqual(_mapping(loaded["video"])["path"], str(video.resolve()))
            loaded_segments = _list(loaded["segments"])
            transcript_segments = _list(transcript["segments"])
            self.assertEqual(transcript_segments, [loaded_segments[0]])
            self.assertEqual(_mapping(transcript_segments[0])["text"], "こんにちは")
            self.assertEqual(_mapping(transcript_segments[0])["speaker"], "A")


if __name__ == "__main__":
    unittest.main()
