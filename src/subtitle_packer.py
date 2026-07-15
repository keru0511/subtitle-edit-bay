from __future__ import annotations

import unicodedata
from functools import lru_cache

from .models import SubtitleEvent

MAX_LINES = 2
ELLIPSIS = "\u2026"
STRONG_BREAK_CHARS = set("\u3002\uff01\uff1f!?")
SOFT_BREAK_CHARS = set("\u3001,")
LEADING_AVOID_CHARS = set("\u3001\u3002\uff01\uff1f!?)]}\u300d\u300f\uff09\u3011\u3041\u3043\u3045\u3047\u3049\u3063\u3083\u3085\u3087\u30a1\u30a3\u30a5\u30a7\u30a9\u30c3\u30e3\u30e5\u30e7\u30fc")
TRAILING_AVOID_CHARS = set("\uff08([{")
RIGHT_BOUNDARY_AVOID_WORDS = {
    "\u304c", "\u3092", "\u306b", "\u3067", "\u3068", "\u3078", "\u306e", "\u306f", "\u3082", "\u3084",
    "\u306d", "\u3088", "\u306a", "\u305e", "\u3055", "\u304b", "\u3057", "\u3066", "\u3060", "\u3067\u3059", "\u307e\u3059",
    "\u3055\u3093", "\u304f\u3093", "\u3061\u3083\u3093",
}
LEFT_BOUNDARY_AVOID_WORDS = {
    "\u304c", "\u3092", "\u306b", "\u3067", "\u3068", "\u3078", "\u306e", "\u306f", "\u3082", "\u3084",
    "\u306d", "\u3088", "\u306a", "\u305e", "\u3055", "\u304b", "\u3057", "\u3066", "\u3060",
}
CLAUSE_BREAK_TOKENS = (
    "\u3067\u3082",
    "\u3060\u3051\u3069",
    "\u3051\u3069",
    "\u3060\u304b\u3089",
    "\u306a\u306e\u3067",
    "\u3060\u304c",
    "\u3057\u304b\u3057",
    "\u305d\u3057\u3066",
    "\u305d\u308c\u3067",
    "\u305f\u3060",
    "\u305f\u3060\u3057",
    "\u3042\u3068",
    "\u3058\u3083\u3042",
    "\u304b\u3089",
    "\u306e\u3067",
    "\u306e\u306b",
    "\u3068\u304b",
    "\u3063\u3066",
)
try:
    import budoux  # type: ignore
except ImportError:
    budoux = None

try:
    from janome.tokenizer import Tokenizer as JanomeTokenizer  # type: ignore
except ImportError:
    JanomeTokenizer = None


@lru_cache(maxsize=1)
def create_budoux_parser():
    if budoux is None:
        return None
    return budoux.load_default_japanese_parser()


def parse_budoux_chunks(text: str) -> list[str]:
    parser = create_budoux_parser()
    if parser is None or not text:
        return [text] if text else []
    chunks = [chunk for chunk in parser.parse(text) if chunk]
    return chunks or [text]


@lru_cache(maxsize=1)
def create_janome_tokenizer():
    if JanomeTokenizer is None:
        return None
    return JanomeTokenizer()


def parse_morpheme_chunks(text: str) -> list[str]:
    tokenizer = create_janome_tokenizer()
    if tokenizer is None or not text:
        return [text] if text else []
    chunks = [token.surface for token in tokenizer.tokenize(text) if token.surface]
    return chunks or [text]


def require_japanese_layout_tools() -> None:
    if create_budoux_parser() is None or create_janome_tokenizer() is None:
        raise RuntimeError(
            "BudouX and Janome are required for readable Japanese subtitle layout. "
            "Install dependencies with: python -m pip install -r requirements.txt"
        )


def display_width(char: str) -> int:
    return 2 if unicodedata.east_asian_width(char) in {"F", "W", "A"} else 1


def text_width(text: str) -> int:
    return sum(display_width(char) for char in text)


def duration_pressure(total_width: int, display_duration: float | None) -> float:
    if display_duration is None or display_duration <= 0:
        return 0.0
    reading_speed = total_width / max(display_duration, 0.01)
    return max(0.0, reading_speed - TARGET_READING_SPEED)


def timing_balance_penalty(left_width: int, right_width: int, display_duration: float | None) -> int:
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
    if previous_bucket == next_bucket == "hiragana" and previous_char == "\u3093":
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
    return bool(previous_char and next_char and previous_char.isascii() and next_char.isascii() and previous_char.isalnum() and next_char.isalnum())


def chunk_boundaries(text: str, chunks: list[str]) -> set[int]:
    boundaries: set[int] = set()
    cursor = 0
    for chunk in chunks:
        cursor += len(chunk)
        if 0 < cursor < len(text):
            boundaries.add(cursor)
    return boundaries


