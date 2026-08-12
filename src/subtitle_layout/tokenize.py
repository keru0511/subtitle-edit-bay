from __future__ import annotations

from functools import lru_cache
from typing import Any

try:
    import budoux  # type: ignore
except ImportError:  # pragma: no cover - depends on optional runtime package
    budoux = None

try:
    from janome.tokenizer import Tokenizer as JanomeTokenizer  # type: ignore
except ImportError:  # pragma: no cover - depends on optional runtime package
    JanomeTokenizer = None


@lru_cache(maxsize=1)
def create_budoux_parser() -> Any | None:
    if budoux is None:
        return None
    return budoux.load_default_japanese_parser()


def parse_budoux_chunks(text: str, parser: Any | None = None) -> list[str]:
    resolved_parser = create_budoux_parser() if parser is None else parser
    if resolved_parser is None or not text:
        return [text] if text else []
    chunks = [chunk for chunk in resolved_parser.parse(text) if chunk]
    return chunks or [text]


@lru_cache(maxsize=1)
def create_janome_tokenizer() -> Any | None:
    if JanomeTokenizer is None:
        return None
    return JanomeTokenizer()


def parse_morpheme_chunks(text: str, tokenizer: Any | None = None) -> list[str]:
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
