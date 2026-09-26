from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from typing import TypedDict
from unicodedata import category

from .data_boundary import coerce_float, coerce_int, is_object_dict, is_object_mapping, is_object_sequence
from .typed_cache import typed_lru_cache

from .models import SubtitleEvent
from .subtitle_layout import wrapping
from .subtitle_layout.rules import (
    CLAUSE_BREAK_TOKENS as CLAUSE_BREAK_TOKENS,
    ELLIPSIS as ELLIPSIS,
    LEADING_AVOID_CHARS as LEADING_AVOID_CHARS,
    LEADING_BOUNDARY_PENALTIES as LEADING_BOUNDARY_PENALTIES,
    LEFT_BOUNDARY_AVOID_WORDS as LEFT_BOUNDARY_AVOID_WORDS,
    MAX_LINES as MAX_LINES,
    RIGHT_BOUNDARY_AVOID_WORDS as RIGHT_BOUNDARY_AVOID_WORDS,
    SOFT_BREAK_CHARS as SOFT_BREAK_CHARS,
    STRONG_BREAK_CHARS as STRONG_BREAK_CHARS,
    TRAILING_AVOID_CHARS as TRAILING_AVOID_CHARS,
)
from .subtitle_layout.scoring import (
    TARGET_READING_SPEED as TARGET_READING_SPEED,
    TIMING_BALANCE_WEIGHT as TIMING_BALANCE_WEIGHT,
    char_bucket as char_bucket,
    chunk_boundaries as chunk_boundaries,
    clause_break_bonus as clause_break_bonus,
    connected_char_penalty as connected_char_penalty,
    display_width as display_width,
    duration_pressure as duration_pressure,
    is_protected_inline_split as is_protected_inline_split,
    leading_boundary_penalty as leading_boundary_penalty,
    text_width as text_width,
    timing_balance_penalty as timing_balance_penalty,
)
from .subtitle_layout.tokenize import (
    ChunkParser,
    create_budoux_parser as create_budoux_parser,
    create_janome_tokenizer as create_janome_tokenizer,
)


class _AtomicUnitText(TypedDict):
    text: str


class AtomicUnitEntry(_AtomicUnitText, total=False):
    force_break_before: bool


class CharacterTiming(TypedDict):
    start: float
    end: float


def _mapping(value: object) -> Mapping[object, object]:
    """拡張フィールドを保持したまま、読み取りに必要な辞書構造を検証する。"""
    if not is_object_mapping(value):
        raise TypeError("subtitle payload must be a mapping")
    return value


def _dictionary(value: object) -> dict[object, object]:
    """更新可能な辞書として返す公開APIでは元の辞書参照を維持する。"""
    if not is_object_dict(value):
        raise TypeError("subtitle payload must be a dictionary")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("subtitle text must be a string")
    return value


def _entry_mappings(value: object) -> list[Mapping[object, object]]:
    if not is_object_sequence(value) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError("subtitle entries must be a sequence of mappings")
    return [_mapping(item) for item in value]


def _number(value: object) -> float:
    """処理中の通常のfloatはそのまま返し、それ以外は共通境界で変換する。"""
    if isinstance(value, float) and type(value) is float:
        return value
    return coerce_float(value)


def parse_budoux_chunks(text: str) -> list[str]:
    parser = create_budoux_parser()
    if parser is None or not text:
        return [text] if text else []
    chunks = [chunk for chunk in parser.parse(text) if chunk]
    return chunks or [text]


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


@typed_lru_cache(maxsize=4096)
def budoux_boundaries(text: str) -> set[int]:
    return chunk_boundaries(text, parse_budoux_chunks(text))


@typed_lru_cache(maxsize=4096)
def morpheme_boundaries(text: str) -> set[int]:
    return chunk_boundaries(text, parse_morpheme_chunks(text))


def candidate_kind_bonus(text: str, break_index: int) -> int:
    return wrapping.candidate_kind_bonus(text, break_index, hooks=_WRAPPING_HOOKS)


def best_chunk_split_index(current: list[str], max_width: int) -> int | None:
    return wrapping.best_chunk_split_index(current, max_width, hooks=_WRAPPING_HOOKS)


