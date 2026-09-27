from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from src.gui_project_editor_controller import ProjectEditorController
from src.subtitle_project import SubtitleProjectError, create_project, load_project, save_project
from src.transcription_project_integration import compose_transcription_project
from tests.typed_case import TypedTestCase
from tests.typed_data import entries, object_dict, required_string, section


class ComposeTranscriptionProjectTests(TypedTestCase):
    def test_merge_preserves_settings_and_renames_colliding_generated_ids(self) -> None:
        preserved: dict[str, object] = {
            "segments": [{"id": "shared", "start": 0.0, "end": 1.0, "text": "original"}],
            "audio_mix": {"master": 0.5},
            "transcription": {"engine": "old"},
        }
        generated: dict[str, object] = {
            "segments": [
                {"id": "shared", "start": 2.0, "end": 3.0, "text": "new"},
                {"id": "new-id", "start": 4.0, "end": 5.0, "text": "newer"},
            ],
            "transcription": {"engine": "new"},
        }
        original_preserved = deepcopy(preserved)
        original_generated = deepcopy(generated)

        with patch("src.transcription_project_integration.uuid4", return_value=SimpleNamespace(hex="abc123456789abcdef")):
            integrated = compose_transcription_project(
                preserved, generated, "merge", assign_layout_rows=lambda segments: segments
            )

        self.assertEqual(
            [segment["id"] for segment in entries(integrated, "segments")],
            ["shared", "transcribed-abc123456789", "new-id"],
        )
        self.assertEqual(integrated["audio_mix"], preserved["audio_mix"])
        self.assertEqual(integrated["transcription"], {"engine": "new"})
        self.assertEqual(preserved, original_preserved)
        self.assertEqual(generated, original_generated)

    def test_replace_uses_generated_segments_and_keeps_existing_settings(self) -> None:
        integrated = compose_transcription_project(
            {"segments": [{"id": "old", "start": 0, "end": 1}], "timeline": {"cuts": [1]}},
            {"segments": [{"id": "new", "start": 2, "end": 3}], "waveforms": [42]},
            "replace",
            assign_layout_rows=lambda segments: segments,
        )
        self.assertEqual([segment["id"] for segment in entries(integrated, "segments")], ["new"])
        self.assertEqual(integrated["timeline"], {"cuts": [1]})
        self.assertEqual(integrated["waveforms"], [42])

    def test_unknown_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            compose_transcription_project({}, {}, "unknown", assign_layout_rows=lambda segments: segments)


