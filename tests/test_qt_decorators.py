"""Qtデコレーターの型付き窓口がメタオブジェクト登録を保つことを確認する。"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from src.qt_decorators import Property, Slot
from tests.typed_case import TypedTestCase


class _SlotProbe(QObject):
    changed = Signal()

    @Slot(int, "QVariantMap", result=bool)
    def accept(self, index: int, fields: dict[str, object]) -> bool:
        return index > 0 and bool(fields)

    @Property(int, notify=changed)
    def total(self) -> int:
        return 3


class QtDecoratorTests(TypedTestCase):
    def test_typed_aliases_register_slot_and_property(self) -> None:
        probe = _SlotProbe()
        self.assertGreaterEqual(probe.metaObject().indexOfSlot("accept(int,QVariantMap)"), 0)
        self.assertGreaterEqual(probe.metaObject().indexOfProperty("total"), 0)
        self.assertTrue(probe.accept(1, {"value": True}))
        self.assertEqual(probe.total, 3)
