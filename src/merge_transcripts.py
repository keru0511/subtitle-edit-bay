from __future__ import annotations

import json
from pathlib import Path

from .subtitle_packer import (
    DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
    pack_segment_pages,
)

BOTTOM_MAX_WIDTH = 28
OZ_MAX_WIDTH = 28
OTHER_MAX_WIDTH = 24
MAX_BOTTOM_ROWS = 3
STRICT_FILTER_PATTERNS = [
    "ご視聴ありがとうございました",
    "by h",
    "subscribe",
    "チャンネル登録",
]
GAME_TERMS = [
    "怪物",
    "ガンマ型",
    "アッシュ",
    "コンバットフレーム",
    "ビークル",
    "敵艦隊",
    "目標上空",
    "エアレーダー",
    "輸送機",
    "投下",
    "戦闘車両",
    "建築物",
    "兵器",
    "大型",
]
CASUAL_MARKERS = ["www", "やば", "うわ", "まじ", "えっ", "あの", "これ", "それ", "いや", "ねえ", "かな", "だよ", "じゃん"]
SHORT_REACTION_MARKERS = ["!", "！", "?", "？", "w", "W", "笑", "うわ", "えっ", "まじ", "やば"]
TRACK_DEFAULT_MAP = {"0:a:1": {None: "Oz"}, "0:a:3": {None: "Guest"}}
DISCORD_TRACK = "0:a:3"
DISCORD_COLORS = ["A", "B", "C"]


