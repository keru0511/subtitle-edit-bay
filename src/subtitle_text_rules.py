from __future__ import annotations

from typing import Any


LEADING_CLOSING_PUNCTUATION = frozenset("、。！？!?，．,.")


def _stream_key(segment: dict[str, Any]) -> tuple[str, str, str]:
    source_file = str(segment.get("source_file") or "")
    source_track = str(segment.get("source_track") or "")
    speaker = str(segment.get("source_speaker") or segment.get("speaker") or "")
    if source_file or source_track:
        return "source", source_file, source_track
    return "speaker", speaker, ""


def _start_time(segment: dict[str, Any]) -> float:
    try:
        return float(segment.get("start", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _leading_punctuation(text: str) -> str:
    end = 0
    while end < len(text) and text[end] in LEADING_CLOSING_PUNCTUATION:
        end += 1
    return text[:end]


def _append_to_last_aligned_word(segment: dict[str, Any], punctuation: str) -> None:
    words = segment.get("words")
    if not isinstance(words, list):
        return
    for index in range(len(words) - 1, -1, -1):
        word = words[index]
        if not isinstance(word, dict):
            continue
        value = str(word.get("word", ""))
        if value.strip():
            updated_words = list(words)
            updated_word = dict(word)
            updated_word["word"] = value.rstrip() + punctuation
            updated_words[index] = updated_word
            segment["words"] = updated_words
            return


def _remove_from_leading_aligned_words(segment: dict[str, Any], count: int) -> None:
    words = segment.get("words")
    if not isinstance(words, list) or count <= 0:
        return

    remaining = count
    cleaned: list[Any] = []
    for word in words:
        if not isinstance(word, dict):
            cleaned.append(word)
            continue

        value = str(word.get("word", ""))
        leading_space_count = len(value) - len(value.lstrip())
        leading_space = value[:leading_space_count]
        body = value[leading_space_count:]
        removed = 0
        while remaining > 0 and removed < len(body) and body[removed] in LEADING_CLOSING_PUNCTUATION:
            removed += 1
            remaining -= 1

        if removed:
            updated = dict(word)
            updated["word"] = leading_space + body[removed:]
            if str(updated["word"]).strip():
                cleaned.append(updated)
            continue
        cleaned.append(word)

    segment["words"] = cleaned


def reattach_leading_punctuation(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Move leading closing punctuation to the preceding caption in its source stream.

    WhisperX can place sentence punctuation at the beginning of its next segment.
    A punctuation mark has no independent speech timing, so the unambiguous repair
    is to attach it to the preceding segment from the same transcription stream. The
    raw transcript remains untouched because this function uses copy-on-write.
    """

    repaired = [dict(segment) for segment in segments]
    removed_indices: set[int] = set()
    previous_by_stream: dict[tuple[str, str, str], dict[str, Any]] = {}
    ordered = sorted(
        enumerate(repaired),
        key=lambda item: (_start_time(item[1]), item[0]),
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
            _remove_from_leading_aligned_words(segment, len(punctuation))
            text = text[len(punctuation):].lstrip()
            if not text:
                removed_indices.add(index)
                continue
            segment["text"] = text

        previous_by_stream[stream_key] = segment

    return [segment for index, segment in enumerate(repaired) if index not in removed_indices]
