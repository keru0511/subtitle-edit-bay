"""外部データをobjectで受け取り、利用前にコンテナの形を検証する。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TypeGuard


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
