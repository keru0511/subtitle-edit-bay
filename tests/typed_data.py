"""テストで扱うJSON境界とモックの呼び出し値を検証する。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeGuard, cast
from unittest.mock import MagicMock

from src.data_boundary import is_string_object_dict, is_string_object_dict_list


def object_dict(value: object) -> dict[str, object]:
    if not is_string_object_dict(value):
        raise AssertionError("expected an object with string keys")
    return value


def section(value: object, key: str) -> dict[str, object]:
    parent = object_dict(value)
    return object_dict(parent[key])


def entries(value: object, key: str) -> list[dict[str, object]]:
    parent = object_dict(value)
    children = parent[key]
    if not is_string_object_dict_list(children):
        raise AssertionError(f"{key} must be an array of objects")
    return children


def is_string_list(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def mock_kwargs(target: MagicMock) -> Mapping[str, object]:
    call = target.call_args
    if call is None:
        raise AssertionError("expected a mock call")
    return cast(Mapping[str, object], call.kwargs)


def mock_args(target: MagicMock) -> tuple[object, ...]:
    call = target.call_args
    if call is None:
        raise AssertionError("expected a mock call")
    return cast(tuple[object, ...], call.args)
