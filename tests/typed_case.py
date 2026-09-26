"""unittest の Any 引数をテストコードへ伝播させない型付き基底クラス。"""

from __future__ import annotations

import unittest


class TypedTestCase(unittest.TestCase):
    def assertEqual(self, first: object, second: object, msg: str | None = None) -> None:
        super().assertEqual(first, second, msg)
