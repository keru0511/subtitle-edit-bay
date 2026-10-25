
from src.typed_cache import typed_lru_cache
from tests.typed_case import TypedTestCase


class TypedCacheTests(TypedTestCase):
    def test_cache_preserves_keywords_eviction_and_clear(self) -> None:
        calls: list[str] = []

        @typed_lru_cache(maxsize=2)
        def compute(text: str, *, suffix: str = "!") -> str:
            calls.append(text)
            return text + suffix

        self.assertEqual(compute("a", suffix="?"), "a?")
        self.assertEqual(compute("a", suffix="?"), "a?")
        self.assertEqual(compute.cache_info().hits, 1)
        compute("b")
        compute("c")
        compute("a", suffix="?")
        self.assertEqual(len(calls), 4)
        self.assertEqual(compute.cache_info().currsize, 2)
        self.assertEqual(compute.cache_info().maxsize, 2)
        compute.cache_clear()
        self.assertEqual(compute.cache_info().currsize, 0)
        self.assertEqual(compute.cache_info().misses, 0)