def budoux_boundaries(text: str) -> set[int]:
    return chunk_boundaries(text, parse_budoux_chunks(text))


@lru_cache(maxsize=4096)
def morpheme_boundaries(text: str) -> set[int]:
    return chunk_boundaries(text, parse_morpheme_chunks(text))


def clause_break_bonus(text: str, break_index: int) -> int:
    left = text[:break_index].rstrip()
    for token in CLAUSE_BREAK_TOKENS:
        if left.endswith(token):
            return -6
    return 0


def leading_boundary_penalty(text: str, break_index: int) -> int:
    right = text[break_index:].lstrip()
    if not right:
        return 0
    penalties = {
        "\u3055\u3093": 14,
        "\u304f\u3093": 14,
        "\u3061\u3083\u3093": 14,
        "\u304c": 10,
        "\u3092": 10,
        "\u306b": 10,
        "\u3067": 8,
        "\u3068": 8,
        "\u306e": 8,
        "\u306f": 10,
        "\u3082": 8,
        "\u3066": 8,
        "\u3060": 8,
    }
    for token, penalty in penalties.items():
        if right.startswith(token):
            return penalty
    return 0

def candidate_kind_bonus(text: str, break_index: int) -> int:
    bonus = 0
    if break_index in budoux_boundaries(text):
        bonus -= 10
    if break_index in morpheme_boundaries(text):
        bonus -= 6
    if text[break_index - 1] in STRONG_BREAK_CHARS:
        bonus -= 6
    elif text[break_index - 1] in SOFT_BREAK_CHARS:
        bonus -= 3
    bonus += clause_break_bonus(text, break_index)
    return bonus


def best_chunk_split_index(current: list[str], max_width: int) -> int | None:
    if len(current) <= 1:
        return None

    full_text = "".join(current)
    candidates: list[tuple[tuple[int, int, int, int, int, int, int], int]] = []
    for index in range(1, len(current)):
        left = "".join(current[:index]).rstrip()
        right = "".join(current[index:]).lstrip()
        if not left or not right:
            continue
        left_width = text_width(left)
        right_width = text_width(right)
        if left_width > max_width:
            continue

        previous_char = left[-1]
        next_char = right[0]
        hard_penalty = connected_char_penalty(previous_char, next_char)
        soft_penalty = 0
        if next_char in LEADING_AVOID_CHARS:
            soft_penalty += 8
        if previous_char in TRAILING_AVOID_CHARS:
            soft_penalty += 6
        if right[:2] in RIGHT_BOUNDARY_AVOID_WORDS or right[:1] in RIGHT_BOUNDARY_AVOID_WORDS:
            soft_penalty += 10
        if left[-2:] in LEFT_BOUNDARY_AVOID_WORDS or left[-1:] in LEFT_BOUNDARY_AVOID_WORDS:
            soft_penalty += 3
        short_left_penalty = 8 if left_width <= 6 else 0
        width_balance_penalty = abs(left_width - right_width)
        width_slack_penalty = max_width - left_width
        char_break_index = len("".join(current[:index]))
        boundary_bonus = candidate_kind_bonus(full_text, char_break_index)
        leading_penalty = leading_boundary_penalty(full_text, char_break_index)
        score = (hard_penalty, soft_penalty + leading_penalty, short_left_penalty, boundary_bonus, width_balance_penalty, width_slack_penalty)
        candidates.append((score, index))

    if not candidates:
        return None

    balanced_candidates = [item for item in candidates if min(text_width("".join(current[:item[1]]).rstrip()), text_width("".join(current[item[1]:]).lstrip())) > 5]
    pool = balanced_candidates or candidates
    return min(pool, key=lambda item: item[0])[1]


def split_by_width_naturally(text: str, max_width: int) -> list[str]:
    remaining = list(text)
    chunks: list[str] = []
    while text_width("".join(remaining)) > max_width:
        split_index = best_chunk_split_index(remaining, max_width)
        if split_index is None:
            return chunks + split_by_width("".join(remaining), max_width)
        left = "".join(remaining[:split_index]).strip()
        if left:
            chunks.append(left)
        remaining = remaining[split_index:]
    tail = "".join(remaining).strip()
    if tail:
        chunks.append(tail)
    return chunks