def split_by_width_naturally(text: str, max_width: int) -> list[str]:
    return wrapping.split_by_width_naturally(text, max_width, hooks=_WRAPPING_HOOKS)


def chunk_text(text: str, max_width: int) -> list[str]:
    return wrapping.chunk_text(text, max_width, hooks=_WRAPPING_HOOKS)


def break_candidates(text: str, max_width: int) -> list[int]:
    return wrapping.break_candidates(text, max_width, hooks=_WRAPPING_HOOKS)


def score_break(
    text: str, break_index: int, max_width: int, display_duration: float | None = None
) -> wrapping.BreakScore:
    return wrapping.score_break(text, break_index, max_width, display_duration, hooks=_WRAPPING_HOOKS)


def build_two_line_candidate(text: str, max_width: int, display_duration: float | None = None) -> str | None:
    return wrapping.build_two_line_candidate(text, max_width, display_duration, hooks=_WRAPPING_HOOKS)


def has_awkward_boundary(left: str, right: str) -> bool:
    return wrapping.has_awkward_boundary(left, right)


def score_truncated_break(
    text: str, break_index: int, max_width: int, display_duration: float | None = None
) -> wrapping.BreakScore:
    return wrapping.score_truncated_break(text, break_index, max_width, display_duration, hooks=_WRAPPING_HOOKS)


def build_truncated_two_line_candidate(
    lines: list[str], max_width: int, max_lines: int, display_duration: float | None = None
) -> list[str] | None:
    return wrapping.build_truncated_two_line_candidate(
        lines, max_width, max_lines, display_duration, hooks=_WRAPPING_HOOKS
    )


def truncate_visible_lines(
    lines: list[str], max_width: int, max_lines: int, display_duration: float | None = None
) -> str:
    return wrapping.truncate_visible_lines(lines, max_width, max_lines, display_duration, hooks=_WRAPPING_HOOKS)


def normalize_text(
    text: str, max_width: int = 24, max_lines: int = MAX_LINES, display_duration: float | None = None
) -> str:
    return wrapping.normalize_text(text, max_width, max_lines, display_duration, hooks=_WRAPPING_HOOKS)


class _PackerWrappingHooks:
    """旧公開関数を毎回参照して mock.patch と既存呼び出しを維持する。"""

    def create_budoux_parser(self) -> ChunkParser | None:
        return create_budoux_parser()

    def parse_budoux_chunks(self, text: str) -> list[str]:
        return parse_budoux_chunks(text)

    def budoux_boundaries(self, text: str) -> set[int]:
        return budoux_boundaries(text)

    def morpheme_boundaries(self, text: str) -> set[int]:
        return morpheme_boundaries(text)

    def candidate_kind_bonus(self, text: str, break_index: int) -> int:
        return candidate_kind_bonus(text, break_index)

    def best_chunk_split_index(self, current: list[str], max_width: int) -> int | None:
        return best_chunk_split_index(current, max_width)

    def split_by_width(self, text: str, max_width: int) -> list[str]:
        return split_by_width(text, max_width)

    def split_by_width_naturally(self, text: str, max_width: int) -> list[str]:
        return split_by_width_naturally(text, max_width)

    def chunk_text(self, text: str, max_width: int) -> list[str]:
        return chunk_text(text, max_width)

    def break_candidates(self, text: str, max_width: int) -> list[int]:
        return break_candidates(text, max_width)

    def score_break(
        self, text: str, break_index: int, max_width: int, display_duration: float | None = None
    ) -> wrapping.BreakScore:
        return score_break(text, break_index, max_width, display_duration)

    def build_two_line_candidate(self, text: str, max_width: int, display_duration: float | None = None) -> str | None:
        return build_two_line_candidate(text, max_width, display_duration)

    def has_awkward_boundary(self, left: str, right: str) -> bool:
        return has_awkward_boundary(left, right)

    def score_truncated_break(
        self, text: str, break_index: int, max_width: int, display_duration: float | None = None
    ) -> wrapping.BreakScore:
        return score_truncated_break(text, break_index, max_width, display_duration)

    def build_truncated_two_line_candidate(
        self, lines: list[str], max_width: int, max_lines: int, display_duration: float | None = None
    ) -> list[str] | None:
        return build_truncated_two_line_candidate(lines, max_width, max_lines, display_duration)

    def truncate_visible_lines(
        self, lines: list[str], max_width: int, max_lines: int, display_duration: float | None = None
    ) -> str:
        return truncate_visible_lines(lines, max_width, max_lines, display_duration)


