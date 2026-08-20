from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .highlight_signals import HighlightCancelled, SpeechSignal, build_speech_signals


HIGHLIGHT_SCORING_VERSION = "local-v1"


@dataclass(frozen=True)
class HighlightSettings:
    before_margin: float = 0.35
    after_margin: float = 0.35
    grouping_gap: float = 0.8
    minimum_duration: float = 3.0
    maximum_duration: float = 45.0
    top_k: int = 10
    overlap_threshold: float = 0.55
    diversity_buckets: int = 5
    scoring_version: str = HIGHLIGHT_SCORING_VERSION


@dataclass(frozen=True)
class HighlightCandidate:
    id: str
    start: float
    end: float
    score: float
    category: str
    reason: str
    subtitle_excerpt: str
    source_segment_ids: tuple[str, ...]
    score_breakdown: Mapping[str, float]

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "duration": round(self.duration, 3),
            "score": self.score,
            "category": self.category,
            "reason": self.reason,
            "subtitle_excerpt": self.subtitle_excerpt,
            "source_segment_ids": list(self.source_segment_ids),
            "score_breakdown": dict(self.score_breakdown),
            "scoring_version": HIGHLIGHT_SCORING_VERSION,
        }


def highlight_cache_key(
    segments: Iterable[Mapping[str, Any]],
    audio_levels: Iterable[Mapping[str, Any]] | None,
    settings: HighlightSettings,
) -> str:
    payload = {
        "segments": [dict(item) for item in segments],
        "audio_levels": [dict(item) for item in (audio_levels or [])],
        "settings": asdict(settings),
        "scoring_version": HIGHLIGHT_SCORING_VERSION,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def generate_highlight_candidates(
    segments: Iterable[Mapping[str, Any]],
    *,
    audio_levels: Iterable[Mapping[str, Any]] | None = None,
    duration_seconds: float | None = None,
    settings: HighlightSettings | None = None,
    cancel_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[float], None] | None = None,
    cache_directory: str | Path | None = None,
) -> list[HighlightCandidate]:
    settings = settings or HighlightSettings()
    source_segments = [dict(item) for item in segments]
    cache_path: Path | None = None
    if cache_directory is not None:
        cache_path = Path(cache_directory) / f"highlight-{highlight_cache_key(source_segments, audio_levels, settings)}.json"
        if cache_path.is_file():
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            return [_candidate_from_json(item) for item in payload]

    ordered = sorted(
        (item for item in source_segments if float(item.get("end", 0.0)) > float(item.get("start", 0.0))),
        key=lambda item: (float(item.get("start", 0.0)), float(item.get("end", 0.0)), str(item.get("id", ""))),
    )
    signals = build_speech_signals(ordered, audio_levels)
    groups = _group_segments(ordered, signals, settings)
    candidates: list[HighlightCandidate] = []
    for index, group in enumerate(groups):
        if cancel_check and cancel_check():
            raise HighlightCancelled("highlight generation cancelled")
        candidates.append(_score_group(group, signals, settings, duration_seconds))
        if progress_callback:
            progress_callback((index + 1) / max(1, len(groups)))

    selected = _select_diverse(_suppress_overlaps(candidates, settings.overlap_threshold), settings)
    selected.sort(key=lambda item: (-item.score, item.start, item.id))
    if cache_path is not None:
        _write_cache(cache_path, selected)
    return selected


def _group_segments(
    segments: list[Mapping[str, Any]],
    signals: list[SpeechSignal],
    settings: HighlightSettings,
) -> list[list[Mapping[str, Any]]]:
    if not segments:
        return []
    groups: list[list[Mapping[str, Any]]] = [[segments[0]]]
    for segment in segments[1:]:
        previous = groups[-1][-1]
        gap = float(segment.get("start", 0.0)) - float(previous.get("end", 0.0))
        if gap <= settings.grouping_gap:
            groups[-1].append(segment)
        else:
            groups.append([segment])
    return groups


