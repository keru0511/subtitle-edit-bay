"""Qt model integration tests for clip-scoped subtitle browsing."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.gui_models import SubtitleClipFilterModel, SubtitleListModel


class SubtitleClipFilterModelTests(unittest.TestCase):
    _app: QApplication | None = None

    @classmethod
    def setUpClass(cls) -> None:
        if QApplication.instance() is None:
            cls._app = QApplication([])

    def setUp(self) -> None:
        self.source = SubtitleListModel()
        self.source.set_segments(
            [
                {"id": "first", "start": 0.0, "end": 10.0, "text": "before"},
                {"id": "middle", "start": 10.0, "end": 16.0, "text": "inside"},
                {"id": "last", "start": 16.0, "end": 23.0, "text": "after"},
            ]
        )
        self.proxy = SubtitleClipFilterModel(self.source)

    def test_no_filter_leaves_every_source_row(self) -> None:
        self.assertEqual(self.proxy.rowCount(), 3)
        self.assertEqual(self.proxy.mapToSource(self.proxy.index(1, 0)).row(), 1)

    def test_half_open_range_and_index_mapping(self) -> None:
        self.proxy.set_clip_window(10.0, 16.0)
        self.assertEqual(self.proxy.rowCount(), 1)
        self.assertEqual(self.proxy.mapToSource(self.proxy.index(0, 0)).row(), 1)
        self.assertEqual(self.proxy.mapFromSource(self.source.index(1, 0)).row(), 0)
        self.assertFalse(self.proxy.mapFromSource(self.source.index(0, 0)).isValid())

    def test_untranscribed_second_asset_shows_no_inherited_subtitles(self) -> None:
        self.proxy.set_clip_window(0.0, 23.0, has_transcript=False)
        self.assertEqual(self.proxy.rowCount(), 0)
        self.proxy.set_clip_window(None, None)
        self.assertEqual(self.proxy.rowCount(), 3)

    def test_source_list_updates_without_mutating_underlying_subtitles(self) -> None:
        self.proxy.set_clip_window(10.0, 16.0)
        incoming = [
            {"id": "first", "start": 0.0, "end": 10.0, "text": "before"},
            {"id": "middle", "start": 10.0, "end": 16.0, "text": "corrected"},
            {"id": "last", "start": 16.0, "end": 23.0, "text": "after"},
        ]
        self.source.set_segments(incoming)
        self.assertEqual(self.proxy.rowCount(), 1)
        self.assertEqual(self.proxy.mapToSource(self.proxy.index(0, 0)).row(), 1)
        self.assertEqual(
            self.source.data(self.source.index(1, 0), SubtitleListModel.TextRole),
            "corrected",
        )

    def test_time_edits_refilter_rows_when_start_or_end_crosses_clip_boundary(self) -> None:
        self.proxy.set_clip_window(10.0, 16.0)
        self.source.set_segments([
            {"id": "first", "start": 0.0, "end": 12.0, "text": "now overlaps"},
            {"id": "middle", "start": 16.0, "end": 18.0, "text": "now outside"},
            {"id": "last", "start": 16.0, "end": 23.0, "text": "after"},
        ])
        self.assertEqual(self.proxy.rowCount(), 1)
        self.assertEqual(self.proxy.mapToSource(self.proxy.index(0, 0)).row(), 0)


if __name__ == "__main__":
    unittest.main()
