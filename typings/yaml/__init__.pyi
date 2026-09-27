from re import Pattern
from typing import ClassVar


class YAMLError(Exception): ...


class SafeLoader:
    yaml_implicit_resolvers: ClassVar[dict[str | None, list[tuple[str, Pattern[str]]]]]

    @classmethod
    def add_implicit_resolver(cls, tag: str, regexp: Pattern[str], first: list[str]) -> None: ...


def load(stream: str, Loader: type[SafeLoader]) -> object: ...


def safe_load(stream: str) -> object: ...
