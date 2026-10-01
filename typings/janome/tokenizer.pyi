# 引数なしのTokenizer（wakati=False）と標準のtokenize呼び出しだけを扱う。
from collections.abc import Iterator

class Token:
    @property
    def surface(self) -> str: ...

class Tokenizer:
    def __init__(self) -> None: ...
    def tokenize(self, text: str) -> Iterator[Token]: ...
