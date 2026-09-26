"""PySide6のデコレーターを実際の登録動作を保ったまま型付けする。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, ParamSpec, Protocol, TypeVar, overload

_P = ParamSpec("_P")
_R = TypeVar("_R")
_T_co = TypeVar("_T_co", covariant=True)


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


if TYPE_CHECKING:
    Slot: _SlotFactory
    Property: _PropertyFactory
else:
    from PySide6.QtCore import Property as Property, Slot as Slot
