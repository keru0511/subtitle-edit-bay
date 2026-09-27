"""Qtの動的プロパティを未検証のobjectとして受け取る。"""

from __future__ import annotations

from typing import cast

from PySide6.QtCore import QObject


def qt_property_value(source: object, name: str) -> object:
    """プロパティの型を使う側で検証できるようにする。"""

    if not isinstance(source, QObject):
        raise AssertionError(f"{name} の所有者はQObjectである必要があります")
    return cast(object, source.property(name))
