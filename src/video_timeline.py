from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4


VIDEO_TIMELINE_SCHEMA_VERSION = 1
MIN_CUT_DURATION_SECONDS = 0.05
_TIME_PRECISION = 3
_TIME_EPSILON = 0.0005


class VideoTimelineError(ValueError):
    """Raised when a non-destructive video timeline is malformed."""


def _finite_seconds(value: Any, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise VideoTimelineError(f"{field_name} must be a number") from error
    if not math.isfinite(result):
        raise VideoTimelineError(f"{field_name} must be finite")
    return result


def _new_cut_id() -> str:
    return f"cut-{uuid4().hex[:12]}"


@dataclass(frozen=True)
class CutRange:
    id: str
    source_start: float
    source_end: float
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(
        cls,
        payload: dict[str, Any],
        *,
        source_duration: float,
        fallback_id: str,
    ) -> "CutRange":
        if not isinstance(payload, dict):
            raise VideoTimelineError("timeline cuts must be objects")
        cut_id = str(payload.get("id", "")).strip() or fallback_id
        source_start = max(
            0.0,
            _finite_seconds(payload.get("source_start", 0.0), "cut.source_start"),
        )
        source_end = _finite_seconds(
            payload.get("source_end", source_start),
            "cut.source_end",
        )
        if source_duration > 0.0:
            source_start = min(source_start, source_duration)
            source_end = min(source_end, source_duration)
        if source_end - source_start < MIN_CUT_DURATION_SECONDS - _TIME_EPSILON:
            raise VideoTimelineError(f"cut {cut_id!r} must be at least {MIN_CUT_DURATION_SECONDS:.2f} seconds")
        return cls(
            id=cut_id,
            source_start=round(source_start, _TIME_PRECISION),
            source_end=round(source_end, _TIME_PRECISION),
            extras=deepcopy(
                {key: value for key, value in payload.items() if key not in {"id", "source_start", "source_end"}}
            ),
        )

    @property
    def duration(self) -> float:
        return self.source_end - self.source_start

    def to_json(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "source_start": self.source_start,
            "source_end": self.source_end,
        }
        payload.update(deepcopy(self.extras))
        return payload


def _normalized_source_duration(value: Any) -> float:
    duration = _finite_seconds(value, "video.duration_seconds")
    return round(max(0.0, duration), _TIME_PRECISION)


def _normalize_cuts(
    cuts: Iterable[CutRange],
    source_duration: float,
    *,
    preferred_id: str = "",
) -> tuple[CutRange, ...]:
    ordered = sorted(cuts, key=lambda cut: (cut.source_start, cut.source_end, cut.id))
    ids = [cut.id for cut in ordered]
    if len(ids) != len(set(ids)):
        raise VideoTimelineError("timeline cut ids must be unique")

    merged: list[CutRange] = []
    for cut in ordered:
        if not merged or cut.source_start > merged[-1].source_end + _TIME_EPSILON:
            merged.append(cut)
            continue
        previous = merged[-1]
        keep = cut if cut.id == preferred_id else previous
        if previous.id == preferred_id:
            keep = previous
        merged[-1] = CutRange(
            id=keep.id,
            source_start=round(min(previous.source_start, cut.source_start), _TIME_PRECISION),
            source_end=round(max(previous.source_end, cut.source_end), _TIME_PRECISION),
            extras=deepcopy(keep.extras),
        )

    if source_duration > 0.0 and merged:
        removed_duration = sum(cut.duration for cut in merged)
        if removed_duration >= source_duration - _TIME_EPSILON:
            raise VideoTimelineError("timeline cuts cannot remove the entire video")
    return tuple(merged)


def intersect_ranges(
    first: Iterable[tuple[float, float]],
    second: Iterable[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Return the ordered intersection of two non-overlapping range sets."""

    left = sorted((float(start), float(end)) for start, end in first if end > start)
    right = sorted((float(start), float(end)) for start, end in second if end > start)
    result: list[tuple[float, float]] = []
    left_index = 0
    right_index = 0
    while left_index < len(left) and right_index < len(right):
        start = max(left[left_index][0], right[right_index][0])
        end = min(left[left_index][1], right[right_index][1])
        if end - start >= _TIME_EPSILON:
            result.append((round(start, _TIME_PRECISION), round(end, _TIME_PRECISION)))
        if left[left_index][1] <= right[right_index][1]:
            left_index += 1
        else:
            right_index += 1
    return result


@dataclass(frozen=True)
class VideoTimeline:
    source_duration: float
    cuts: tuple[CutRange, ...] = ()
    schema_version: int = VIDEO_TIMELINE_SCHEMA_VERSION
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(
        cls,
        payload: dict[str, Any] | None,
        *,
        source_duration: float,
    ) -> "VideoTimeline":
        duration = _normalized_source_duration(source_duration)
        if payload is None:
            return cls(source_duration=duration)
        if not isinstance(payload, dict):
            raise VideoTimelineError("timeline must be an object")
        try:
            schema_version = int(payload.get("schema_version", VIDEO_TIMELINE_SCHEMA_VERSION))
        except (TypeError, ValueError) as error:
            raise VideoTimelineError("timeline.schema_version must be an integer") from error
        if schema_version > VIDEO_TIMELINE_SCHEMA_VERSION or schema_version <= 0:
            raise VideoTimelineError(f"unsupported timeline.schema_version: {payload.get('schema_version')!r}")
        raw_cuts = payload.get("cuts", [])
        if not isinstance(raw_cuts, list):
            raise VideoTimelineError("timeline.cuts must be an array")
        if raw_cuts and duration <= 0.0:
            raise VideoTimelineError("video duration is required when timeline cuts exist")
        cuts = tuple(
            CutRange.from_json(
                cut,
                source_duration=duration,
                fallback_id=f"cut-{index + 1:06d}",
            )
            for index, cut in enumerate(raw_cuts)
        )
        return cls(
            source_duration=duration,
            cuts=_normalize_cuts(cuts, duration),
            extras=deepcopy({key: value for key, value in payload.items() if key not in {"schema_version", "cuts"}}),
        )

    @property
    def has_cuts(self) -> bool:
        return bool(self.cuts)

    @property
    def removed_duration(self) -> float:
        return round(sum(cut.duration for cut in self.cuts), _TIME_PRECISION)

    @property
    def output_duration(self) -> float:
        return round(max(0.0, self.source_duration - self.removed_duration), _TIME_PRECISION)

    @property
    def keep_ranges(self) -> list[tuple[float, float]]:
        if self.source_duration <= 0.0:
            return []
        kept: list[tuple[float, float]] = []
        cursor = 0.0
        for cut in self.cuts:
            if cut.source_start > cursor + _TIME_EPSILON:
                kept.append((round(cursor, _TIME_PRECISION), cut.source_start))
            cursor = max(cursor, cut.source_end)
        if cursor < self.source_duration - _TIME_EPSILON:
            kept.append((round(cursor, _TIME_PRECISION), self.source_duration))
        return kept

    def to_json(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "cuts": [cut.to_json() for cut in self.cuts],
        }
        payload.update(deepcopy(self.extras))
        return payload

    def as_view(self) -> dict[str, Any]:
        output_cursor = 0.0
        keep_ranges: list[dict[str, float]] = []
        for source_start, source_end in self.keep_ranges:
            duration = source_end - source_start
            keep_ranges.append(
                {
                    "source_start": source_start,
                    "source_end": source_end,
                    "output_start": round(output_cursor, _TIME_PRECISION),
                    "output_end": round(output_cursor + duration, _TIME_PRECISION),
                }
            )
            output_cursor += duration
        return {
            "schemaVersion": self.schema_version,
            "sourceDuration": self.source_duration,
            "outputDuration": self.output_duration,
            "removedDuration": self.removed_duration,
            "hasCuts": self.has_cuts,
            "cuts": [
                {
                    "id": cut.id,
                    "source_start": cut.source_start,
                    "source_end": cut.source_end,
                    "duration": round(cut.duration, _TIME_PRECISION),
                }
                for cut in self.cuts
            ],
            "keepRanges": keep_ranges,
        }

    def _validated_edit_range(self, source_start: Any, source_end: Any) -> tuple[float, float]:
        if self.source_duration <= 0.0:
            raise VideoTimelineError("video duration is required before editing cuts")
        start = max(0.0, _finite_seconds(source_start, "cut.source_start"))
        end = min(self.source_duration, _finite_seconds(source_end, "cut.source_end"))
        start = min(start, self.source_duration)
        if end - start < MIN_CUT_DURATION_SECONDS - _TIME_EPSILON:
            raise VideoTimelineError(f"cut range must be at least {MIN_CUT_DURATION_SECONDS:.2f} seconds")
        return round(start, _TIME_PRECISION), round(end, _TIME_PRECISION)

    def add_cut(
        self,
        source_start: Any,
        source_end: Any,
        *,
        cut_id: str | None = None,
    ) -> "VideoTimeline":
        start, end = self._validated_edit_range(source_start, source_end)
        overlapping = next(
            (
                cut
                for cut in self.cuts
                if start <= cut.source_end + _TIME_EPSILON and end >= cut.source_start - _TIME_EPSILON
            ),
            None,
        )
        new_id = str(cut_id or "").strip() or _new_cut_id()
        if new_id in {cut.id for cut in self.cuts}:
            raise VideoTimelineError(f"timeline cut id already exists: {new_id}")
        cut = CutRange(id=new_id, source_start=start, source_end=end)
        return VideoTimeline(
            source_duration=self.source_duration,
            cuts=_normalize_cuts(
                (*self.cuts, cut),
                self.source_duration,
                preferred_id=overlapping.id if overlapping else "",
            ),
            schema_version=self.schema_version,
            extras=deepcopy(self.extras),
        )

    def update_cut(self, cut_id: str, source_start: Any, source_end: Any) -> "VideoTimeline":
        target = next((cut for cut in self.cuts if cut.id == cut_id), None)
        if target is None:
            raise VideoTimelineError(f"timeline cut was not found: {cut_id}")
        start, end = self._validated_edit_range(source_start, source_end)
        updated = CutRange(
            id=target.id,
            source_start=start,
            source_end=end,
            extras=deepcopy(target.extras),
        )
        remaining = tuple(cut for cut in self.cuts if cut.id != cut_id)
        return VideoTimeline(
            source_duration=self.source_duration,
            cuts=_normalize_cuts(
                (*remaining, updated),
                self.source_duration,
                preferred_id=cut_id,
            ),
            schema_version=self.schema_version,
            extras=deepcopy(self.extras),
        )

    def restore_cut(self, cut_id: str) -> "VideoTimeline":
        remaining = tuple(cut for cut in self.cuts if cut.id != cut_id)
        if len(remaining) == len(self.cuts):
            raise VideoTimelineError(f"timeline cut was not found: {cut_id}")
        return VideoTimeline(
            source_duration=self.source_duration,
            cuts=remaining,
            schema_version=self.schema_version,
            extras=deepcopy(self.extras),
        )

    def restore_range(
        self,
        source_start: Any,
        source_end: Any,
        *,
        id_factory: Callable[[], str] = _new_cut_id,
    ) -> "VideoTimeline":
        start, end = self._validated_edit_range(source_start, source_end)
        restored: list[CutRange] = []
        for cut in self.cuts:
            if end <= cut.source_start + _TIME_EPSILON or start >= cut.source_end - _TIME_EPSILON:
                restored.append(cut)
                continue
            left_end = min(start, cut.source_end)
            right_start = max(end, cut.source_start)
            if left_end - cut.source_start >= MIN_CUT_DURATION_SECONDS - _TIME_EPSILON:
                restored.append(
                    CutRange(
                        id=cut.id,
                        source_start=cut.source_start,
                        source_end=round(left_end, _TIME_PRECISION),
                        extras=deepcopy(cut.extras),
                    )
                )
            if cut.source_end - right_start >= MIN_CUT_DURATION_SECONDS - _TIME_EPSILON:
                right_id = (
                    cut.id if left_end - cut.source_start < MIN_CUT_DURATION_SECONDS - _TIME_EPSILON else id_factory()
                )
                restored.append(
                    CutRange(
                        id=right_id,
                        source_start=round(right_start, _TIME_PRECISION),
                        source_end=cut.source_end,
                        extras=deepcopy(cut.extras),
                    )
                )
        return VideoTimeline(
            source_duration=self.source_duration,
            cuts=_normalize_cuts(restored, self.source_duration),
            schema_version=self.schema_version,
            extras=deepcopy(self.extras),
        )

    def clear_cuts(self) -> "VideoTimeline":
        return VideoTimeline(
            source_duration=self.source_duration,
            schema_version=self.schema_version,
            extras=deepcopy(self.extras),
        )

    def source_to_output_seconds(self, source_position: Any) -> float:
        position = min(
            self.source_duration,
            max(0.0, _finite_seconds(source_position, "source position")),
        )
        removed_before = 0.0
        for cut in self.cuts:
            if position < cut.source_start:
                break
            if position < cut.source_end:
                return round(cut.source_start - removed_before, _TIME_PRECISION)
            removed_before += cut.duration
        return round(position - removed_before, _TIME_PRECISION)

    def output_to_source_seconds(self, output_position: Any) -> float:
        position = min(
            self.output_duration,
            max(0.0, _finite_seconds(output_position, "output position")),
        )
        output_cursor = 0.0
        keep_ranges = self.keep_ranges
        for index, (source_start, source_end) in enumerate(keep_ranges):
            duration = source_end - source_start
            output_end = output_cursor + duration
            if position < output_end - _TIME_EPSILON:
                return round(source_start + position - output_cursor, _TIME_PRECISION)
            if abs(position - output_end) <= _TIME_EPSILON and index + 1 < len(keep_ranges):
                return keep_ranges[index + 1][0]
            output_cursor = output_end
        return self.source_duration

    def contains_source_seconds(self, source_position: Any) -> bool:
        position = _finite_seconds(source_position, "source position")
        return any(cut.source_start <= position < cut.source_end for cut in self.cuts)

    def next_playable_source_seconds(self, source_position: Any) -> float:
        position = min(
            self.source_duration,
            max(0.0, _finite_seconds(source_position, "source position")),
        )
        for cut in self.cuts:
            if cut.source_start <= position < cut.source_end:
                return cut.source_end
            if position < cut.source_start:
                break
        return position

    # EditorWorkspaceState uses integer milliseconds at the shared-player boundary.
    def source_to_output(self, position_ms: int) -> int:
        return int(round(self.source_to_output_seconds(int(position_ms) / 1000.0) * 1000))

    def output_to_source(self, position_ms: int) -> int:
        return int(round(self.output_to_source_seconds(int(position_ms) / 1000.0) * 1000))


def timeline_from_project(project: Mapping[str, Any]) -> VideoTimeline:
    """Build the normal-video timeline without consulting short-video state."""

    video = project.get("video", {})
    video_duration = 0.0
    if isinstance(video, Mapping):
        video_duration = _finite_seconds(
            video.get("duration_seconds", 0.0) or 0.0,
            "video.duration_seconds",
        )
    segment_ends = []
    segments = project.get("segments", [])
    if isinstance(segments, list):
        segment_ends = [
            _finite_seconds(segment.get("end", 0.0), "segment.end")
            for segment in segments
            if isinstance(segment, Mapping)
        ]
    return VideoTimeline.from_json(
        project.get("timeline"),
        source_duration=(video_duration if video_duration > 0.0 else max(segment_ends, default=0.0)),
    )
