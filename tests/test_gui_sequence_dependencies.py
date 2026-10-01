"""シーケンス窓口がバックエンドの非公開属性へ依存しないことを確認する。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal

from src.gui_project_editor_controller import ProjectEditorController
from src.gui import EditBayBackend
from src.gui_sequence_facade import SequenceDependencies, SequenceFacade
from src.video_sequence import VideoSequence
from tests.typed_case import TypedTestCase


class SequenceBackendStub(QObject):
    sequenceChanged = Signal()

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.running = False
        self.workspace_root = root


class SequenceDependenciesTests(TypedTestCase):
    def test_asset_validation_uses_explicit_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "sample.mp4"
            video.write_bytes(b"sample")
            backend = SequenceBackendStub(root)
            validated: list[tuple[Path, set[str], str]] = []
            statuses: list[tuple[str, str]] = []
            changes: list[str] = []

            def validate(source: Path, streams: set[str], label: str) -> tuple[bool, str]:
                validated.append((source, streams, label))
                return False, "動画素材として利用できません"

            facade = SequenceFacade(
                cast(EditBayBackend, backend),
                SequenceDependencies(
                    local_path=lambda value: Path(str(value)),
                    validate_media_file=validate,
                    normalize_source_path=lambda value: value.casefold(),
                    set_status=lambda message, stage: statuses.append((message, stage)),
                ),
            )
            facade.sequenceChanged.connect(lambda: changes.append(facade.sequenceError))

            backend.running = True
            self.assertFalse(facade.addSequenceAsset(str(video)))
            self.assertEqual(validated, [])

            backend.running = False
            self.assertFalse(facade.addSequenceAsset(str(video)))
            self.assertEqual(validated, [(video, {"video"}, "sequence動画素材")])
            self.assertEqual(statuses[-1], ("動画素材として利用できません", "CHECK"))
            self.assertEqual(changes[-1], facade.sequenceError)

    def test_asset_addition_and_duplicate_check_use_explicit_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "sample.mp4"
            video.write_bytes(b"sample")
            backend = SequenceBackendStub(root)
            controller = ProjectEditorController(root)
            self.addCleanup(controller.shutdown)
            controller.project = {"sequence": VideoSequence().to_json()}
            normalized: list[str] = []
            statuses: list[tuple[str, str]] = []

            def normalize(value: str) -> str:
                normalized.append(value)
                return value.casefold()

            facade = SequenceFacade(
                cast(EditBayBackend, backend),
                SequenceDependencies(
                    local_path=lambda value: Path(str(value)),
                    validate_media_file=lambda source, streams, label: (True, ""),
                    normalize_source_path=normalize,
                    set_status=lambda message, stage: statuses.append((message, stage)),
                ),
            )
            facade.bind_project_editor(controller)

            with patch("src.gui_sequence_facade.probe_media_duration", return_value=3.0):
                self.assertTrue(facade.addSequenceAsset(str(video)))
                self.assertFalse(facade.addSequenceAsset(str(video)))

            self.assertEqual(len(controller.sequence_model().assets), 1)
            self.assertTrue(controller.project_dirty)
            self.assertEqual(normalized, [str(video), str(video), str(video)])
            self.assertEqual([stage for _, stage in statuses], ["EDIT", "CHECK"])
            self.assertEqual(facade.sequenceError, "同じ動画素材は既にmedia binにあります")


if __name__ == "__main__":
    unittest.main()