_WRAPPING_HOOKS: wrapping.WrappingHooks = _PackerWrappingHooks()


TARGET_MIN_DURATION = 0.8
TARGET_MAX_DURATION = 2.8
ABSOLUTE_MAX_DURATION = 3.6
DEFAULT_PAGE_WIDTH = 28
MAX_UNIT_WIDTH = DEFAULT_PAGE_WIDTH * MAX_LINES
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


def normalize_alignment_text(text: object) -> str:
    return "".join(str(text).split())


def split_by_width(text: str, max_width: int = MAX_UNIT_WIDTH) -> list[str]:
    return wrapping.split_by_width(text, max_width)


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

        def distance(candidate: int) -> tuple[int, int]:
            return (abs(candidate - boundary), candidate)

        best = min(candidates, key=distance)
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


def split_into_atomic_unit_entries(text: str, forced_boundaries: set[int] | None = None) -> list[AtomicUnitEntry]:
    normalized = " ".join(text.split())
    if not normalized:
        return []

    snapped_boundaries = snap_forced_boundaries(normalized, forced_boundaries or set())
    fragments = split_text_by_boundaries(normalized, snapped_boundaries)
    entries: list[AtomicUnitEntry] = []
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


def build_character_timeline(words: object) -> list[CharacterTiming]:
    timeline: list[CharacterTiming] = []
    for word in _entry_mappings([] if words is None else words):
        normalized = normalize_alignment_text(word.get("word", ""))
        start = word.get("start")
        end = word.get("end")
        if not normalized or start is None or end is None:
            continue

        start_time = _number(start)
        end_time = _effective_word_end_mapping(word)
        if end_time <= start_time:
            continue

        duration = end_time - start_time
        length = len(normalized)
        for index, _ in enumerate(normalized):
            char_start = start_time + duration * (index / length)
            char_end = start_time + duration * ((index + 1) / length)
            timeline.append({"start": char_start, "end": char_end})
    return timeline


def _ordered_character_timeline(timeline: Sequence[CharacterTiming]) -> bool:
    """本文順と矛盾する語時刻はページの時刻配分に使わない。"""
    return all(
        current["start"] >= previous["end"] - 1e-9
        for previous, current in zip(timeline, timeline[1:])
    )


def effective_word_end(word: object) -> float:
    return _effective_word_end_mapping(_mapping(word))


def _effective_word_end_mapping(word: Mapping[object, object]) -> float:
    """検証済み単語の読み取りではマッピング検査を重ねない。"""
    start = _number(word["start"])
    end = _number(word["end"])
    character_count = max(1, len(normalize_alignment_text(word.get("word", ""))))
    return min(end, start + MAX_ALIGNED_CHARACTER_DURATION_SECONDS * character_count)


def gap_boundary_indices(words: object, max_gap_seconds: float) -> set[int]:
    boundaries: set[int] = set()
    words = _entry_mappings([] if words is None else words)
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
        inferred_end = _effective_word_end_mapping(words[index])
        if _number(next_start) - inferred_end >= max_gap_seconds and cursor > 0:
            boundaries.add(cursor)
    return boundaries


def split_words_on_gaps(words: object, max_gap_seconds: float) -> list[list[dict[object, object]]]:
    valid_words: list[dict[object, object]] = []
    for word in _entry_mappings([] if words is None else words):
        normalized = normalize_alignment_text(word.get("word", ""))
        start = word.get("start")
        end = word.get("end")
        if not normalized or start is None or end is None:
            continue
        if _number(end) <= _number(start):
            continue
        valid_words.append(_dictionary(word))

    if not valid_words:
        return []

    groups: list[list[dict[object, object]]] = [[valid_words[0]]]
    for word in valid_words[1:]:
        previous = groups[-1][-1]
        gap = _number(word["start"]) - _number(previous["end"])
        if gap >= max_gap_seconds:
            groups.append([word])
        else:
            groups[-1].append(word)
    return groups


