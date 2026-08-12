from __future__ import annotations

from typing import Final

from .. import subtitle_packer as legacy_packer
from . import rules, tokenize

_RULE_BINDINGS: Final = (
    "MAX_LINES",
    "ELLIPSIS",
    "STRONG_BREAK_CHARS",
    "SOFT_BREAK_CHARS",
    "LEADING_AVOID_CHARS",
    "TRAILING_AVOID_CHARS",
    "RIGHT_BOUNDARY_AVOID_WORDS",
    "LEFT_BOUNDARY_AVOID_WORDS",
    "CLAUSE_BREAK_TOKENS",
    "LEADING_BOUNDARY_PENALTIES",
)
_TOKENIZER_FACTORY_BINDINGS: Final = (
    "create_budoux_parser",
    "create_janome_tokenizer",
)


def apply_layout_modules() -> None:
    """Bind the legacy packer to the extracted rules and tokenizer factories.

    The public functions still live in ``subtitle_packer`` during this migration,
    but global lookups inside those functions should resolve through the extracted
    layout boundary before the scoring/rules split continues. The legacy
    ``require_japanese_layout_tools`` wrapper intentionally remains in place so
    existing tests and callers can still patch ``src.subtitle_packer.create_*``.
    """
    for name in _RULE_BINDINGS:
        setattr(legacy_packer, name, getattr(rules, name))
    for name in _TOKENIZER_FACTORY_BINDINGS:
        setattr(legacy_packer, name, getattr(tokenize, name))


apply_layout_modules()

MAX_LINES = legacy_packer.MAX_LINES
ELLIPSIS = legacy_packer.ELLIPSIS
STRONG_BREAK_CHARS = legacy_packer.STRONG_BREAK_CHARS
SOFT_BREAK_CHARS = legacy_packer.SOFT_BREAK_CHARS
LEADING_AVOID_CHARS = legacy_packer.LEADING_AVOID_CHARS
TRAILING_AVOID_CHARS = legacy_packer.TRAILING_AVOID_CHARS
RIGHT_BOUNDARY_AVOID_WORDS = legacy_packer.RIGHT_BOUNDARY_AVOID_WORDS
LEFT_BOUNDARY_AVOID_WORDS = legacy_packer.LEFT_BOUNDARY_AVOID_WORDS
CLAUSE_BREAK_TOKENS = legacy_packer.CLAUSE_BREAK_TOKENS
LEADING_BOUNDARY_PENALTIES = legacy_packer.LEADING_BOUNDARY_PENALTIES

DEFAULT_SUBTITLE_MAX_GAP_SECONDS = legacy_packer.DEFAULT_SUBTITLE_MAX_GAP_SECONDS
DEFAULT_SUBTITLE_END_PADDING_SECONDS = legacy_packer.DEFAULT_SUBTITLE_END_PADDING_SECONDS
DEFAULT_SUBTITLE_MIN_DURATION_SECONDS = legacy_packer.DEFAULT_SUBTITLE_MIN_DURATION_SECONDS

create_budoux_parser = legacy_packer.create_budoux_parser
create_janome_tokenizer = legacy_packer.create_janome_tokenizer
require_japanese_layout_tools = legacy_packer.require_japanese_layout_tools
parse_budoux_chunks = legacy_packer.parse_budoux_chunks
parse_morpheme_chunks = legacy_packer.parse_morpheme_chunks

text_width = legacy_packer.text_width
display_width = legacy_packer.display_width
budoux_boundaries = legacy_packer.budoux_boundaries
morpheme_boundaries = legacy_packer.morpheme_boundaries
break_candidates = legacy_packer.break_candidates
score_break = legacy_packer.score_break
score_truncated_break = legacy_packer.score_truncated_break
split_into_atomic_units = legacy_packer.split_into_atomic_units
normalize_text = legacy_packer.normalize_text
pack_segment_pages = legacy_packer.pack_segment_pages
pack_event = legacy_packer.pack_event
pack_segments = legacy_packer.pack_segments
