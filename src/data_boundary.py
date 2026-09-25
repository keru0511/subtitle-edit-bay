"""外部データをobjectで受け取り、利用前にコンテナの形を検証する。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import SupportsFloat, SupportsIndex, SupportsInt, TypeGuard


def is_object_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    """キー・値の型を仮定せず、読み取り可能なマッピングとして扱う。"""
    return isinstance(value, Mapping)


def is_object_sequence(value: object) -> TypeGuard[Sequence[object]]:
    """要素の型を仮定せず、読み取り可能なシーケンスとして扱う。"""
    return isinstance(value, Sequence)


def decode_json(text: str) -> object:
    """JSONを未検証の値として受け渡す。構造・値の検証は呼び出し元が行う。"""
    payload: object = json.loads(text)
    return payload


def coerce_float(value: object) -> float:
    """数値・文字列・bytes/bytearray/memoryviewをfloatに変換する。"""
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (str, bytes, bytearray, SupportsFloat, SupportsIndex)):
        return float(value)
    raise TypeError("value must be convertible to float")


def coerce_int(value: object) -> int:
    """数値・文字列・bytes/bytearray/memoryviewをintに変換する。"""
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (str, bytes, bytearray, SupportsInt, SupportsIndex)):
        return int(value)
    raise TypeError("value must be convertible to int")
