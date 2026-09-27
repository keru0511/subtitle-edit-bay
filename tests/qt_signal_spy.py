"""Qtのシグナル監視を型付きSignalと組み合わせるためのテスト用窓口。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Protocol, TypeVar, overload

    from PySide6.QtCore import SignalInstance
    from PySide6.QtTest import QSignalSpy as _QSignalSpy

    from src.qt_decorators import _Signal0, _Signal1, _Signal2, _Signal3

    _T1 = TypeVar("_T1")
    _T2 = TypeVar("_T2")
    _T3 = TypeVar("_T3")

    class _QSignalSpyFactory(Protocol):
        @overload
        def __call__(self, signal: SignalInstance) -> _QSignalSpy: ...

        @overload
        def __call__(self, signal: _Signal0) -> _QSignalSpy: ...

        @overload
        def __call__(self, signal: _Signal1[_T1]) -> _QSignalSpy: ...

        @overload
        def __call__(self, signal: _Signal2[_T1, _T2]) -> _QSignalSpy: ...

        @overload
        def __call__(self, signal: _Signal3[_T1, _T2, _T3]) -> _QSignalSpy: ...

    QSignalSpy: _QSignalSpyFactory
else:
    from PySide6.QtTest import QSignalSpy as QSignalSpy
