"""unittest の Any 引数をテストコードへ伝播させない型付き基底クラス。"""

from __future__ import annotations

import unittest
from typing import Callable, TypeVar, cast


_CaseT = TypeVar("_CaseT", bound=unittest.TestCase)


def typed_skip_unless(condition: object, reason: str) -> Callable[[type[_CaseT]], type[_CaseT]]:
    """型を失わずにunittestのクラス単位スキップを適用する。"""

    return cast(Callable[[type[_CaseT]], type[_CaseT]], unittest.skipUnless(bool(condition), reason))


def typed_skip_unless_method(
    condition: object, reason: str
) -> Callable[[Callable[[_CaseT], None]], Callable[[_CaseT], None]]:
    """型を失わずにunittestのテストメソッドをスキップする。"""

    return cast(
        Callable[[Callable[[_CaseT], None]], Callable[[_CaseT], None]],
        unittest.skipUnless(bool(condition), reason),
    )


class TypedTestCase(unittest.TestCase):
    def assertEqual(self, first: object, second: object, msg: object = None) -> None:
        super().assertEqual(first, second, str(msg) if msg is not None else None)
