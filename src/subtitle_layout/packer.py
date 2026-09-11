"""Re-export the subtitle layout surface for migrated callers.

The layout rules, tokenizer factories, and scoring helpers live in the
``subtitle_layout`` submodules and are imported directly by ``subtitle_packer``.
This module keeps the ``subtitle_layout.packer`` import path stable while the
remaining packer functions still live in ``subtitle_packer``.
"""

from __future__ import annotations

from .. import subtitle_packer as legacy_packer

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
duration_pressure = legacy_packer.duration_pressure
timing_balance_penalty = legacy_packer.timing_balance_penalty
char_bucket = legacy_packer.char_bucket
connected_char_penalty = legacy_packer.connected_char_penalty
is_protected_inline_split = legacy_packer.is_protected_inline_split
chunk_boundaries = legacy_packer.chunk_boundaries
clause_break_bonus = legacy_packer.clause_break_bonus
leading_boundary_penalty = legacy_packer.leading_boundary_penalty
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