def build_segment_text_from_words(words: object) -> str:
    return "".join(str(word.get("word", "")) for word in _entry_mappings(words)).strip()


def split_segment_by_word_gaps(segment: object, max_gap_seconds: float) -> list[dict[object, object]]:
    segment = _dictionary(segment)
    word_groups = split_words_on_gaps(segment.get("words"), max_gap_seconds)
    if len(word_groups) <= 1:
        return [segment]

    split_segments: list[dict[object, object]] = []
    for group in word_groups:
        split_segments.append(
            {
                **segment,
                "start": _number(group[0]["start"]),
                "end": _number(group[-1]["end"]),
                "text": build_segment_text_from_words(group) or segment.get("text", ""),
                "words": group,
            }
        )
    return split_segments


def build_timed_units_from_width(segment: object, unit_entries: Sequence[AtomicUnitEntry], start: float, end: float) -> list[dict[object, object]]:
    segment = _mapping(segment)
    total_duration = max(0.01, end - start)
    texts = [entry["text"] for entry in unit_entries]
    widths = [max(1, text_width(text)) for text in texts]
    total_width = sum(widths)

    timed_units: list[dict[object, object]] = []
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


def build_timed_units_from_words(segment: object, unit_entries: Sequence[AtomicUnitEntry], start: float, end: float) -> list[dict[object, object]]:
    segment = _mapping(segment)
    timeline = build_character_timeline(segment.get("words"))
    if not timeline or not _ordered_character_timeline(timeline):
        return []

    words = _entry_mappings(segment.get("words"))
    word_text = "".join(normalize_alignment_text(word.get("word", "")) for word in words)
    source_text = normalize_alignment_text("".join(entry["text"] for entry in unit_entries))
    word_positions = (
        _aligned_word_positions(source_text, word_text)
        if all(isinstance(word.get("word"), str) for word in words) and len(timeline) == len(word_text)
        else None
    )
    if word_positions is None:
        # 不一致を比例配分すると一部の語時刻へ本文全体を押し込むため、幅による時刻配分に戻す。
        return []

    aligned_units: list[dict[object, object]] = []
    source_cursor = 0
    for entry in unit_entries:
        next_source_cursor = source_cursor + len(normalize_alignment_text(entry["text"]))
        first_char = bisect_left(word_positions, source_cursor)
        next_char = bisect_left(word_positions, next_source_cursor)
        if first_char < next_char:
            unit_start = timeline[first_char]["start"]
            unit_end = timeline[next_char - 1]["end"]
        else:
            # 語時刻のない句読点は隣接する発話時刻に置き、後続語の時刻を消費しない。
            boundary = timeline[first_char - 1]["end"] if first_char else timeline[0]["start"]
            unit_start = boundary
            unit_end = boundary
        aligned_units.append({
            **segment,
            "start": max(start, min(unit_start, end)),
            "end": max(start, min(unit_end, end)),
            "text": entry["text"],
            "force_break_before": bool(entry.get("force_break_before", False)),
        })
        source_cursor = next_source_cursor
    return aligned_units


def is_sentence_like(text: str) -> bool:
    normalized = text.strip()
    return bool(normalized) and normalized[-1] in PAGE_BREAK_PUNCTUATION


def max_duration_for_width(width: int) -> float:
    reading_duration = width / TARGET_READING_SPEED
    return min(ABSOLUTE_MAX_DURATION, max(TARGET_MAX_DURATION, reading_duration))