def chunk_text(text: str, max_width: int) -> list[str]:
    parser = create_budoux_parser()
    if parser is None:
        return split_by_width(text, max_width)

    pieces = parse_budoux_chunks(text)
    if not pieces:
        return []
    if len(pieces) == 1 and text_width(pieces[0]) > max_width:
        return split_by_width_naturally(text, max_width)

    chunks: list[str] = []
    current: list[str] = []
    current_width = 0

    for piece in pieces:
        piece_width = text_width(piece)
        if current and current_width + piece_width > max_width:
            split_index = best_chunk_split_index(current, max_width)
            if split_index is None:
                chunks.append("".join(current).strip())
                current = [piece]
                current_width = piece_width
                continue

            left = "".join(current[:split_index]).strip()
            right = "".join(current[split_index:]).strip()
            if left:
                chunks.append(left)
            current = ([right] if right else []) + [piece]
            current_width = text_width("".join(current))
            continue
        current.append(piece)
        current_width += piece_width

    if current:
        chunks.append("".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def break_candidates(text: str, max_width: int) -> list[int]:
    candidates: set[int] = set()
    primary_boundaries = budoux_boundaries(text)
    candidates.update(primary_boundaries or morpheme_boundaries(text))

    for index in range(1, len(text)):
        if text[index - 1].isspace() or text[index] in SOFT_BREAK_CHARS or text[index - 1] in (STRONG_BREAK_CHARS | SOFT_BREAK_CHARS):
            candidates.add(index)

    if not candidates:
        for index in range(1, len(text)):
            if is_protected_inline_split(text[index - 1], text[index]):
                continue
            if text_width(text[:index]) <= max_width * 1.45 and text_width(text[index:]) <= max_width * 1.45:
                candidates.add(index)

    return sorted(candidate for candidate in candidates if 0 < candidate < len(text))


def score_break(text: str, break_index: int, max_width: int, display_duration: float | None = None) -> tuple[int, int, int, int, int, int, int, int]:
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
    if next_char in LEADING_AVOID_CHARS:
        boundary_penalty += 8
    if previous_char in TRAILING_AVOID_CHARS:
        boundary_penalty += 6
    if right[:2] in RIGHT_BOUNDARY_AVOID_WORDS or right[:1] in RIGHT_BOUNDARY_AVOID_WORDS:
        boundary_penalty += 10
    if left[-2:] in LEFT_BOUNDARY_AVOID_WORDS or left[-1:] in LEFT_BOUNDARY_AVOID_WORDS:
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


def build_two_line_candidate(text: str, max_width: int, display_duration: float | None = None) -> str | None:
    candidates = break_candidates(text, max_width)
    viable_candidates: list[int] = []
    for candidate in candidates:
        left = text[:candidate].rstrip()
        right = text[candidate:].lstrip()
        if not left or not right:
            continue
        left_width = text_width(left)
        right_width = text_width(right)
        if left_width <= max_width * 1.45 and right_width <= max_width * 1.45:
            if min(left_width, right_width) <= 5 and text_width(text) > max_width + 2:
                continue
            viable_candidates.append(candidate)

    if not viable_candidates:
        return None

    break_index = min(viable_candidates, key=lambda candidate: score_break(text, candidate, max_width, display_duration=display_duration))
    return text[:break_index].rstrip() + r"\N" + text[break_index:].lstrip()


def has_awkward_boundary(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return connected_char_penalty(left[-1], right[0]) >= 8


def score_truncated_break(text: str, break_index: int, max_width: int, display_duration: float | None = None) -> tuple[int, int, int, int, int, int, int, int]:
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
    if next_char in LEADING_AVOID_CHARS:
        boundary_penalty += 8
    if previous_char in TRAILING_AVOID_CHARS:
        boundary_penalty += 6
    if right[:2] in RIGHT_BOUNDARY_AVOID_WORDS or right[:1] in RIGHT_BOUNDARY_AVOID_WORDS:
        boundary_penalty += 10
    if left[-2:] in LEFT_BOUNDARY_AVOID_WORDS or left[-1:] in LEFT_BOUNDARY_AVOID_WORDS:
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


def build_truncated_two_line_candidate(lines: list[str], max_width: int, max_lines: int, display_duration: float | None = None) -> list[str] | None:
    best_choice: tuple[tuple[int, int, int, int, int, int, int, int, int], list[str]] | None = None
    max_source = min(len(lines), max_lines + 2)
    for source_limit in range(max_lines, max_source + 1):
        joined = "".join(lines[:source_limit]).strip()
        if not joined:
            continue
        candidates = break_candidates(joined, max_width)
        for candidate in candidates:
            left = joined[:candidate].rstrip()
            right = joined[candidate:].lstrip()
            if not left or not right:
                continue
            left_width = text_width(left)
            right_width = text_width(right)
            if left_width > max_width * 1.45 or right_width > max_width * 1.45:
                continue
            score = score_truncated_break(joined, candidate, max_width, display_duration=display_duration) + (-source_limit,)
            visible = [left, right]
            if best_choice is None or score < best_choice[0]:
                best_choice = (score, visible)
    return None if best_choice is None else best_choice[1]


def truncate_visible_lines(lines: list[str], max_width: int, max_lines: int, display_duration: float | None = None) -> str:
    visible = lines[:max_lines]
    if max_lines == 2 and len(visible) >= 2:
        first_width = text_width(visible[0])
        second_width = text_width(visible[1])
        if first_width <= 6 or second_width <= 8 or has_awkward_boundary(visible[0], visible[1]):
            rebalanced = build_truncated_two_line_candidate(lines, max_width, max_lines, display_duration=display_duration)
            if rebalanced is not None:
                visible = rebalanced

    last_line = visible[-1]
    if last_line and last_line[-1].isascii() and last_line[-1].isalnum():
        visible[-1] = last_line + ELLIPSIS
    else:
        visible[-1] = last_line[:-1] + ELLIPSIS if len(last_line) >= 2 else last_line + ELLIPSIS
    return r"\N".join(visible)


def normalize_text(text: str, max_width: int = 24, max_lines: int = MAX_LINES, display_duration: float | None = None) -> str:
    compact = " ".join(text.split())
    if not compact:
        return compact

    if max_lines <= 1:
        chunks = chunk_text(compact, max_width)
        return chunks[0] if len(chunks) <= 1 else chunks[0][:-1] + ELLIPSIS

    compact_width = text_width(compact)
    if compact_width <= max_width:
        return compact
    if compact_width <= max_width + 2 and compact[-1] in STRONG_BREAK_CHARS | SOFT_BREAK_CHARS:
        return compact

    if max_lines == 2 and compact_width <= max_width * 2.5:
        two_line_candidate = build_two_line_candidate(compact, max_width, display_duration=display_duration)
        if two_line_candidate is not None:
            return two_line_candidate

    chunks = chunk_text(compact, max_width)
    if len(chunks) > max_lines:
        return truncate_visible_lines(chunks, max_width, max_lines, display_duration=display_duration)
    return r"\N".join(chunks)


TARGET_MIN_DURATION = 0.8
TARGET_MAX_DURATION = 2.8
ABSOLUTE_MAX_DURATION = 3.6
DEFAULT_PAGE_WIDTH = 28
MAX_UNIT_WIDTH = DEFAULT_PAGE_WIDTH * MAX_LINES
TARGET_READING_SPEED = 14.0
TIMING_BALANCE_WEIGHT = 1.4
MAX_ALIGNED_CHARACTER_DURATION_SECONDS = 0.65
DEFAULT_SUBTITLE_MAX_GAP_SECONDS = 0.32
DEFAULT_SUBTITLE_END_PADDING_SECONDS = 0.08
DEFAULT_SUBTITLE_MIN_DURATION_SECONDS = 0.35
GAP_BOUNDARY_SNAP_RADIUS = 8
MIN_FORCED_FRAGMENT_WIDTH = 4
ATTACH_TO_PREVIOUS_FRAGMENTS = {"\u304b\u3089", "\u306e\u3067", "\u3051\u3069", "\u306e\u306b", "\u3063\u3066", "\u3068\u304b", "\u306a\u3089", "\u305f\u3089"}
SHORT_UTTERANCE_FRAGMENTS = {"\u3046\u3093", "\u306f\u3044", "\u3048\u3048", "\u3044\u3084", "\u3078\u3048", "\u305d\u3046"}
PUNCTUATION_BREAKS = r"\u3002\uff01\uff1f!?\n".encode("ascii").decode("unicode_escape")
PAGE_BREAK_PUNCTUATION = set(PUNCTUATION_BREAKS)
CONNECTORS = [
    r"\u3067\u3082",
    r"\u3060\u3051\u3069",
    r"\u3051\u3069",
    r"\u3060\u304b\u3089",
    r"\u306a\u306e\u3067",
    r"\u3060\u304c",
    r"\u3057\u304b\u3057",
    r"\u305d\u3057\u3066",
    r"\u305d\u308c\u3067",
    r"\u305f\u3060",
    r"\u305f\u3060\u3057",
    r"\u3042\u3068",
    r"\u3058\u3083\u3042",
]
CONNECTORS = [connector.encode("ascii").decode("unicode_escape") for connector in CONNECTORS]


def normalize_alignment_text(text: str) -> str:
    return "".join(str(text).split())


def split_by_width(text: str, max_width: int = MAX_UNIT_WIDTH) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    current_width = 0
    for char in text:
        char_width = display_width(char)
        if current and current_width + char_width > max_width:
            parts.append("".join(current).strip())
            current = [char]
            current_width = char_width
        else:
            current.append(char)
            current_width += char_width
    if current:
        parts.append("".join(current).strip())
    return [part for part in parts if part]


def split_by_connectors(text: str) -> list[str]:
    pieces = [text]
    for connector in CONNECTORS:
        next_pieces: list[str] = []
        for piece in pieces:
            replaced = piece.replace(connector, f"|{connector}")
            next_pieces.extend(part for part in replaced.split("|") if part)
        pieces = next_pieces
    return pieces


def split_into_atomic_units(text: str) -> list[str]:
    normalized = " ".join(text.split())
    if not normalized:
        return []

    sentence_units: list[str] = []
    current: list[str] = []
    for char in normalized:
        current.append(char)
        if char in PUNCTUATION_BREAKS or char in SOFT_BREAK_CHARS:
            sentence_units.append("".join(current).strip())
            current = []
    if current:
        sentence_units.append("".join(current).strip())

    budoux_units: list[str] = []
    for unit in sentence_units:
        if not unit:
            continue
        if unit[-1] in PUNCTUATION_BREAKS or unit[-1] in SOFT_BREAK_CHARS:
            body = unit[:-1]
            trailing = unit[-1]
            body_chunks = parse_budoux_chunks(body) if body else []
            if body_chunks:
                body_chunks[-1] = body_chunks[-1] + trailing
                budoux_units.extend(body_chunks)
            else:
                budoux_units.append(trailing)
            continue
        budoux_units.extend(parse_budoux_chunks(unit))

    connector_split: list[str] = []
    for chunk in budoux_units:
        connector_split.extend(split_by_connectors(chunk))

    final_units: list[str] = []
    soft_break_text = "".join(SOFT_BREAK_CHARS)
    for chunk in connector_split:
        if not any(mark in chunk for mark in PUNCTUATION_BREAKS + soft_break_text) and text_width(chunk) > MAX_UNIT_WIDTH:
            final_units.extend(split_by_width_naturally(chunk, MAX_UNIT_WIDTH))
        else:
            final_units.append(chunk)
    return [unit for unit in final_units if unit]


def natural_boundary_indices(text: str) -> set[int]:
    boundaries = set(budoux_boundaries(text))
    boundaries.update(morpheme_boundaries(text))
    for index in range(1, len(text)):
        if text[index - 1] in STRONG_BREAK_CHARS or text[index - 1] in SOFT_BREAK_CHARS:
            boundaries.add(index)
    return boundaries


def snap_forced_boundaries(text: str, boundaries: set[int], radius: int = GAP_BOUNDARY_SNAP_RADIUS) -> set[int]:
    if not boundaries:
        return set()

    natural_boundaries = natural_boundary_indices(text)
    snapped: set[int] = set()
    for boundary in sorted(boundaries):
        candidates = [candidate for candidate in natural_boundaries if abs(candidate - boundary) <= radius]
        if not candidates:
            continue
        best = min(candidates, key=lambda candidate: (abs(candidate - boundary), candidate))
        left_width = text_width(text[:best].strip())
        right_width = text_width(text[best:].strip())
        if left_width < MIN_FORCED_FRAGMENT_WIDTH or right_width < MIN_FORCED_FRAGMENT_WIDTH:
            continue
        snapped.add(best)
    return remove_orphan_forced_boundaries(text, snapped)


def remove_orphan_forced_boundaries(text: str, boundaries: set[int]) -> set[int]:
    filtered = set(boundaries)
    while filtered:
        ordered = [0, *sorted(filtered), len(text)]
        removed = False
        for fragment_index in range(len(ordered) - 1):
            fragment = text[ordered[fragment_index]:ordered[fragment_index + 1]].strip()
            if fragment in ATTACH_TO_PREVIOUS_FRAGMENTS and fragment_index > 0:
                filtered.remove(ordered[fragment_index])
                removed = True
                break
            if text_width(fragment) >= MIN_FORCED_FRAGMENT_WIDTH or fragment in SHORT_UTTERANCE_FRAGMENTS:
                continue

            if fragment_index + 2 < len(ordered):
                next_fragment = text[ordered[fragment_index + 1]:ordered[fragment_index + 2]].strip()
                if fragment + next_fragment in SHORT_UTTERANCE_FRAGMENTS:
                    filtered.remove(ordered[fragment_index + 1])
                    removed = True
                    break
            if fragment_index > 0:
                filtered.remove(ordered[fragment_index])
                removed = True
                break
            if fragment_index + 1 < len(ordered) - 1:
                filtered.remove(ordered[fragment_index + 1])
                removed = True
                break
        if not removed:
            break
    return filtered


def split_text_by_boundaries(text: str, boundaries: set[int]) -> list[str]:
    if not boundaries:
        return [text] if text else []
    parts: list[str] = []
    cursor = 0
    for boundary in sorted(boundary for boundary in boundaries if 0 < boundary < len(text)):
        fragment = text[cursor:boundary].strip()
        if fragment:
            parts.append(fragment)
        cursor = boundary
    tail = text[cursor:].strip()
    if tail:
        parts.append(tail)
    return parts


def split_into_atomic_unit_entries(text: str, forced_boundaries: set[int] | None = None) -> list[dict]:
    normalized = " ".join(text.split())
    if not normalized:
        return []

    snapped_boundaries = snap_forced_boundaries(normalized, forced_boundaries or set())
    fragments = split_text_by_boundaries(normalized, snapped_boundaries)
    entries: list[dict] = []
    for fragment_index, fragment in enumerate(fragments):
        fragment_units = split_into_atomic_units(fragment)
        for unit_index, unit in enumerate(fragment_units):
            entries.append(
                {
                    "text": unit,
                    "force_break_before": fragment_index > 0 and unit_index == 0,
                }
            )
    return entries


def duration_for_width(width: int, total_width: int, total_duration: float) -> float:
    if total_width <= 0:
        return total_duration
    return total_duration * (width / total_width)


def build_character_timeline(words: list[dict] | None) -> list[dict]:
    timeline: list[dict] = []
    for word in words or []:
        normalized = normalize_alignment_text(word.get("word", ""))
        start = word.get("start")
        end = word.get("end")
        if not normalized or start is None or end is None:
            continue

        start_time = float(start)
        end_time = effective_word_end(word)
        if end_time <= start_time:
            continue

        duration = end_time - start_time
        length = len(normalized)
        for index, _ in enumerate(normalized):
            char_start = start_time + duration * (index / length)
            char_end = start_time + duration * ((index + 1) / length)
            timeline.append({"start": char_start, "end": char_end})
    return timeline


def effective_word_end(word: dict) -> float:
    start = float(word["start"])
    end = float(word["end"])
    character_count = max(1, len(normalize_alignment_text(word.get("word", ""))))
    return min(end, start + MAX_ALIGNED_CHARACTER_DURATION_SECONDS * character_count)


def gap_boundary_indices(words: list[dict] | None, max_gap_seconds: float) -> set[int]:
    boundaries: set[int] = set()
    if not words:
        return boundaries

    normalized_lengths = [len(normalize_alignment_text(word.get("word", ""))) for word in words]
    cursor = 0
    for index in range(len(words) - 1):
        cursor += normalized_lengths[index]
        current_end = words[index].get("end")
        next_start = words[index + 1].get("start")
        if current_end is None or next_start is None:
            continue
        inferred_end = effective_word_end(words[index])
        if float(next_start) - inferred_end >= max_gap_seconds and cursor > 0:
            boundaries.add(cursor)
    return boundaries


def split_words_on_gaps(words: list[dict] | None, max_gap_seconds: float) -> list[list[dict]]:
    valid_words: list[dict] = []
    for word in words or []:
        normalized = normalize_alignment_text(word.get("word", ""))
        start = word.get("start")
        end = word.get("end")
        if not normalized or start is None or end is None:
            continue
        if float(end) <= float(start):
            continue
        valid_words.append(word)

    if not valid_words:
        return []

    groups: list[list[dict]] = [[valid_words[0]]]
    for word in valid_words[1:]:
        previous = groups[-1][-1]
        gap = float(word["start"]) - float(previous["end"])
        if gap >= max_gap_seconds:
            groups.append([word])
        else:
            groups[-1].append(word)
    return groups


def build_segment_text_from_words(words: list[dict]) -> str:
    return "".join(str(word.get("word", "")) for word in words).strip()


def split_segment_by_word_gaps(segment: dict, max_gap_seconds: float) -> list[dict]:
    word_groups = split_words_on_gaps(segment.get("words"), max_gap_seconds)
    if len(word_groups) <= 1:
        return [segment]

    split_segments: list[dict] = []
    for group in word_groups:
        split_segments.append(
            {
                **segment,
                "start": float(group[0]["start"]),
                "end": float(group[-1]["end"]),
                "text": build_segment_text_from_words(group) or segment.get("text", ""),
                "words": group,
            }
        )
    return split_segments


def build_timed_units_from_width(segment: dict, unit_entries: list[dict], start: float, end: float) -> list[dict]:
    total_duration = max(0.01, end - start)
    texts = [entry["text"] for entry in unit_entries]
    widths = [max(1, text_width(text)) for text in texts]
    total_width = sum(widths)

    timed_units: list[dict] = []
    cursor = start
    for index, entry in enumerate(unit_entries):
        raw_duration = duration_for_width(widths[index], total_width, total_duration)
        next_cursor = end if index == len(unit_entries) - 1 else min(end, cursor + raw_duration)
        timed_units.append({
            **segment,
            "start": cursor,
            "end": next_cursor,
            "text": entry["text"],
            "force_break_before": bool(entry.get("force_break_before", False)),
        })
        cursor = next_cursor
    return timed_units


def build_timed_units_from_words(segment: dict, unit_entries: list[dict], start: float, end: float) -> list[dict]:
    timeline = build_character_timeline(segment.get("words"))
    if not timeline:
        return []

    unit_lengths = [max(1, len(normalize_alignment_text(entry["text"]))) for entry in unit_entries]
    total_unit_length = sum(unit_lengths)
    total_chars = len(timeline)
    if total_unit_length <= 0 or total_chars <= 0:
        return []

    timed_units: list[dict] = []
    cursor = 0
    consumed_units = 0
    for index, entry in enumerate(unit_entries):
        consumed_units += unit_lengths[index]
        if index == len(unit_entries) - 1:
            next_cursor = total_chars
        else:
            next_cursor = round(total_chars * (consumed_units / total_unit_length))
            min_next = cursor + 1
            max_next = total_chars - (len(unit_entries) - index - 1)
            next_cursor = max(min_next, min(next_cursor, max_next))

        char_slice = timeline[cursor:next_cursor]
        if not char_slice:
            return []

        timed_units.append(
            {
                **segment,
                "start": max(start, float(char_slice[0]["start"])),
                "end": min(end, float(char_slice[-1]["end"])),
                "text": entry["text"],
                "force_break_before": bool(entry.get("force_break_before", False)),
            }
        )
        cursor = next_cursor
    return timed_units


def is_sentence_like(text: str) -> bool:
    normalized = text.strip()
    return bool(normalized) and normalized[-1] in PAGE_BREAK_PUNCTUATION


def max_duration_for_width(width: int) -> float:
    reading_duration = width / TARGET_READING_SPEED
    return min(ABSOLUTE_MAX_DURATION, max(TARGET_MAX_DURATION, reading_duration))


def merge_unreadable_groups(
    groups: list[list[dict]],
    max_group_width: int,
    min_duration: float,
    max_merge_gap: float,
) -> list[list[dict]]:
    merged = [list(group) for group in groups]
    index = 0
    while index < len(merged):
        group = merged[index]
        group_text = "".join(item["text"] for item in group)
        group_duration = float(group[-1]["end"]) - float(group[0]["start"])
        if text_width(group_text) >= MIN_FORCED_FRAGMENT_WIDTH and group_duration >= min_duration:
            index += 1
            continue

        candidates: list[tuple[int, int]] = []
        if index + 1 < len(merged):
            next_gap = float(merged[index + 1][0]["start"]) - float(group[-1]["end"])
            combined_width = text_width(group_text + "".join(item["text"] for item in merged[index + 1]))
            if next_gap <= max_merge_gap and combined_width <= max_group_width:
                candidates.append((0, index + 1))
        if index > 0:
            previous_gap = float(group[0]["start"]) - float(merged[index - 1][-1]["end"])
            combined_width = text_width("".join(item["text"] for item in merged[index - 1]) + group_text)
            if previous_gap <= max_merge_gap and combined_width <= max_group_width:
                candidates.append((1, index - 1))
        if not candidates:
            index += 1
            continue

        _, neighbor_index = min(candidates)
        if neighbor_index > index:
            merged[index] = group + merged[neighbor_index]
            del merged[neighbor_index]
        else:
            merged[neighbor_index].extend(group)
            del merged[index]
            index = max(0, neighbor_index)
    return merged


def finalize_group_segment(
    segment: dict,
    group: list[dict],
    next_group_start: float | None,
    subtitle_end_padding_seconds: float,
    subtitle_min_duration_seconds: float,
    use_word_timing: bool,
) -> dict:
    group_start = float(group[0]["start"])
    group_end = float(group[-1]["end"])
    segment_end_limit = float(segment["end"])
    adjusted_end = group_end + subtitle_end_padding_seconds if use_word_timing else group_end

    group_width = text_width("".join(item["text"] for item in group))
    upper_bound = min(segment_end_limit, group_start + max_duration_for_width(group_width))
    if next_group_start is not None:
        upper_bound = min(upper_bound, next_group_start)

    adjusted_end = min(adjusted_end, upper_bound)
    minimum_end = min(group_start + subtitle_min_duration_seconds, upper_bound)
    if adjusted_end < minimum_end:
        adjusted_end = minimum_end
    adjusted_end = max(group_start, adjusted_end)

    return {
        **segment,
        "start": group_start,
        "end": adjusted_end,
        "text": "".join(item["text"] for item in group).strip(),
        "layout_packed": True,
    }


def pack_segment_pages(
    segment: dict,
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
) -> list[dict]:
    text = segment.get("text", "").strip()
    if not text:
        return []

    require_japanese_layout_tools()

    start = float(segment["start"])
    end = float(segment["end"])
    has_word_timing = bool(segment.get("words"))
    forced_boundaries = gap_boundary_indices(segment.get("words"), subtitle_max_gap_seconds) if has_word_timing else set()
    unit_entries = split_into_atomic_unit_entries(text, forced_boundaries=forced_boundaries)
    if not unit_entries:
        return []

    timed_units = build_timed_units_from_words(segment, unit_entries, start, end)
    if not timed_units:
        timed_units = build_timed_units_from_width(segment, unit_entries, start, end)
        has_word_timing = False

    grouped: list[list[dict]] = []
    current_group: list[dict] = []
    current_duration = 0.0
    current_width = 0
    current_sentences = 0
    max_group_width = int(segment.get("max_width", DEFAULT_PAGE_WIDTH)) * MAX_LINES

    for unit in timed_units:
        unit_duration = float(unit["end"]) - float(unit["start"])
        unit_width = text_width(unit["text"])
        sentence_increment = 1 if is_sentence_like(unit["text"]) else 0
        next_sentence_count = current_sentences + sentence_increment
        should_split = bool(current_group) and (
            bool(unit.get("force_break_before"))
            or current_duration + unit_duration > max_duration_for_width(current_width + unit_width)
            or current_width + unit_width > max_group_width
            or next_sentence_count > 2
        )
        if should_split:
            grouped.append(current_group)
            current_group = []
            current_duration = 0.0
            current_width = 0
            current_sentences = 0
        current_group.append(unit)
        current_duration += unit_duration
        current_width += unit_width
        current_sentences += sentence_increment
    if current_group:
        grouped.append(current_group)

    grouped = merge_unreadable_groups(
        grouped,
        max_group_width,
        subtitle_min_duration_seconds,
        subtitle_max_gap_seconds,
    )

    results: list[dict] = []
    for index, group in enumerate(grouped):
        next_group_start = float(grouped[index + 1][0]["start"]) if index + 1 < len(grouped) else None
        group_start = float(group[0]["start"])
        group_end = float(group[-1]["end"])
        if group_end - group_start > ABSOLUTE_MAX_DURATION and len(group) > 1:
            midpoint = len(group) // 2
            results.append(
                finalize_group_segment(
                    segment,
                    group[:midpoint],
                    float(group[midpoint]["start"]),
                    subtitle_end_padding_seconds,
                    subtitle_min_duration_seconds,
                    has_word_timing,
                )
            )
            results.append(
                finalize_group_segment(
                    segment,
                    group[midpoint:],
                    next_group_start,
                    subtitle_end_padding_seconds,
                    subtitle_min_duration_seconds,
                    has_word_timing,
                )
            )
            continue
        results.append(
            finalize_group_segment(
                segment,
                group,
                next_group_start,
                subtitle_end_padding_seconds,
                subtitle_min_duration_seconds,
                has_word_timing,
            )
        )
    return results


def pack_event(segment: dict, default_max_width: int = 24) -> SubtitleEvent | None:
    speaker = segment.get("speaker", "Oz")
    text = segment.get("text", "").strip()
    if not text:
        return None

    emphasis = segment.get("emphasis", "normal")
    max_width = int(segment.get("max_width", default_max_width))
    display_duration = max(0.01, float(segment["end"]) - float(segment["start"]))
    return SubtitleEvent(
        start=float(segment["start"]),
        end=float(segment["end"]),
        speaker=speaker,
        text=normalize_text(text, max_width=max_width, display_duration=display_duration),
        emphasis=emphasis,
        position="bottom",
        layer=int(segment.get("layout_row", 0)),
        metadata={
            "source_text": text,
            "max_width": max_width,
            "display_duration": display_duration,
            "source_track": str(segment.get("source_track", "")),
            "source_speaker": str(segment.get("source_speaker", "")),
            "source_file": str(segment.get("source_file", "")),
        },
    )


def pack_segments(
    data: dict,
    default_max_width: int = 24,
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
) -> list[SubtitleEvent]:
    events: list[SubtitleEvent] = []
    for segment in data.get("segments", []):
        pages = [segment] if segment.get("layout_packed") else pack_segment_pages(
            segment,
            subtitle_max_gap_seconds=subtitle_max_gap_seconds,
            subtitle_end_padding_seconds=subtitle_end_padding_seconds,
            subtitle_min_duration_seconds=subtitle_min_duration_seconds,
        )
        for page in pages:
            event = pack_event(page, default_max_width=default_max_width)
            if event is not None:
                events.append(event)
    return events
