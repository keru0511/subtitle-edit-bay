"""シーケンス窓口がバックエンドの非公開属性へ依存しないことを確認する。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.gui_sequence_facade import SequenceDependencies, SequenceFacade


class SequenceBackendStub(QObject):
    sequenceChanged = Signal()

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.running = False
        self.workspace_root = root


class SequenceDependenciesTests(unittest.TestCase):
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
                backend,
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


if __name__ == "__main__":
    unittest.main()