def merge_unreadable_groups(
    groups: list[list[dict[object, object]]],
    max_group_width: int,
    min_duration: float,
    max_merge_gap: float,
) -> list[list[dict[object, object]]]:
    merged = [list(group) for group in groups]
    index = 0
    while index < len(merged):
        group = merged[index]
        group_text = "".join(_text(item["text"]) for item in group)
        group_duration = _number(group[-1]["end"]) - _number(group[0]["start"])
        if text_width(group_text) >= MIN_FORCED_FRAGMENT_WIDTH and group_duration >= min_duration:
            index += 1
            continue

        candidates: list[tuple[int, int]] = []
        if index + 1 < len(merged):
            next_gap = _number(merged[index + 1][0]["start"]) - _number(group[-1]["end"])
            combined_width = text_width(group_text + "".join(_text(item["text"]) for item in merged[index + 1]))
            if next_gap <= max_merge_gap and combined_width <= max_group_width:
                candidates.append((0, index + 1))
        if index > 0:
            previous_gap = _number(group[0]["start"]) - _number(merged[index - 1][-1]["end"])
            combined_width = text_width("".join(_text(item["text"]) for item in merged[index - 1]) + group_text)
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
    segment: object,
    group: list[dict[object, object]],
    next_group_start: float | None,
    subtitle_end_padding_seconds: float,
    subtitle_min_duration_seconds: float,
    use_word_timing: bool,
) -> dict[object, object]:
    segment = _mapping(segment)
    group_start = _number(group[0]["start"])
    group_end = _number(group[-1]["end"])
    segment_end_limit = _number(segment["end"])
    adjusted_end = group_end + subtitle_end_padding_seconds if use_word_timing else group_end

    group_width = text_width("".join(_text(item["text"]) for item in group))
    duration_limit = max_duration_for_width(group_width)
    if use_word_timing and group_end - group_start <= ABSOLUTE_MAX_DURATION:
        duration_limit = max(duration_limit, group_end - group_start)
    upper_bound = min(segment_end_limit, group_start + duration_limit)
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
        "text": "".join(_text(item["text"]) for item in group).strip(),
        "layout_packed": True,
    }


def _page_preserves_text(text: str, max_width: int, max_lines: int = MAX_LINES) -> bool:
    """描画時に省略される組み合わせをページ確定前に除外する。"""
    rendered = normalize_text(text, max_width=max_width, max_lines=max_lines)
    return normalize_alignment_text(rendered.replace(r"\N", "")) == normalize_alignment_text(text.replace(r"\N", ""))


def _aligned_word_positions(page_text: str, word_text: str) -> list[int] | None:
    """アラインメントにない句読点だけを飛ばして語の文字位置を対応させる。"""
    positions: list[int] = []
    cursor = 0
    for char in word_text:
        while cursor < len(page_text) and page_text[cursor] != char:
            if not category(page_text[cursor]).startswith("P"):
                return None
            cursor += 1
        if cursor >= len(page_text):
            return None
        positions.append(cursor)
        cursor += 1
    if any(not category(char).startswith("P") for char in page_text[cursor:]):
        return None
    return positions


def _assign_page_words(segment: Mapping[object, object], pages: list[dict[object, object]]) -> None:
    """ページ本文に対応する語だけを保持し、後の結合で語が重複しないようにする。"""
    if not pages or not segment.get("words"):
        return
    words = _entry_mappings(segment["words"])
    normalized_words = [normalize_alignment_text(word.get("word", "")) for word in words]
    timeline = build_character_timeline(words)
    page_lengths = [len(normalize_alignment_text(page["text"])) for page in pages]
    page_text = normalize_alignment_text("".join(_text(page["text"]) for page in pages))
    word_positions = _aligned_word_positions(page_text, "".join(normalized_words))
    can_partition_words = (
        all(isinstance(word.get("word"), str) for word in words)
        and len(timeline) == sum(len(item) for item in normalized_words)
        and _ordered_character_timeline(timeline)
        and word_positions is not None
    )
    assigned: list[list[dict[object, object]]] = [[] for _ in pages]
    if can_partition_words:
        assert word_positions is not None
        page_start = 0
        for page_index, page_length in enumerate(page_lengths):
            page_end = page_start + page_length
            page_word_start = bisect_left(word_positions, page_start)
            page_word_end = bisect_left(word_positions, page_end)
            word_start = 0
            for word, normalized in zip(words, normalized_words):
                word_end = word_start + len(normalized)
                overlap_start = max(page_word_start, word_start)
                overlap_end = min(page_word_end, word_end)
                if overlap_start < overlap_end:
                    fragment = dict(word)
                    if overlap_start != word_start or overlap_end != word_end:
                        fragment["word"] = normalized[overlap_start - word_start:overlap_end - word_start]
                        fragment["start"] = timeline[overlap_start]["start"]
                        fragment["end"] = timeline[overlap_end - 1]["end"]
                    page_time_start = _number(pages[page_index]["start"])
                    page_time_end = _number(pages[page_index]["end"])
                    if _number(fragment["end"]) > page_time_start and _number(fragment["start"]) < page_time_end:
                        fragment_start = max(page_time_start, min(_number(fragment["start"]), page_time_end))
                        fragment["start"] = fragment_start
                        fragment["end"] = max(fragment_start, min(_number(fragment["end"]), page_time_end))
                        assigned[page_index].append(fragment)
                word_start = word_end
            page_start = page_end
    else:
        # 本文と合わない語を時刻だけで推測配置しない。文字と時刻が収まる語だけ残す。
        for word in words:
            start = word.get("start")
            end = word.get("end")
            normalized = normalize_alignment_text(word.get("word", ""))
            if start is None or end is None or not normalized:
                continue
            word_time_start = _number(start)
            word_time_end = _number(end)
            for index, page in enumerate(pages):
                if (
                    _number(page["start"]) <= word_time_start <= word_time_end <= _number(page["end"])
                    and normalized in normalize_alignment_text(page["text"])
                ):
                    assigned[index].append(dict(word))
                    break
    for page, page_words in zip(pages, assigned):
        page["words"] = page_words


