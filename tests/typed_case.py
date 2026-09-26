"""unittest の Any 引数をテストコードへ伝播させない型付き基底クラス。"""

from __future__ import annotations

import unittest
from typing import Callable, TypeVar, cast


_CaseT = TypeVar("_CaseT", bound=unittest.TestCase)


def typed_skip_unless(condition: bool, reason: str) -> Callable[[type[_CaseT]], type[_CaseT]]:
    """型を失わずにunittestのクラス単位スキップを適用する。"""

    return cast(Callable[[type[_CaseT]], type[_CaseT]], unittest.skipUnless(condition, reason))


class TypedTestCase(unittest.TestCase):
    def assertEqual(self, first: object, second: object, msg: str | None = None) -> None:
        super().assertEqual(first, second, msg)
