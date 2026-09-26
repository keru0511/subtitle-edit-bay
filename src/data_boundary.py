"""外部データをobjectで受け取り、利用前にコンテナの形を検証する。"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import SupportsFloat, SupportsIndex, SupportsInt, TypeGuard


def is_object_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    """キー・値の型を仮定せず、読み取り可能なマッピングとして扱う。"""
    return isinstance(value, Mapping)


def is_object_sequence(value: object) -> TypeGuard[Sequence[object]]:
    """要素の型を仮定せず、読み取り可能なシーケンスとして扱う。"""
    return isinstance(value, Sequence)


def is_object_list(value: object) -> TypeGuard[list[object]]:
    """JSON配列などの更新可能なリストを、要素型を仮定せずに扱う。"""
    return isinstance(value, list)


def decode_json(text: str | bytes | bytearray) -> object:
    """JSONを未検証の値として受け渡す。構造・値の検証は呼び出し元が行う。"""
    payload: object = json.loads(text)
    return payload


def coerce_float(value: object) -> float:
    """数値・文字列・bytes/bytearray/memoryviewをfloatに変換する。"""
    if isinstance(value, memoryview):
        value = value.tobytes()
    # 通常のJSON数値は実行時プロトコル検査より先に判定する。
    if isinstance(value, (str, bytes, bytearray, int, float, SupportsFloat, SupportsIndex)):
        return float(value)
    raise TypeError("value must be convertible to float")


def coerce_int(value: object) -> int:
    """数値・文字列・bytes/bytearray/memoryviewをintに変換する。"""
    if isinstance(value, memoryview):
        value = value.tobytes()
    # 通常のJSON数値は実行時プロトコル検査より先に判定する。
    if isinstance(value, (str, bytes, bytearray, int, float, SupportsInt, SupportsIndex)):
        return int(value)
    raise TypeError("value must be convertible to int")


def is_object_iterable(value: object) -> TypeGuard[Iterable[object]]:
    """要素の型を仮定せず、反復可能な入力として扱う。"""
    return isinstance(value, Iterable)


def is_object_dict(value: object) -> TypeGuard[dict[object, object]]:
    """キー・値を検証する前の辞書を、同じ参照のまま更新可能として扱う。"""
    return isinstance(value, dict)
