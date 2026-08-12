from __future__ import annotations

import unicodedata
from functools import lru_cache

from . import rules, tokenize

TARGET_READING_SPEED = 14.0
TIMING_BALANCE_WEIGHT = 1.4


def display_width(char: str) -> int:
    return 2 if unicodedata.east_asian_width(char) in {"F", "W", "A"} else 1


@lru_cache(maxsize=16384)
def text_width(text: str) -> int:
    return sum(display_width(char) for char in text)


def duration_pressure(total_width: int, display_duration: float | None) -> float:
    if display_duration is None or display_duration <= 0:
        return 0.0
    reading_speed = total_width / max(display_duration, 0.01)
    return max(0.0, reading_speed - TARGET_READING_SPEED)


def timing_balance_penalty(
    left_width: int,
    right_width: int,
    display_duration: float | None,
) -> int:
    pressure = duration_pressure(left_width + right_width, display_duration)
    if pressure <= 0:
        return 0
    return round(abs(left_width - right_width) * pressure * TIMING_BALANCE_WEIGHT)


def char_bucket(char: str) -> str:
    codepoint = ord(char)
    if 0x3040 <= codepoint <= 0x309F:
        return "hiragana"
    if 0x30A0 <= codepoint <= 0x30FF:
        return "katakana"
    if 0x4E00 <= codepoint <= 0x9FFF:
        return "kanji"
    if char.isdigit():
        return "digit"
    if char.isascii() and char.isalpha():
        return "latin"
    return "other"


def connected_char_penalty(previous_char: str, next_char: str) -> int:
    if not previous_char or not next_char:
        return 0

    previous_bucket = char_bucket(previous_char)
    next_bucket = char_bucket(next_char)
    if previous_bucket == next_bucket == "hiragana" and previous_char == "ん":
        return 4
    if previous_bucket == next_bucket and previous_bucket in {"latin", "digit"}:
        return 40
    if previous_bucket == next_bucket and previous_bucket in {"hiragana", "katakana"}:
        return 12
    if {previous_bucket, next_bucket} <= {"hiragana", "katakana"}:
        return 8
    if previous_bucket == next_bucket == "kanji":
        return 14
    if previous_bucket == "kanji" and next_bucket == "hiragana":
        return 14
    if previous_bucket == "hiragana" and next_bucket == "kanji":
        return 8
    return 0


def is_protected_inline_split(previous_char: str, next_char: str) -> bool:
    return bool(
        previous_char
        and next_char
        and previous_char.isascii()
        and next_char.isascii()
        and previous_char.isalnum()
        and next_char.isalnum()
    )


def chunk_boundaries(text: str, chunks: list[str]) -> set[int]:
    boundaries: set[int] = set()
    cursor = 0
    for chunk in chunks:
        cursor += len(chunk)
        if 0 < cursor < len(text):
            boundaries.add(cursor)
    return boundaries


@lru_cache(maxsize=4096)
def budoux_boundaries(text: str) -> set[int]:
    return chunk_boundaries(text, tokenize.parse_budoux_chunks(text))


@lru_cache(maxsize=4096)
def morpheme_boundaries(text: str) -> set[int]:
    return chunk_boundaries(text, tokenize.parse_morpheme_chunks(text))


def clause_break_bonus(text: str, break_index: int) -> int:
    left = text[:break_index].rstrip()
    for token in rules.CLAUSE_BREAK_TOKENS:
        if left.endswith(token):
            return -6
    return 0


def leading_boundary_penalty(text: str, break_index: int) -> int:
    right = text[break_index:].lstrip()
    if not right:
        return 0
    for token, penalty in rules.LEADING_BOUNDARY_PENALTIES.items():
        if right.startswith(token):
            return penalty
    return 0


def candidate_kind_bonus(text: str, break_index: int) -> int:
    bonus = 0
    if break_index in budoux_boundaries(text):
        bonus -= 10
    if break_index in morpheme_boundaries(text):
        bonus -= 6
    if text[break_index - 1] in rules.STRONG_BREAK_CHARS:
        bonus -= 6
    elif text[break_index - 1] in rules.SOFT_BREAK_CHARS:
        bonus -= 3
    bonus += clause_break_bonus(text, break_index)
    return bonus


def score_break(
    text: str,
    break_index: int,
    max_width: int,
    display_duration: float | None = None,
) -> tuple[int, int, int, int, int, int, int, int]:
    left = text[:break_index].rstrip()
    right = text[break_index:].lstrip()
    left_width = text_width(left)
    right_width = text_width(right)
    overflow_penalty = max(0, left_width - max_width) + max(0, right_width - max_width)
    width_balance_penalty = abs(left_width - right_width)
    tiny_line_penalty = 0
    if left_width <= 5 or right_width <= 5:
        tiny_line_penalty += 18
    if left_width <= 3 or right_width <= 3:
        tiny_line_penalty += 36
    if left_width <= 2 or right_width <= 2:
        tiny_line_penalty += 60

    previous_char = left[-1] if left else ""
    next_char = right[0] if right else ""

    boundary_penalty = 0
    if next_char in rules.LEADING_AVOID_CHARS:
        boundary_penalty += 8
    if previous_char in rules.TRAILING_AVOID_CHARS:
        boundary_penalty += 6
    if right[:2] in rules.RIGHT_BOUNDARY_AVOID_WORDS:
        boundary_penalty += 10
    elif right[:1] in rules.RIGHT_BOUNDARY_AVOID_WORDS:
        boundary_penalty += 10
    if left[-2:] in rules.LEFT_BOUNDARY_AVOID_WORDS:
        boundary_penalty += 3
    elif left[-1:] in rules.LEFT_BOUNDARY_AVOID_WORDS:
        boundary_penalty += 3
    boundary_penalty += connected_char_penalty(previous_char, next_char)

    natural_midpoint_penalty = abs(break_index - len(text) // 2)
    candidate_bonus = candidate_kind_bonus(text, break_index)
    leading_penalty = leading_boundary_penalty(text, break_index)
    timing_penalty = timing_balance_penalty(left_width, right_width, display_duration)
    return (
        overflow_penalty,
        timing_penalty,
        tiny_line_penalty,
        candidate_bonus,
        boundary_penalty + leading_penalty,
        width_balance_penalty,
        natural_midpoint_penalty,
        break_index,
    )


def score_truncated_break(
    text: str,
    break_index: int,
    max_width: int,
    display_duration: float | None = None,
) -> tuple[int, int, int, int, int, int, int, int]:
    return score_break(text, break_index, max_width, display_duration=display_duration)