def _score_group(
    group: list[Mapping[str, Any]],
    signals: list[SpeechSignal],
    settings: HighlightSettings,
    duration_seconds: float | None,
) -> HighlightCandidate:
    raw_start = min(float(item.get("start", 0.0)) for item in group)
    raw_end = max(float(item.get("end", raw_start)) for item in group)
    start = max(0.0, raw_start - settings.before_margin)
    end = raw_end + settings.after_margin
    if duration_seconds is not None:
        end = min(max(0.0, float(duration_seconds)), end)
    if end - start < settings.minimum_duration:
        end = start + settings.minimum_duration
        if duration_seconds is not None and end > duration_seconds:
            end = float(duration_seconds)
            start = max(0.0, end - settings.minimum_duration)
    if end - start > settings.maximum_duration:
        end = start + settings.maximum_duration
    text = " ".join(str(item.get("text", "")).strip() for item in group).strip()
    text_signal = min(1.0, len(text) / 80.0)
    group_signals = [
        signal.level
        for signal in signals
        if signal.end > raw_start and signal.start < raw_end
    ]
    intensity = sum(group_signals) / len(group_signals) if group_signals else 0.0
    speaker_count = len({str(item.get("speaker", "")) for item in group if item.get("speaker")})
    diversity = min(1.0, speaker_count / 2.0)
    score_breakdown = {
        "text": round(text_signal, 4),
        "intensity": round(intensity, 4),
        "speaker_diversity": round(diversity, 4),
    }
    score = round(0.45 * text_signal + 0.4 * intensity + 0.15 * diversity, 4)
    category = "emphasis" if any(mark in text for mark in ("!", "！", "?", "？")) else "conversation"
    reason = f"字幕{len(group)}件、発話強度{intensity:.2f}、話者{speaker_count}人"
    segment_ids = tuple(str(item.get("id", "")) for item in group)
    candidate_id = hashlib.sha1(
        f"{segment_ids}:{start:.3f}:{end:.3f}:{HIGHLIGHT_SCORING_VERSION}".encode("utf-8")
    ).hexdigest()[:16]
    return HighlightCandidate(
        id=f"highlight-{candidate_id}",
        start=round(start, 3),
        end=round(end, 3),
        score=score,
        category=category,
        reason=reason,
        subtitle_excerpt=text[:160],
        source_segment_ids=segment_ids,
        score_breakdown=score_breakdown,
    )


def _suppress_overlaps(candidates: list[HighlightCandidate], threshold: float) -> list[HighlightCandidate]:
    selected: list[HighlightCandidate] = []
    for candidate in sorted(candidates, key=lambda item: (-item.score, item.start, item.id)):
        if any(_overlap_ratio(candidate, other) >= threshold for other in selected):
            continue
        selected.append(candidate)
    return selected


def _select_diverse(candidates: list[HighlightCandidate], settings: HighlightSettings) -> list[HighlightCandidate]:
    if not candidates:
        return []
    if settings.top_k <= 0:
        return []
    max_end = max(item.end for item in candidates)
    bucket_count = max(1, settings.diversity_buckets)
    buckets: dict[int, list[HighlightCandidate]] = {index: [] for index in range(bucket_count)}
    for candidate in candidates:
        bucket = min(bucket_count - 1, int((candidate.start / max(1.0, max_end)) * bucket_count))
        buckets[bucket].append(candidate)
    selected: list[HighlightCandidate] = []
    for bucket in range(bucket_count):
        if buckets[bucket] and len(selected) < settings.top_k:
            selected.append(max(buckets[bucket], key=lambda item: (item.score, -item.start)))
    for candidate in sorted(candidates, key=lambda item: (-item.score, item.start, item.id)):
        if candidate not in selected and len(selected) < settings.top_k:
            selected.append(candidate)
    return selected


def _overlap_ratio(first: HighlightCandidate, second: HighlightCandidate) -> float:
    overlap = max(0.0, min(first.end, second.end) - max(first.start, second.start))
    shorter = min(first.duration, second.duration)
    return overlap / shorter if shorter > 0 else 0.0


def _write_cache(path: Path, candidates: list[HighlightCandidate]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f"{path.stem}-", suffix=".tmp", delete=False
        )
        temp_path = Path(handle.name)
        with handle:
            json.dump([candidate.to_json() for candidate in candidates], handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
    except OSError:
        try:
            temp_path.unlink(missing_ok=True)
        except (NameError, OSError):
            pass


def _candidate_from_json(payload: Mapping[str, Any]) -> HighlightCandidate:
    return HighlightCandidate(
        id=str(payload["id"]),
        start=float(payload["start"]),
        end=float(payload["end"]),
        score=float(payload["score"]),
        category=str(payload["category"]),
        reason=str(payload["reason"]),
        subtitle_excerpt=str(payload.get("subtitle_excerpt", "")),
        source_segment_ids=tuple(str(item) for item in payload.get("source_segment_ids", [])),
        score_breakdown={str(key): float(value) for key, value in payload.get("score_breakdown", {}).items()},
    )