def load_transcript(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def display_width(char: str) -> int:
    return 2 if ord(char) > 127 else 1


def text_width(text: str) -> int:
    return sum(display_width(char) for char in text)


def max_width_for_speaker(speaker: str) -> int:
    return OZ_MAX_WIDTH if speaker == "Oz" else OTHER_MAX_WIDTH


def is_short_reaction(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    if text_width(normalized) <= 8:
        return True
    return any(marker in normalized for marker in SHORT_REACTION_MARKERS)


def split_segment(
    segment: dict,
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
) -> list[dict]:
    return pack_segment_pages(
        segment,
        subtitle_max_gap_seconds=subtitle_max_gap_seconds,
        subtitle_end_padding_seconds=subtitle_end_padding_seconds,
        subtitle_min_duration_seconds=subtitle_min_duration_seconds,
    )


def build_discord_speaker_map(segments: list[dict]) -> dict[str | None, str]:
    mapping: dict[str | None, str] = {}
    for segment in segments:
        diarized_speaker = segment.get("speaker")
        if diarized_speaker is None or diarized_speaker in mapping:
            continue
        if len(mapping) < len(DISCORD_COLORS):
            mapping[diarized_speaker] = DISCORD_COLORS[len(mapping)]
        else:
            unknown_index = sum(1 for value in mapping.values() if value.startswith("UNKNOWN_")) + 1
            mapping[diarized_speaker] = f"UNKNOWN_{unknown_index}"
    return mapping


def speaker_for_track(audio_track: str, diarized_speaker: str | None = None, dynamic_map: dict[str | None, str] | None = None) -> str:
    if audio_track == DISCORD_TRACK and dynamic_map is not None:
        if diarized_speaker in dynamic_map:
            return dynamic_map[diarized_speaker]
        return dynamic_map.get(None, "Guest")

    mapping = TRACK_DEFAULT_MAP.get(audio_track, {})
    if diarized_speaker in mapping:
        return mapping[diarized_speaker]
    if None in mapping:
        return mapping[None]
    return diarized_speaker or audio_track.replace(":", "_")


def is_non_speech_candidate(text: str, source_track: str) -> tuple[bool, list[str]]:
    normalized = text.lower()
    reasons: list[str] = []
    if source_track != DISCORD_TRACK:
        return False, reasons

    if any(pattern in normalized for pattern in STRICT_FILTER_PATTERNS):
        reasons.append("strict_phrase")

    game_hits = [term for term in GAME_TERMS if term in text]
    if len(game_hits) >= 2:
        reasons.append(f"game_terms:{','.join(game_hits[:4])}")
    elif len(game_hits) >= 1 and len(text) >= 24 and not any(marker in normalized for marker in CASUAL_MARKERS):
        reasons.append(f"single_game_term:{game_hits[0]}")

    if len(text) >= 40 and not any(marker in normalized for marker in CASUAL_MARKERS):
        reasons.append("long_formal_text")

    return bool(reasons), reasons


def row_span_for_segment(segment: dict) -> int:
    max_width = int(segment.get("max_width", BOTTOM_MAX_WIDTH))
    return min(MAX_BOTTOM_ROWS, 2 if text_width(segment.get("text", "")) > max_width else 1)


def occupied_rows(segment: dict) -> set[int]:
    start_row = int(segment.get("layout_row", 0))
    span = int(segment.get("layout_row_span", row_span_for_segment(segment)))
    return set(range(start_row, min(MAX_BOTTOM_ROWS, start_row + span)))


def available_base_rows(active: list[dict], span: int) -> list[int]:
    used_rows: set[int] = set()
    for item in active:
        used_rows.update(occupied_rows(item))
    return [row for row in range(0, MAX_BOTTOM_ROWS - span + 1) if all(slot not in used_rows for slot in range(row, row + span))]


def mark_overflow(segment: dict, overflow: list[dict]) -> None:
    segment["filter_reasons"] = list(dict.fromkeys(segment.get("filter_reasons", []) + ["overflow_dropped"]))
    overflow.append(segment)


def assign_bottom_rows(segments: list[dict]) -> tuple[list[dict], list[dict]]:
    active: list[dict] = []
    assigned: list[dict] = []
    overflow: list[dict] = []

    for segment in sorted(segments, key=lambda item: (item["start"], -(item["end"] - item["start"]), item["speaker"])):
        active = [item for item in active if float(item["end"]) > float(segment["start"])]
        span = row_span_for_segment(segment)
        segment["layout_row_span"] = span
        available_rows = available_base_rows(active, span)

        while not available_rows:
            candidates = active + [segment]
            if not candidates:
                break
            shortest = min(candidates, key=lambda item: (float(item["end"]) - float(item["start"]), text_width(item["text"])))
            if shortest is segment:
                mark_overflow(segment, overflow)
                break
            active.remove(shortest)
            assigned.remove(shortest)
            mark_overflow(shortest, overflow)
            available_rows = available_base_rows(active, span)

        if not available_rows:
            continue

        segment["layout_row"] = min(available_rows)
        assigned.append(segment)
        active.append(segment)

    assigned.sort(key=lambda item: (item["start"], item["layout_row"], item["speaker"]))
    overflow.sort(key=lambda item: (item["start"], item["end"], item["speaker"]))
    return assigned, overflow


def refine_segments(
    segments: list[dict],
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
) -> tuple[list[dict], list[dict]]:
    refined: list[dict] = []
    filtered: list[dict] = []
    for segment in segments:
        segment_filtered, segment_reasons = is_non_speech_candidate(segment["text"], segment["source_track"])
        split_parts = split_segment(
            segment,
            subtitle_max_gap_seconds=subtitle_max_gap_seconds,
            subtitle_end_padding_seconds=subtitle_end_padding_seconds,
            subtitle_min_duration_seconds=subtitle_min_duration_seconds,
        )
        for split in split_parts:
            split_filtered, split_reasons = is_non_speech_candidate(split["text"], split["source_track"])
            reasons = list(dict.fromkeys(segment_reasons + split_reasons))
            split["filter_reasons"] = reasons
            if segment_filtered or split_filtered:
                filtered.append(split)
            else:
                refined.append(split)
    refined, overflow = assign_bottom_rows(refined)
    filtered.extend(overflow)
    filtered.sort(key=lambda item: (item["start"], item["end"], item["speaker"]))
    return refined, filtered


def merge_transcripts(
    track_to_transcript: dict[str, str],
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
) -> tuple[dict, dict]:
    merged_segments: list[dict] = []
    for audio_track, transcript_path in track_to_transcript.items():
        data = load_transcript(transcript_path)
        dynamic_map = build_discord_speaker_map(data.get("segments", [])) if audio_track == DISCORD_TRACK and any(segment.get("speaker") for segment in data.get("segments", [])) else None
        for segment in data.get("segments", []):
            text = segment.get("text", "").strip()
            if not text:
                continue

            diarized_speaker = segment.get("speaker")
            speaker = speaker_for_track(audio_track, diarized_speaker, dynamic_map)
            merged_segments.append(
                {
                    "start": float(segment["start"]),
                    "end": float(segment["end"]),
                    "speaker": speaker,
                    "text": text,
                    "emphasis": "shout" if is_short_reaction(text) and any(mark in text for mark in ["!", "！", "?", "？"]) else segment.get("emphasis", "normal"),
                    "position": "bottom",
                    "layout_row": 0,
                    "max_width": max_width_for_speaker(speaker),
                    "source_track": audio_track,
                    "source_speaker": diarized_speaker,
                    "words": segment.get("words", []),
                }
            )

    refined, filtered = refine_segments(
        merged_segments,
        subtitle_max_gap_seconds=subtitle_max_gap_seconds,
        subtitle_end_padding_seconds=subtitle_end_padding_seconds,
        subtitle_min_duration_seconds=subtitle_min_duration_seconds,
    )
    return {"segments": refined}, {"segments": filtered}


def write_merged_transcript(
    track_to_transcript: dict[str, str],
    output_path: str,
    filtered_output_path: str | None = None,
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
) -> tuple[Path, Path | None]:
    merged, filtered = merge_transcripts(
        track_to_transcript,
        subtitle_max_gap_seconds=subtitle_max_gap_seconds,
        subtitle_end_padding_seconds=subtitle_end_padding_seconds,
        subtitle_min_duration_seconds=subtitle_min_duration_seconds,
    )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    filtered_path: Path | None = None
    if filtered_output_path:
        filtered_path = Path(filtered_output_path)
        filtered_path.parent.mkdir(parents=True, exist_ok=True)
        filtered_path.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8")

    return path, filtered_path
