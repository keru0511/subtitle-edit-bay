from __future__ import annotations

import json
import unittest
from collections import UserDict

from src.data_boundary import decode_json, is_object_mapping, is_object_sequence


class DataBoundaryTests(unittest.TestCase):
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
        self.assertFalse(is_object_sequence(object()))
        self.assertFalse(is_object_mapping(object()))

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


if __name__ == "__main__":
    unittest.main()
