"""字幕テキストの改行候補・幅調整・省略表示を扱う。"""

from __future__ import annotations

from typing import Protocol

from .rules import (
    ELLIPSIS,
    LEADING_AVOID_CHARS,
    LEFT_BOUNDARY_AVOID_WORDS,
    MAX_LINES,
    RIGHT_BOUNDARY_AVOID_WORDS,
    SOFT_BREAK_CHARS,
    STRONG_BREAK_CHARS,
    TRAILING_AVOID_CHARS,
)
from .scoring import (
    BreakScore as BreakScore,
    candidate_kind_bonus_with_boundaries,
    connected_char_penalty,
    display_width,
    is_protected_inline_split,
    leading_boundary_penalty,
    score_break_with_bonus,
    text_width,
)
from .tokenize import ChunkParser


class WrappingHooks(Protocol):
    """既存の subtitle_packer で公開された差し替え点を改行処理へ渡す。"""

    def create_budoux_parser(self) -> ChunkParser | None: ...
    def parse_budoux_chunks(self, text: str) -> list[str]: ...
    def budoux_boundaries(self, text: str) -> set[int]: ...
    def morpheme_boundaries(self, text: str) -> set[int]: ...
    def candidate_kind_bonus(self, text: str, break_index: int) -> int: ...
    def best_chunk_split_index(self, current: list[str], max_width: int) -> int | None: ...
    def split_by_width(self, text: str, max_width: int) -> list[str]: ...
    def split_by_width_naturally(self, text: str, max_width: int) -> list[str]: ...
    def chunk_text(self, text: str, max_width: int) -> list[str]: ...
    def break_candidates(self, text: str, max_width: int) -> list[int]: ...
    def score_break(
        self, text: str, break_index: int, max_width: int, display_duration: float | None = None
    ) -> BreakScore: ...
    def build_two_line_candidate(
        self, text: str, max_width: int, display_duration: float | None = None
    ) -> str | None: ...
    def has_awkward_boundary(self, left: str, right: str) -> bool: ...
    def score_truncated_break(
        self, text: str, break_index: int, max_width: int, display_duration: float | None = None
    ) -> BreakScore: ...
    def build_truncated_two_line_candidate(
        self, lines: list[str], max_width: int, max_lines: int, display_duration: float | None = None
    ) -> list[str] | None: ...
    def truncate_visible_lines(
        self, lines: list[str], max_width: int, max_lines: int, display_duration: float | None = None
    ) -> str: ...


def candidate_kind_bonus(text: str, break_index: int, *, hooks: WrappingHooks) -> int:
    return candidate_kind_bonus_with_boundaries(text, break_index, hooks.budoux_boundaries, hooks.morpheme_boundaries)


def best_chunk_split_index(current: list[str], max_width: int, *, hooks: WrappingHooks) -> int | None:
    if len(current) <= 1:
        return None

    full_text = "".join(current)
    candidates: list[tuple[tuple[int, int, int, int, int, int], int]] = []
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
        boundary_bonus = hooks.candidate_kind_bonus(full_text, char_break_index)
        leading_penalty = leading_boundary_penalty(full_text, char_break_index)
        score = (
            hard_penalty,
            soft_penalty + leading_penalty,
            short_left_penalty,
            boundary_bonus,
            width_balance_penalty,
            width_slack_penalty,
        )
        candidates.append((score, index))

    if not candidates:
        return None

    balanced_candidates = [
        item
        for item in candidates
        if min(text_width("".join(current[: item[1]]).rstrip()), text_width("".join(current[item[1] :]).lstrip())) > 5
    ]
    pool = balanced_candidates or candidates

    def candidate_score(item: tuple[tuple[int, int, int, int, int, int], int]) -> tuple[int, int, int, int, int, int]:
        return item[0]

    return min(pool, key=candidate_score)[1]


def split_by_width_naturally(text: str, max_width: int, *, hooks: WrappingHooks) -> list[str]:
    remaining = list(text)
    chunks: list[str] = []
    while text_width("".join(remaining)) > max_width:
        split_index = hooks.best_chunk_split_index(remaining, max_width)
        if split_index is None:
            return chunks + hooks.split_by_width("".join(remaining), max_width)
        left = "".join(remaining[:split_index]).strip()
        if left:
            chunks.append(left)
        remaining = remaining[split_index:]
    tail = "".join(remaining).strip()
    if tail:
        chunks.append(tail)
    return chunks


def chunk_text(text: str, max_width: int, *, hooks: WrappingHooks) -> list[str]:
    parser = hooks.create_budoux_parser()
    if parser is None:
        return hooks.split_by_width(text, max_width)

    pieces = hooks.parse_budoux_chunks(text)
    if not pieces:
        return []
    if len(pieces) == 1 and text_width(pieces[0]) > max_width:
        return hooks.split_by_width_naturally(text, max_width)

    chunks: list[str] = []
    current: list[str] = []
    current_width = 0

    for piece in pieces:
        piece_width = text_width(piece)
        if current and current_width + piece_width > max_width:
            split_index = hooks.best_chunk_split_index(current, max_width)
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


