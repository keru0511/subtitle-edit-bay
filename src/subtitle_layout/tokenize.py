from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol

from ..typed_cache import typed_lru_cache


class ChunkParser(Protocol):
    def parse(self, text: str, /) -> Iterable[str]: ...


class MorphemeToken(Protocol):
    @property
    def surface(self) -> str: ...


class MorphemeTokenizer(Protocol):
    def tokenize(self, text: str, /) -> Iterable[MorphemeToken]: ...


class _BudouxModule(Protocol):
    def load_default_japanese_parser(self) -> ChunkParser: ...


budoux: _BudouxModule | None
JanomeTokenizer: Callable[[], MorphemeTokenizer] | None


try:
    import budoux as _budoux

    budoux = _budoux
except ImportError:  # pragma: no cover - depends on optional runtime package
    budoux = None

try:
    from janome.tokenizer import Tokenizer as _JanomeTokenizer

    JanomeTokenizer = _JanomeTokenizer
except ImportError:  # pragma: no cover - depends on optional runtime package
    JanomeTokenizer = None


@typed_lru_cache(maxsize=1)
def create_budoux_parser() -> ChunkParser | None:
    if budoux is None:
        return None
    return budoux.load_default_japanese_parser()


def parse_budoux_chunks(text: str, parser: ChunkParser | None = None) -> list[str]:
    resolved_parser = create_budoux_parser() if parser is None else parser
    if resolved_parser is None or not text:
        return [text] if text else []
    chunks = [chunk for chunk in resolved_parser.parse(text) if chunk]
    return chunks or [text]


@typed_lru_cache(maxsize=1)
def create_janome_tokenizer() -> MorphemeTokenizer | None:
    if JanomeTokenizer is None:
        return None
    return JanomeTokenizer()


def parse_morpheme_chunks(text: str, tokenizer: MorphemeTokenizer | None = None) -> list[str]:
    resolved_tokenizer = create_janome_tokenizer() if tokenizer is None else tokenizer
    if resolved_tokenizer is None or not text:
        return [text] if text else []
    chunks = [token.surface for token in resolved_tokenizer.tokenize(text) if token.surface]
    return chunks or [text]


def require_japanese_layout_tools() -> None:
    if create_budoux_parser() is None or create_janome_tokenizer() is None:
        raise RuntimeError(
            "BudouX and Janome are required for readable Japanese subtitle layout. "
            "Install dependencies with: python -m pip install -r requirements.txt"
        )
