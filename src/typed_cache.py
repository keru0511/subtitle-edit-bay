"""標準LRUキャッシュの引数・戻り値の型と管理APIを保持する。"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from typing import ParamSpec, Protocol, TypeVar, runtime_checkable

_Parameters = ParamSpec("_Parameters")
_Result = TypeVar("_Result", covariant=True)


class CacheInfo(Protocol):
    @property
    def hits(self) -> int: ...
    @property
    def misses(self) -> int: ...
    @property
    def maxsize(self) -> int | None: ...
    @property
    def currsize(self) -> int: ...


class CachedFunction(Protocol[_Parameters, _Result]):
    def __call__(self, *args: _Parameters.args, **kwargs: _Parameters.kwargs) -> _Result: ...
    def cache_clear(self) -> None: ...
    def cache_info(self) -> CacheInfo: ...


@runtime_checkable
class _CacheDecorator(Protocol):
    def __call__(self, function: Callable[_Parameters, _Result]) -> CachedFunction[_Parameters, _Result]: ...


def typed_lru_cache(maxsize: int) -> _CacheDecorator:
    """実行には標準実装を使い、そのシグネチャ保存契約をProtocolで表す。"""
    # typeshedのlru_cacheは引数の型を消すため、返されたデコレーターとの境界を明示する。
    # 任意の外部値をこの契約に変換せず、標準ライブラリからの値だけを受け取る。
    decorator: object = lru_cache(maxsize=maxsize)
    if not isinstance(decorator, _CacheDecorator):
        raise TypeError("lru_cache must return a callable decorator")
    return decorator