def pack_segment_pages(
    segment: object,
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
) -> list[dict[object, object]]:
    segment = _mapping(segment)
    text = _text(segment.get("text", "")).strip()
    if not text:
        return []

    require_japanese_layout_tools()

    start = _number(segment["start"])
    end = _number(segment["end"])
    has_word_timing = bool(segment.get("words"))
    forced_boundaries = gap_boundary_indices(segment.get("words"), subtitle_max_gap_seconds) if has_word_timing else set()
    max_width = coerce_int(segment.get("max_width", DEFAULT_PAGE_WIDTH))
    max_lines = 1 if str(segment.get("subtitle_line_count", "auto")).strip() == "1" else MAX_LINES
    segment = {**segment, "max_width": max_width}
    raw_entries = split_into_atomic_unit_entries(text, forced_boundaries=forced_boundaries)
    unit_entries: list[AtomicUnitEntry] = []
    for entry in raw_entries:
        parts = (
            [entry["text"]]
            if _page_preserves_text(entry["text"], max_width, max_lines)
            else split_by_width_naturally(entry["text"], max_width)
        )
        for index, part in enumerate(parts):
            unit_entries.append({
                **entry,
                "text": part,
                "force_break_before": index == 0 and entry.get("force_break_before", False),
            })
    if not unit_entries:
        return []

    timed_units = build_timed_units_from_words(segment, unit_entries, start, end)
    word_boundaries: list[int] = []
    if timed_units:
        words = _entry_mappings(segment["words"])
        normalized_words = [normalize_alignment_text(word["word"]) for word in words]
        source_text = normalize_alignment_text("".join(entry["text"] for entry in unit_entries))
        word_positions = _aligned_word_positions(source_text, "".join(normalized_words))
        assert word_positions is not None
        word_cursor = 0
        for word_text in normalized_words[:-1]:
            word_cursor += len(word_text)
            if 0 < word_cursor < len(word_positions):
                word_boundaries.append(word_positions[word_cursor])
    while timed_units:
        expanded_entries: list[AtomicUnitEntry] = []
        source_cursor = 0
        for entry, unit in zip(unit_entries, timed_units):
            parts = [entry["text"]]
            next_source_cursor = source_cursor + len(normalize_alignment_text(entry["text"]))
            crosses_words = bisect_right(word_boundaries, source_cursor) < bisect_left(word_boundaries, next_source_cursor)
            if crosses_words and _number(unit["end"]) - _number(unit["start"]) > ABSOLUTE_MAX_DURATION:
                narrower_width = max(1, text_width(entry["text"]) // 2)
                parts = split_by_width_naturally(entry["text"], narrower_width)
            for index, part in enumerate(parts):
                expanded_entries.append({
                    **entry,
                    "text": part,
                    "force_break_before": index == 0 and entry.get("force_break_before", False),
                })
            source_cursor = next_source_cursor
        if len(expanded_entries) == len(unit_entries):
            break
        unit_entries = expanded_entries
        timed_units = build_timed_units_from_words(segment, unit_entries, start, end)
    if not timed_units:
        timed_units = build_timed_units_from_width(segment, unit_entries, start, end)
        has_word_timing = False

    grouped: list[list[dict[object, object]]] = []
    current_group: list[dict[object, object]] = []
    current_duration = 0.0
    current_width = 0
    current_sentences = 0
    max_group_width = max_width * max_lines

    for unit in timed_units:
        unit_duration = _number(unit["end"]) - _number(unit["start"])
        unit_width = text_width(_text(unit["text"]))
        sentence_increment = 1 if is_sentence_like(_text(unit["text"])) else 0
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

    # 結合後の実際の描画を確認し、認識済みの文字を省略する前に次ページへ送る。
    duration_groups: list[list[dict[object, object]]] = []
    for group in grouped:
        duration_fitting: list[dict[object, object]] = []
        for unit in group:
            if duration_fitting and _number(unit["end"]) - _number(duration_fitting[0]["start"]) > ABSOLUTE_MAX_DURATION:
                duration_groups.append(duration_fitting)
                duration_fitting = []
            duration_fitting.append(unit)
        if duration_fitting:
            duration_groups.append(duration_fitting)
    fitting_groups: list[list[dict[object, object]]] = []
    for group in duration_groups:
        fitting: list[dict[object, object]] = []
        for unit in group:
            combined = "".join(_text(item["text"]) for item in fitting) + _text(unit["text"])
            if fitting and not _page_preserves_text(combined, max_width, max_lines):
                fitting_groups.append(fitting)
                fitting = []
            fitting.append(unit)
        if fitting:
            fitting_groups.append(fitting)
    grouped = fitting_groups

    results: list[dict[object, object]] = []
    for index, group in enumerate(grouped):
        next_group_start = _number(grouped[index + 1][0]["start"]) if index + 1 < len(grouped) else None
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
    _assign_page_words(segment, results)
    return results


def pack_event(segment: object, default_max_width: int = 24) -> SubtitleEvent | None:
    segment = _mapping(segment)
    speaker = _text(segment.get("speaker", "Oz"))
    text = _text(segment.get("text", "")).strip()
    if not text:
        return None

    emphasis = _text(segment.get("emphasis", "normal"))
    max_width = coerce_int(segment.get("max_width", default_max_width))
    display_duration = max(0.01, _number(segment["end"]) - _number(segment["start"]))
    return SubtitleEvent(
        start=_number(segment["start"]),
        end=_number(segment["end"]),
        speaker=speaker,
        text=normalize_text(text, max_width=max_width, display_duration=display_duration),
        emphasis=emphasis,
        position="bottom",
        layer=coerce_int(segment.get("layout_row", 0)),
        metadata={
            "source_text": text,
            "max_width": max_width,
            "display_duration": display_duration,
            "subtitle_font_scale": _number(segment.get("subtitle_font_scale", 1.0)),
            "subtitle_font_family": str(segment.get("subtitle_font_family", "")),
            "source_track": str(segment.get("source_track", "")),
            "source_speaker": str(segment.get("source_speaker", "")),
            "source_file": str(segment.get("source_file", "")),
        },
    )


def pack_segments(
    data: object,
    default_max_width: int = 24,
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
) -> list[SubtitleEvent]:
    events: list[SubtitleEvent] = []
    for segment in _entry_mappings(_mapping(data).get("segments", [])):
        pages = [segment] if segment.get("layout_packed") else pack_segment_pages(
            {**segment, "max_width": coerce_int(segment.get("max_width", default_max_width))},
            subtitle_max_gap_seconds=subtitle_max_gap_seconds,
            subtitle_end_padding_seconds=subtitle_end_padding_seconds,
            subtitle_min_duration_seconds=subtitle_min_duration_seconds,
        )
        for page in pages:
            event = pack_event(page, default_max_width=default_max_width)
            if event is not None:
                events.append(event)
    return events
