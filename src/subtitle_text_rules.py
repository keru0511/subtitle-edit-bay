from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeGuard

from .data_boundary import coerce_float, is_object_mapping


LEADING_CLOSING_PUNCTUATION = frozenset("、。！？!?")


def _mapping(value: object) -> Mapping[object, object]:
    if not is_object_mapping(value):
        raise TypeError("subtitle segment must be a mapping")
    return value


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _stream_key(segment: Mapping[object, object]) -> tuple[str, str, str]:
    source_stream_id = str(segment.get("source_stream_id") or "")
    source_file = str(segment.get("source_file") or "")
    source_track = str(segment.get("source_track") or "")
    speaker = str(segment.get("source_speaker") or segment.get("speaker") or "")
    if source_stream_id:
        return "source_stream", source_stream_id, ""
    if source_file or source_track:
        return "source", source_file, source_track
    return "speaker", speaker, ""


def _start_time(segment: Mapping[object, object]) -> float:
    try:
        return coerce_float(segment.get("start", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _leading_punctuation(text: str) -> str:
    end = 0
    while end < len(text) and text[end] in LEADING_CLOSING_PUNCTUATION:
        end += 1
    return text[:end]


def _append_to_last_aligned_word(segment: dict[object, object], punctuation: str) -> None:
    words = segment.get("words")
    if not _is_object_list(words):
        return
    for index in range(len(words) - 1, -1, -1):
        word = words[index]
        if not is_object_mapping(word):
            continue
        value = str(word.get("word", ""))
        if value.strip():
            updated_words = list(words)
            updated_word = dict(word)
            updated_word["word"] = value.rstrip() + punctuation
            updated_words[index] = updated_word
            segment["words"] = updated_words
            return


def _remove_from_leading_aligned_words(segment: dict[object, object], punctuation: str) -> None:
    words = segment.get("words")
    if not _is_object_list(words) or not punctuation:
        return

    punctuation_index = 0
    cleaned: list[object] = []
    removed_any = False
    for index, word in enumerate(words):
        if punctuation_index >= len(punctuation):
            cleaned.extend(words[index:])
            break
        if not is_object_mapping(word):
            cleaned.extend(words[index:])
            break

        value = str(word.get("word", ""))
        leading_space_count = len(value) - len(value.lstrip())
        leading_space = value[:leading_space_count]
        body = value[leading_space_count:]
        if not body:
            cleaned.append(word)
            continue

        removed = 0
        while removed < len(body):
            # Alignment may omit marks, so match its prefix as an ordered subsequence.
            matched_index = punctuation.find(body[removed], punctuation_index)
            if matched_index < 0:
                break
            removed += 1
            punctuation_index = matched_index + 1
            if punctuation_index >= len(punctuation):
                break

        if removed:
            removed_any = True
            updated = dict(word)
            updated["word"] = leading_space + body[removed:]
            if str(updated["word"]).strip():
                cleaned.append(updated)
                cleaned.extend(words[index + 1 :])
                break
            continue
        cleaned.extend(words[index:])
        break

    if removed_any:
        segment["words"] = cleaned


def reattach_leading_punctuation(segments: Sequence[object]) -> list[dict[object, object]]:
    """Move leading closing punctuation to the preceding caption in its source stream.

    WhisperX can place sentence punctuation at the beginning of its next segment.
    A punctuation mark has no independent speech timing, so the unambiguous repair
    is to attach it to the preceding segment from the same transcription stream. The
    raw transcript remains untouched because this function uses copy-on-write.
    """

    repaired = [dict(_mapping(segment)) for segment in segments]
    removed_indices: set[int] = set()
    previous_by_stream: dict[tuple[str, str, str], dict[object, object]] = {}

    def _order_key(item: tuple[int, dict[object, object]]) -> tuple[float, int]:
        return _start_time(item[1]), item[0]

    ordered = sorted(
        enumerate(repaired),
        key=_order_key,
    )

    for index, segment in ordered:
        text = str(segment.get("text", "")).strip()
        if not text:
            continue

        stream_key = _stream_key(segment)
        punctuation = _leading_punctuation(text)
        previous = previous_by_stream.get(stream_key)
        if punctuation and previous is not None:
            previous["text"] = str(previous.get("text", "")).rstrip() + punctuation
            _append_to_last_aligned_word(previous, punctuation)
            _remove_from_leading_aligned_words(segment, punctuation)
            text = text[len(punctuation):].lstrip()
            if not text:
                removed_indices.add(index)
                continue
            segment["text"] = text

        previous_by_stream[stream_key] = segment

    return [segment for index, segment in enumerate(repaired) if index not in removed_indices]
