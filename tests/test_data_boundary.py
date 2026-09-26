from __future__ import annotations

import json
import unittest
from collections import UserDict

from src.data_boundary import (
    coerce_float,
    coerce_int,
    decode_json,
    is_object_iterable,
    is_object_dict,
    is_object_list,
    is_object_mapping,
    is_object_sequence,
)
from tests.typed_case import TypedTestCase


class DataBoundaryTests(TypedTestCase):
    def test_mutable_dict_guard_preserves_identity_and_unknown_values(self) -> None:
        original: dict[str, object] = {"value": [1, None]}
        incoming: object = original
        if not is_object_dict(incoming):
            self.fail("辞書を更新可能として受け取る必要があります")
        incoming["added"] = True
        self.assertIs(incoming, original)
        self.assertIs(original["added"], True)
        self.assertFalse(is_object_dict(UserDict({"value": 1})))
        self.assertFalse(is_object_dict([]))

    def test_mapping_preserves_unknown_key_and_value_types(self) -> None:
        payload: object = UserDict({1: ["value"]})
        self.assertTrue(is_object_mapping(payload))
        if not is_object_mapping(payload):
            self.fail("マッピングとして読み取れる必要があります")
        values = payload[1]
        self.assertTrue(is_object_sequence(values))
        if not is_object_sequence(values):
            self.fail("シーケンスとして読み取れる必要があります")
        self.assertEqual(values[0], "value")
        self.assertFalse(is_object_mapping(values))

    def test_sequence_guard_does_not_assume_string_elements(self) -> None:
        payload: object = (1, None, "value")
        self.assertTrue(is_object_sequence(payload))
        if not is_object_sequence(payload):
            self.fail("シーケンスとして読み取れる必要があります")
        self.assertEqual(payload[0], 1)
        self.assertIsNone(payload[1])
        self.assertFalse(is_object_list(payload))
        self.assertTrue(is_object_list([1, None]))
        self.assertFalse(is_object_sequence(object()))
        self.assertFalse(is_object_mapping(object()))

    def test_iterable_guard_preserves_lazy_values(self) -> None:
        values: object = (value for value in (1, "two", None))
        if not is_object_iterable(values):
            self.fail("ジェネレーターを受け入れる必要があります")
        actual = list(values)
        expected: list[object] = [1, "two", None]
        self.assertEqual(actual, expected)
        self.assertFalse(is_object_iterable(None))

    def test_decode_leaves_domain_validation_to_caller(self) -> None:
        payload = decode_json('{"items": [1, null, "日本語"]}')
        if not is_object_mapping(payload):
            self.fail("JSONオブジェクトとして読み取れる必要があります")
        items = payload["items"]
        if not is_object_sequence(items):
            self.fail("JSON配列として読み取れる必要があります")
        self.assertEqual(items[0], 1)
        self.assertIsNone(items[1])
        self.assertEqual(items[2], "日本語")
        self.assertIsNone(decode_json("null"))
        self.assertTrue(decode_json("true"))
        with self.assertRaises(json.JSONDecodeError):
            decode_json("{")

    def test_numeric_coercion_preserves_builtin_json_input_semantics(self) -> None:
        values: tuple[object, ...] = (2, 2.0, "2", b"2", bytearray(b"2"), memoryview(b"2"))
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(coerce_float(value), 2.0)
                self.assertEqual(coerce_int(value), 2)
        self.assertEqual(coerce_int(1.9), 1)
        self.assertEqual(coerce_int(True), 1)
        self.assertEqual(coerce_float(False), 0.0)
        for invalid in (None, object(), [], {}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(TypeError):
                    coerce_float(invalid)
                with self.assertRaises(TypeError):
                    coerce_int(invalid)
        with self.assertRaises(ValueError):
            coerce_int("1.5")
        with self.assertRaises(ValueError):
            coerce_float("invalid")
        with self.assertRaises(OverflowError):
            coerce_int(float("inf"))

    def test_numeric_coercion_accepts_explicit_numeric_protocols(self) -> None:
        class FloatValue:
            def __float__(self) -> float:
                return 2.5

        class IntegerValue:
            def __int__(self) -> int:
                return 3

        class IndexValue:
            def __index__(self) -> int:
                return 4

        self.assertEqual(coerce_float(FloatValue()), 2.5)
        self.assertEqual(coerce_int(IntegerValue()), 3)
        self.assertEqual(coerce_float(IndexValue()), 4.0)
        self.assertEqual(coerce_int(IndexValue()), 4)


if __name__ == "__main__":
    unittest.main()