class IntegrateTranscriptionResultTests(TypedTestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project_path = self.root / "custom.subtitle-project.json"
        self.generated_path = self.root / "generated.subtitle-project.json"
        video = self.root / "video.mkv"
        self.preserved = create_project(
            video_path=video,
            output_dir=self.root,
            duration_seconds=10.0,
            segments=[{"id": "old", "start": 0.0, "end": 1.0, "text": "old"}],
        )
        section(self.preserved, "subtitle_settings")["font_size"] = 48
        self.generated = create_project(
            video_path=video,
            output_dir=self.root,
            duration_seconds=10.0,
            segments=[{"id": "new", "start": 2.0, "end": 3.0, "text": "new"}],
            transcription={"engine": "generated"},
        )
        save_project(self.project_path, self.preserved)
        save_project(self.generated_path, self.generated)

    def _controller(self) -> ProjectEditorController:
        controller = ProjectEditorController(self.root)
        self.addCleanup(controller.shutdown)
        controller.load(self.project_path)
        return controller

    def test_save_then_adopt_updates_revision_history_and_selection(self) -> None:
        controller = self._controller()
        controller.undo_stack.append({"kind": "segment"})
        original_revision = controller.project_revision
        preserved = deepcopy(object_dict(controller.project))

        integrated = controller.integrate_transcription_result(
            self.generated_path, preserved, self.project_path, "merge"
        )

        self.assertIs(controller.project, integrated)
        self.assertEqual(controller.project_revision, original_revision + 1)
        empty_history: list[dict[str, object]] = []
        self.assertEqual(controller.undo_stack, empty_history)
        self.assertEqual(controller.selected_segment_index, 0)
        self.assertFalse(controller.project_dirty)
        self.assertEqual([segment["id"] for segment in entries(load_project(self.project_path), "segments")], ["old", "new"])
        self.assertEqual(section(controller.project, "subtitle_settings")["font_size"], 48)
        self.assertEqual(section(controller.project, "transcription")["engine"], "generated")

    def test_save_failure_keeps_original_document_and_file(self) -> None:
        def fail_save(*_args: object, **_kwargs: object) -> Path:
            raise OSError("disk full")

        controller = self._controller()
        controller._save_project_fn = fail_save
        original_project = object_dict(controller.project)
        original_revision = controller.project_revision
        original_contents = self.project_path.read_bytes()

        with self.assertRaisesRegex(OSError, "disk full"):
            controller.integrate_transcription_result(
                self.generated_path, deepcopy(original_project), self.project_path, "replace"
            )

        self.assertIs(controller.project, original_project)
        self.assertEqual(controller.project_revision, original_revision)
        self.assertEqual(controller.project_path, str(self.project_path.resolve()))
        self.assertEqual(self.project_path.read_bytes(), original_contents)

    def test_same_video_under_another_path_is_accepted(self) -> None:
        controller = self._controller()
        original_video = Path(required_string(section(self.preserved, "video")["path"]))
        original_video.write_bytes(b"video")
        linked_video = self.root / "linked-video.mkv"
        linked_video.hardlink_to(original_video)
        generated = deepcopy(self.generated)
        section(generated, "video")["path"] = str(linked_video)
        save_project(self.generated_path, generated)

        integrated = controller.integrate_transcription_result(
            self.generated_path, deepcopy(object_dict(controller.project)), self.project_path, "merge"
        )

        self.assertEqual([segment["id"] for segment in entries(integrated, "segments")], ["old", "new"])

    def test_null_generated_transcription_is_normalized(self) -> None:
        controller = self._controller()
        generated = deepcopy(self.generated)
        generated["transcription"] = None
        save_project(self.generated_path, generated)

        integrated = controller.integrate_transcription_result(
            self.generated_path, deepcopy(object_dict(controller.project)), self.project_path, "merge"
        )

        self.assertEqual(section(integrated, "transcription")["context_base_dir"], str(self.root.resolve()))
        self.assertEqual(load_project(self.project_path)["transcription"], integrated["transcription"])

    def test_generated_result_for_other_video_is_rejected(self) -> None:
        controller = self._controller()
        unrelated = deepcopy(self.generated)
        section(unrelated, "video")["path"] = str(self.root / "other-video.mkv")
        save_project(self.generated_path, unrelated)
        original_project = object_dict(controller.project)
        original_contents = self.project_path.read_bytes()

        with self.assertRaisesRegex(SubtitleProjectError, "動画が編集プロジェクトと一致しません"):
            controller.integrate_transcription_result(
                self.generated_path, deepcopy(original_project), self.project_path, "merge"
            )

        self.assertIs(controller.project, original_project)
        self.assertEqual(self.project_path.read_bytes(), original_contents)

    def test_invalid_integrated_project_is_not_saved(self) -> None:
        controller = self._controller()
        original_project = object_dict(controller.project)
        original_contents = self.project_path.read_bytes()
        invalid = deepcopy(original_project)
        invalid_segments = entries(invalid, "segments")
        invalid_segments.append(deepcopy(invalid_segments[0]))

        with patch("src.gui_project_editor_controller.compose_transcription_project", return_value=invalid):
            with self.assertRaisesRegex(SubtitleProjectError, "segment ids must be unique"):
                controller.integrate_transcription_result(
                    self.generated_path, deepcopy(original_project), self.project_path, "merge"
                )

        self.assertIs(controller.project, original_project)
        self.assertEqual(self.project_path.read_bytes(), original_contents)

    def test_saved_noncanonical_settings_do_not_look_like_external_edits(self) -> None:
        controller = self._controller()
        section(controller.project, "subtitle_settings")["outline_color"] = "#abcdef"
        controller.save()
        preserved = deepcopy(object_dict(controller.project))

        integrated = controller.integrate_transcription_result(
            self.generated_path, preserved, self.project_path, "merge"
        )

        self.assertEqual([segment["id"] for segment in entries(integrated, "segments")], ["old", "new"])
        self.assertEqual(section(load_project(self.project_path), "subtitle_settings")["outline_color"], "#ABCDEF")

    def test_externally_changed_project_file_cannot_be_overwritten(self) -> None:
        controller = self._controller()
        preserved = deepcopy(object_dict(controller.project))
        external = deepcopy(preserved)
        entries(external, "segments")[0]["text"] = "edited outside the app"
        save_project(self.project_path, cast(dict[object, object], external))
        external_contents = self.project_path.read_bytes()

        with self.assertRaisesRegex(SubtitleProjectError, "ファイルが変更されました"):
            controller.integrate_transcription_result(self.generated_path, preserved, self.project_path, "merge")

        self.assertIsNotNone(controller.project)
        self.assertEqual(entries(controller.project, "segments")[0]["text"], "old")
        self.assertEqual(self.project_path.read_bytes(), external_contents)

    def test_changed_open_project_cannot_be_overwritten_by_old_result(self) -> None:
        controller = self._controller()
        preserved = deepcopy(object_dict(controller.project))
        entries(controller.project, "segments")[0]["text"] = "edited during transcription"
        original_project = object_dict(controller.project)
        original_contents = self.project_path.read_bytes()

        with self.assertRaises(SubtitleProjectError):
            controller.integrate_transcription_result(self.generated_path, preserved, self.project_path, "merge")

        self.assertIs(controller.project, original_project)
        self.assertEqual(entries(controller.project, "segments")[0]["text"], "edited during transcription")
        self.assertEqual(self.project_path.read_bytes(), original_contents)

    def test_other_project_cannot_receive_stale_result(self) -> None:
        controller = self._controller()
        other_path = self.root / "other.subtitle-project.json"
        with self.assertRaises(SubtitleProjectError):
            controller.integrate_transcription_result(
                self.generated_path, deepcopy(object_dict(controller.project)), other_path, "merge"
            )
        self.assertFalse(other_path.exists())