def break_candidates(text: str, max_width: int, *, hooks: WrappingHooks) -> list[int]:
    candidates: set[int] = set()
    primary_boundaries = hooks.budoux_boundaries(text)
    candidates.update(primary_boundaries or hooks.morpheme_boundaries(text))

    for index in range(1, len(text)):
        if (
            text[index - 1].isspace()
            or text[index] in SOFT_BREAK_CHARS
            or text[index - 1] in (STRONG_BREAK_CHARS | SOFT_BREAK_CHARS)
        ):
            candidates.add(index)

    if not candidates:
        for index in range(1, len(text)):
            if is_protected_inline_split(text[index - 1], text[index]):
                continue
            if text_width(text[:index]) <= max_width * 1.45 and text_width(text[index:]) <= max_width * 1.45:
                candidates.add(index)

    return sorted(candidate for candidate in candidates if 0 < candidate < len(text))


def score_break(
    text: str, break_index: int, max_width: int, display_duration: float | None = None, *, hooks: WrappingHooks
) -> BreakScore:
    return score_break_with_bonus(text, break_index, max_width, display_duration, hooks.candidate_kind_bonus)


def build_two_line_candidate(
    text: str, max_width: int, display_duration: float | None = None, *, hooks: WrappingHooks
) -> str | None:
    candidates = hooks.break_candidates(text, max_width)
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

    def candidate_score(candidate: int) -> BreakScore:
        return hooks.score_break(text, candidate, max_width, display_duration=display_duration)

    break_index = min(viable_candidates, key=candidate_score)
    return text[:break_index].rstrip() + r"\N" + text[break_index:].lstrip()


def has_awkward_boundary(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return connected_char_penalty(left[-1], right[0]) >= 8


def score_truncated_break(
    text: str, break_index: int, max_width: int, display_duration: float | None = None, *, hooks: WrappingHooks
) -> BreakScore:
    return hooks.score_break(text, break_index, max_width, display_duration=display_duration)


def build_truncated_two_line_candidate(
    lines: list[str], max_width: int, max_lines: int, display_duration: float | None = None, *, hooks: WrappingHooks
) -> list[str] | None:
    best_choice: tuple[tuple[int, int, int, int, int, int, int, int, int], list[str]] | None = None
    max_source = min(len(lines), max_lines + 2)
    for source_limit in range(max_lines, max_source + 1):
        joined = "".join(lines[:source_limit]).strip()
        if not joined:
            continue
        candidates = hooks.break_candidates(joined, max_width)
        for candidate in candidates:
            left = joined[:candidate].rstrip()
            right = joined[candidate:].lstrip()
            if not left or not right:
                continue
            left_width = text_width(left)
            right_width = text_width(right)
            if left_width > max_width * 1.45 or right_width > max_width * 1.45:
                continue
            score: tuple[int, int, int, int, int, int, int, int, int] = (
                *hooks.score_truncated_break(joined, candidate, max_width, display_duration=display_duration),
                -source_limit,
            )
            visible = [left, right]
            if best_choice is None or score < best_choice[0]:
                best_choice = (score, visible)
    return None if best_choice is None else best_choice[1]


def truncate_visible_lines(
    lines: list[str], max_width: int, max_lines: int, display_duration: float | None = None, *, hooks: WrappingHooks
) -> str:
    visible = lines[:max_lines]
    if max_lines == 2 and len(visible) >= 2:
        first_width = text_width(visible[0])
        second_width = text_width(visible[1])
        if first_width <= 6 or second_width <= 8 or hooks.has_awkward_boundary(visible[0], visible[1]):
            rebalanced = hooks.build_truncated_two_line_candidate(
                lines, max_width, max_lines, display_duration=display_duration
            )
            if rebalanced is not None:
                visible = rebalanced

    last_line = visible[-1]
    if last_line and last_line[-1].isascii() and last_line[-1].isalnum():
        visible[-1] = last_line + ELLIPSIS
    else:
        visible[-1] = last_line[:-1] + ELLIPSIS if len(last_line) >= 2 else last_line + ELLIPSIS
    return r"\N".join(visible)


def normalize_text(
    text: str,
    max_width: int = 24,
    max_lines: int = MAX_LINES,
    display_duration: float | None = None,
    *,
    hooks: WrappingHooks,
) -> str:
    normalized_source = str(text).replace("\r\n", "\n").replace("\r", "\n").replace(r"\N", "\n").strip()
    if "\n" in normalized_source:
        wrapped_lines: list[str] = []
        for line in normalized_source.split("\n"):
            compact_line = " ".join(line.split())
            if not compact_line:
                wrapped_lines.append("")
                continue
            wrapped_lines.extend(hooks.chunk_text(compact_line, max_width))
        return r"\N".join(wrapped_lines)

    compact = " ".join(normalized_source.split())
    if not compact:
        return compact

    if max_lines <= 1:
        chunks = hooks.chunk_text(compact, max_width)
        return chunks[0] if len(chunks) <= 1 else chunks[0][:-1] + ELLIPSIS

    compact_width = text_width(compact)
    if compact_width <= max_width:
        return compact
    if compact_width <= max_width + 2 and compact[-1] in STRONG_BREAK_CHARS | SOFT_BREAK_CHARS:
        return compact

    if max_lines == 2 and compact_width <= max_width * 2.5:
        two_line_candidate = hooks.build_two_line_candidate(compact, max_width, display_duration=display_duration)
        if two_line_candidate is not None:
            return two_line_candidate

    chunks = hooks.chunk_text(compact, max_width)
    if len(chunks) > max_lines:
        return hooks.truncate_visible_lines(chunks, max_width, max_lines, display_duration=display_duration)
    return r"\N".join(chunks)


def split_by_width(text: str, max_width: int) -> list[str]:
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
