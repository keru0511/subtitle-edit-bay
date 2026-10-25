"""PySide6のデコレーターとシグナルを登録動作を保ったまま型付けする。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, ParamSpec, Protocol, TypeVar, overload

_P = ParamSpec("_P")
_R = TypeVar("_R")
_T_co = TypeVar("_T_co", covariant=True)
_S_co = TypeVar("_S_co", covariant=True)
_T1 = TypeVar("_T1")
_T2 = TypeVar("_T2")
_T3 = TypeVar("_T3")


class _SlotFactory(Protocol):
    def __call__(
        self,
        *types: type[object] | str,
        result: type[object] | str | None = None,
    ) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]: ...


class _PropertyDescriptor(Protocol[_T_co]):
    @overload
    def __get__(self, instance: None, owner: type[object] | None = None) -> _PropertyDescriptor[_T_co]: ...

    @overload
    def __get__(self, instance: object, owner: type[object] | None = None) -> _T_co: ...


class _PropertyFactory(Protocol):
    def __call__(
        self,
        type_: type[object] | str,
        *,
        notify: object = None,
        constant: bool = False,
    ) -> Callable[[Callable[_P, _R]], _PropertyDescriptor[_R]]: ...


class _SignalDescriptor(Protocol[_S_co]):
    @overload
    def __get__(self, instance: None, owner: type[object] | None = None) -> _SignalDescriptor[_S_co]: ...

    @overload
    def __get__(self, instance: object, owner: type[object] | None = None) -> _S_co: ...


class _Signal0(Protocol):
    def emit(self) -> None: ...

    def connect(self, slot: Callable[[], object], type: Qt.ConnectionType = ...) -> object: ...


class _Signal1(Protocol[_T1]):
    def emit(self, value: _T1) -> None: ...

    @overload
    def connect(self, slot: Callable[[_T1], object], type: Qt.ConnectionType = ...) -> object: ...

    @overload
    def connect(self, slot: Callable[[], object], type: Qt.ConnectionType = ...) -> object: ...


class _Signal2(Protocol[_T1, _T2]):
    def emit(self, first: _T1, second: _T2) -> None: ...

    @overload
    def connect(self, slot: Callable[[_T1, _T2], object], type: Qt.ConnectionType = ...) -> object: ...

    @overload
    def connect(self, slot: Callable[[_T1], object], type: Qt.ConnectionType = ...) -> object: ...

    @overload
    def connect(self, slot: Callable[[], object], type: Qt.ConnectionType = ...) -> object: ...


class _Signal3(Protocol[_T1, _T2, _T3]):
    def emit(self, first: _T1, second: _T2, third: _T3) -> None: ...

    @overload
    def connect(self, slot: Callable[[_T1, _T2, _T3], object], type: Qt.ConnectionType = ...) -> object: ...

    @overload
    def connect(self, slot: Callable[[_T1, _T2], object], type: Qt.ConnectionType = ...) -> object: ...

    @overload
    def connect(self, slot: Callable[[_T1], object], type: Qt.ConnectionType = ...) -> object: ...

    @overload
    def connect(self, slot: Callable[[], object], type: Qt.ConnectionType = ...) -> object: ...


class _SignalFactory(Protocol):
    @overload
    def __call__(self) -> _SignalDescriptor[_Signal0]: ...

    @overload
    def __call__(self, first: type[_T1]) -> _SignalDescriptor[_Signal1[_T1]]: ...

    @overload
    def __call__(self, first: type[_T1], second: type[_T2]) -> _SignalDescriptor[_Signal2[_T1, _T2]]: ...

    @overload
    def __call__(
        self, first: type[_T1], second: type[_T2], third: type[_T3]
    ) -> _SignalDescriptor[_Signal3[_T1, _T2, _T3]]: ...


if TYPE_CHECKING:
    from PySide6.QtCore import Qt

    Slot: _SlotFactory
    Property: _PropertyFactory
    Signal: _SignalFactory
else:
    from PySide6.QtCore import Property as Property, Signal as Signal, Slot as Slot
