"""字幕とショート動画のQMLリストモデル。"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Qt

from .subtitle_line_count import segment_editor_text


class SubtitleListModel(QAbstractListModel):
    SegmentIdRole = Qt.ItemDataRole.UserRole + 1
    StartRole = SegmentIdRole + 1
    EndRole = SegmentIdRole + 2
    TextRole = SegmentIdRole + 3
    SpeakerRole = SegmentIdRole + 4
    LayoutRowRole = SegmentIdRole + 5
    FontScaleRole = SegmentIdRole + 6
    FontFamilyRole = SegmentIdRole + 7
    EditorTextRole = SegmentIdRole + 8

    _ROLE_NAMES = {
        SegmentIdRole: b"segmentId",
        StartRole: b"start",
        EndRole: b"end",
        TextRole: b"text",
        SpeakerRole: b"speaker",
        LayoutRowRole: b"layoutRow",
        FontFamilyRole: b"subtitleFontFamily",
        FontScaleRole: b"subtitleFontScale",
        EditorTextRole: b"editorText",
    }

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._segments: list[dict[str, Any]] = []

    def roleNames(self) -> dict[int, bytes]:
        return self._ROLE_NAMES

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._segments)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._segments):
            return None
        segment = self._segments[index.row()]
        if role == self.SegmentIdRole:
            return str(segment["id"])
        if role == self.StartRole:
            return float(segment["start"])
        if role == self.EndRole:
            return float(segment["end"])
        if role == self.TextRole:
            return str(segment.get("text", ""))
        if role == self.EditorTextRole:
            return segment_editor_text(segment)
        if role == self.SpeakerRole:
            return str(segment.get("speaker", ""))
        if role == self.LayoutRowRole:
            return int(segment.get("layout_row", 0))
        if role == self.FontScaleRole:
            return float(segment.get("subtitle_font_scale", 1.0))
        if role == self.FontFamilyRole:
            return str(segment.get("subtitle_font_family", ""))
        return None

    def set_segments(self, segments: list[dict[str, Any]]) -> None:
        incoming = list(segments)
        old_ids = [str(item["id"]) for item in self._segments]
        new_ids = [str(item["id"]) for item in incoming]

        if old_ids == new_ids:
            changed = [index for index, (old, new) in enumerate(zip(self._segments, incoming)) if old != new]
            self._segments = incoming
            if changed:
                range_start = range_end = changed[0]
                for index in changed[1:]:
                    if index == range_end + 1:
                        range_end = index
                        continue
                    self.dataChanged.emit(
                        self.index(range_start, 0),
                        self.index(range_end, 0),
                        list(self._ROLE_NAMES),
                    )
                    range_start = range_end = index
                self.dataChanged.emit(
                    self.index(range_start, 0),
                    self.index(range_end, 0),
                    list(self._ROLE_NAMES),
                )
            return

        if len(new_ids) == len(old_ids) + 1:
            insert_at = next(
                (index for index, item in enumerate(new_ids) if index >= len(old_ids) or old_ids[index] != item),
                len(old_ids),
            )
            if old_ids == new_ids[:insert_at] + new_ids[insert_at + 1 :]:
                self.beginInsertRows(QModelIndex(), insert_at, insert_at)
                self._segments = incoming
                self.endInsertRows()
                self.dataChanged.emit(
                    self.index(0, 0),
                    self.index(len(incoming) - 1, 0),
                    list(self._ROLE_NAMES),
                )
                return

        if len(old_ids) == len(new_ids) + 1:
            remove_at = next(
                (index for index, item in enumerate(old_ids) if index >= len(new_ids) or new_ids[index] != item),
                len(new_ids),
            )
            if new_ids == old_ids[:remove_at] + old_ids[remove_at + 1 :]:
                self.beginRemoveRows(QModelIndex(), remove_at, remove_at)
                self._segments = incoming
                self.endRemoveRows()
                if incoming:
                    self.dataChanged.emit(
                        self.index(0, 0),
                        self.index(len(incoming) - 1, 0),
                        list(self._ROLE_NAMES),
                    )
                return

        if len(old_ids) == len(new_ids) and set(old_ids) == set(new_ids):
            first_mismatch = next(index for index, item in enumerate(new_ids) if old_ids[index] != item)
            moves = [
                (old_ids.index(new_ids[first_mismatch]), first_mismatch),
                (first_mismatch, new_ids.index(old_ids[first_mismatch])),
            ]
            for source, destination in moves:
                candidate = list(old_ids)
                moved = candidate.pop(source)
                candidate.insert(destination, moved)
                if candidate != new_ids:
                    continue
                destination_child = destination + 1 if source < destination else destination
                self.beginMoveRows(
                    QModelIndex(),
                    source,
                    source,
                    QModelIndex(),
                    destination_child,
                )
                self._segments = incoming
                self.endMoveRows()
                if incoming:
                    self.dataChanged.emit(
                        self.index(0, 0),
                        self.index(len(incoming) - 1, 0),
                        list(self._ROLE_NAMES),
                    )
                return

        self.beginResetModel()
        self._segments = incoming
        self.endResetModel()


class ShortVideoClipListModel(QAbstractListModel):
    ClipDataRole = Qt.ItemDataRole.UserRole + 1

    _ROLE_NAMES = {
        ClipDataRole: b"clipData",
    }

    def __init__(
        self,
        count_resolver: Callable[[], int],
        data_resolver: Callable[[int], dict[str, Any]],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._count_resolver = count_resolver
        self._data_resolver = data_resolver
        self._count = 0

    def roleNames(self) -> dict[int, bytes]:
        return self._ROLE_NAMES

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else self._count

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role != self.ClipDataRole or not index.isValid() or not 0 <= index.row() < self._count:
            return None
        return self._data_resolver(index.row())

    def refresh(self) -> None:
        incoming_count = max(0, int(self._count_resolver()))
        if incoming_count != self._count:
            self.beginResetModel()
            self._count = incoming_count
            self.endResetModel()
            return
        if self._count:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(self._count - 1, 0),
                [self.ClipDataRole],
            )
