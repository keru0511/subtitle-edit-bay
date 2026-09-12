from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import json
import math
from typing import Any, Iterable, Mapping, TypeAlias

from .color_config import normalize_rgb_color
from .short_video_schema import ShortVideo, ShortVideoClip, ShortVideoError, VALID_FIT_MODES


MIN_SHORT_CLIP_SECONDS = 0.05


@dataclass(frozen=True)
class AddShortVideoRangeClip:
    clip_id: str
    source_start: Any
    source_end: Any


@dataclass(frozen=True)
class AddShortVideoSegmentClip:
    clip_id: str
    segment_id: str


@dataclass(frozen=True)
class RemoveShortVideoClip:
    clip_id: str


@dataclass(frozen=True)
class MoveShortVideoClip:
    clip_id: str
    before_clip_id: str = ""


@dataclass(frozen=True)
class UpdateShortVideoClip:
    clip_id: str
    changes: Mapping[str, Any]


@dataclass(frozen=True)
class UseShortVideoHighlightCandidate:
    clip_id: str
    highlight_candidate_id: str


@dataclass(frozen=True)
class SetShortVideoDurationTarget:
    target_seconds: Any


ShortVideoCommand: TypeAlias = (
    AddShortVideoRangeClip
    | AddShortVideoSegmentClip
    | RemoveShortVideoClip
    | MoveShortVideoClip
    | UpdateShortVideoClip
    | UseShortVideoHighlightCandidate
    | SetShortVideoDurationTarget
)


@dataclass(frozen=True)
class ShortVideoCommandResult:
    short_video: ShortVideo
    changed_clip_ids: tuple[str, ...]


@dataclass(frozen=True)
class _SourceContext:
    duration: float
    segment_ranges: Mapping[str, tuple[float, float]]


def _legacy_clip_id(clip: Mapping[str, Any], index: int) -> str:
    canonical = json.dumps(dict(clip), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{index}:{canonical}".encode()).hexdigest()[:12]
    return f"short-clip-{digest}"


def short_video_section_with_stable_ids(project: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached short section with a stable identity for every clip."""

    raw = project.get("short_video", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ShortVideoError("short_video must be an object")
    section = deepcopy(dict(raw))
    if str(section.get("time_basis", "source")) != "source":
        raise ShortVideoError("short clips must use source time")
    section.setdefault("enabled", False)
    section.setdefault("clips", [])
    raw_clips = section.get("clips")
    if not isinstance(raw_clips, list):
        raise ShortVideoError("short_video.clips must be an array")

    clips: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, raw_clip in enumerate(raw_clips):
        if not isinstance(raw_clip, Mapping):
            raise ShortVideoError(f"short_video.clips[{index}] must be an object")
        clip = deepcopy(dict(raw_clip))
        clip_id = str(clip.get("proposal_id", "")).strip() or _legacy_clip_id(clip, index)
        if clip_id in used_ids:
            raise ShortVideoError("short clip proposal ids must be unique")
        clip["proposal_id"] = clip_id
        clips.append(clip)
        used_ids.add(clip_id)
    section["clips"] = clips
    return section


def short_video_from_project(project: Mapping[str, Any]) -> ShortVideo:
    """Load the canonical command state without mutating the project."""

    return ShortVideo.from_json(short_video_section_with_stable_ids(project))


def _finite_seconds(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ShortVideoError(f"{field} must be a number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ShortVideoError(f"{field} must be a number") from error
    if not math.isfinite(result):
        raise ShortVideoError(f"{field} must be finite")
    return round(result, 3)


def _source_context(project: Mapping[str, Any]) -> _SourceContext:
    raw_video = project.get("video", {})
    if not isinstance(raw_video, Mapping):
        raise ShortVideoError("video must be an object")
    video_duration = _finite_seconds(raw_video.get("duration_seconds", 0.0), "video.duration_seconds")
    if video_duration < 0.0:
        raise ShortVideoError("video.duration_seconds must not be negative")

    raw_segments = project.get("segments", [])
    if not isinstance(raw_segments, list):
        raise ShortVideoError("segments must be an array")
    segment_ranges: dict[str, tuple[float, float]] = {}
    for index, raw_segment in enumerate(raw_segments):
        if not isinstance(raw_segment, Mapping):
            raise ShortVideoError(f"segments[{index}] must be an object")
        segment_id = str(raw_segment.get("id", ""))
        if not segment_id:
            continue
        if segment_id in segment_ranges:
            raise ShortVideoError(f"duplicate segment id: {segment_id}")
        start = _finite_seconds(raw_segment.get("start", 0.0), f"segments[{index}].start")
        end = _finite_seconds(raw_segment.get("end", start), f"segments[{index}].end")
        if start < 0.0 or end < start:
            raise ShortVideoError(f"segments[{index}] has an invalid range")
        segment_ranges[segment_id] = (start, end)
    return _SourceContext(duration=video_duration, segment_ranges=segment_ranges)


def _required_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShortVideoError(f"{field} is required")
    return value.strip()


def _clip_index(clips: list[ShortVideoClip], clip_id: Any) -> tuple[int, str]:
    resolved_id = _required_id(clip_id, "short clip id")
    for index, clip in enumerate(clips):
        if clip.proposal_id == resolved_id:
            return index, resolved_id
    raise ShortVideoError(f"short clip was not found: {resolved_id}")


def _validated_range(
    start: Any,
    end: Any,
    context: _SourceContext,
    *,
    field: str,
) -> tuple[float, float]:
    resolved_start = _finite_seconds(start, f"{field}.source_start")
    resolved_end = _finite_seconds(end, f"{field}.source_end")
    if resolved_start < 0.0 or round(resolved_end - resolved_start, 3) < MIN_SHORT_CLIP_SECONDS:
        raise ShortVideoError(f"{field} must be at least {MIN_SHORT_CLIP_SECONDS:.2f} seconds")
    if context.duration <= 0.0 or resolved_end > context.duration:
        raise ShortVideoError(f"{field} is outside the source video")
    return resolved_start, resolved_end


def _validate_segment_range(
    context: _SourceContext,
    segment_id: str,
    start: float,
    end: float,
) -> None:
    segment_range = context.segment_ranges.get(segment_id)
    if segment_range is None:
        raise ShortVideoError(f"short clip segment was not found: {segment_id}")
    segment_start, segment_end = segment_range
    if start < segment_start or end > segment_end:
        raise ShortVideoError("short clip range must stay inside its segment")


def _validate_new_clip_id(used_clip_ids: set[str], clip_id: Any) -> str:
    resolved_id = _required_id(clip_id, "short clip id")
    if resolved_id in used_clip_ids:
        raise ShortVideoError(f"short clip id already exists: {resolved_id}")
    return resolved_id


def _validate_unique_range(
    clips: list[ShortVideoClip],
    start: float,
    end: float,
    *,
    excluded_clip_id: str = "",
) -> None:
    if any(clip.proposal_id != excluded_clip_id and clip.start == start and clip.end == end for clip in clips):
        raise ShortVideoError("short video contains a duplicate clip range")


def _highlight_candidate(
    candidates: tuple[Mapping[str, Any], ...],
    candidate_id: Any,
) -> tuple[Mapping[str, Any], list[str]]:
    resolved_id = _required_id(candidate_id, "highlight candidate id")
    matches = [candidate for candidate in candidates if str(candidate.get("id", "")) == resolved_id]
    if not matches:
        raise ShortVideoError(f"highlight candidate was not found: {resolved_id}")
    if len(matches) > 1:
        raise ShortVideoError(f"highlight candidate id is duplicated: {resolved_id}")
    candidate = matches[0]
    raw_source_ids = candidate.get("source_segment_ids", [])
    if (
        not isinstance(raw_source_ids, list)
        or not raw_source_ids
        or not all(isinstance(item, str) and item for item in raw_source_ids)
    ):
        raise ShortVideoError("highlight candidate source ids are invalid")
    return candidate, list(raw_source_ids)


def apply_short_video_commands(
    project: Mapping[str, Any],
    commands: Iterable[ShortVideoCommand],
    *,
    highlight_candidates: Iterable[Mapping[str, Any]] = (),
) -> ShortVideoCommandResult:
    """Apply trusted short commands to detached state and return one atomic result.

    Callers own transport concerns such as proposal schema/revision/selection or GUI
    index binding. Clip lookup, source/segment bounds, duplicate prevention, and all
    mutations are deliberately centralized here.
    """

    short_video = short_video_from_project(project)
    context = _source_context(project)
    clips = list(short_video.clips)
    used_clip_ids = {clip.proposal_id for clip in clips}
    duration_target = short_video.duration_target_seconds
    candidates = tuple(candidate for candidate in highlight_candidates if isinstance(candidate, Mapping))
    changed_clip_ids: set[str] = set()

    for command in commands:
        if isinstance(command, AddShortVideoRangeClip):
            clip_id = _validate_new_clip_id(used_clip_ids, command.clip_id)
            start, end = _validated_range(
                command.source_start,
                command.source_end,
                context,
                field=clip_id,
            )
            _validate_unique_range(clips, start, end)
            clips.append(ShortVideoClip(proposal_id=clip_id, start=start, end=end))
            used_clip_ids.add(clip_id)
            changed_clip_ids.add(clip_id)
        elif isinstance(command, AddShortVideoSegmentClip):
            clip_id = _validate_new_clip_id(used_clip_ids, command.clip_id)
            segment_id = _required_id(command.segment_id, "short clip segment id")
            segment_range = context.segment_ranges.get(segment_id)
            if segment_range is None:
                raise ShortVideoError(f"short clip segment was not found: {segment_id}")
            start, end = _validated_range(*segment_range, context, field=clip_id)
            clips.append(
                ShortVideoClip(
                    proposal_id=clip_id,
                    segment_id=segment_id,
                    start=start,
                    end=end,
                )
            )
            used_clip_ids.add(clip_id)
            changed_clip_ids.add(clip_id)
        elif isinstance(command, RemoveShortVideoClip):
            index, clip_id = _clip_index(clips, command.clip_id)
            clips.pop(index)
            used_clip_ids.remove(clip_id)
            changed_clip_ids.add(clip_id)
        elif isinstance(command, MoveShortVideoClip):
            index, clip_id = _clip_index(clips, command.clip_id)
            before_clip_id = str(command.before_clip_id).strip()
            if before_clip_id == clip_id:
                continue
            if before_clip_id:
                _clip_index(clips, before_clip_id)
            clip = clips.pop(index)
            if before_clip_id:
                destination, _ = _clip_index(clips, before_clip_id)
                clips.insert(destination, clip)
            else:
                clips.append(clip)
            changed_clip_ids.add(clip_id)
        elif isinstance(command, UpdateShortVideoClip):
            index, clip_id = _clip_index(clips, command.clip_id)
            if not isinstance(command.changes, Mapping) or not command.changes:
                raise ShortVideoError("short clip update must contain changes")
            unknown = sorted(set(command.changes) - {"start", "end", "fit", "background_color"})
            if unknown:
                raise ShortVideoError("short clip update contains unsupported fields: " + ", ".join(unknown))
            clip = clips[index]
            if "start" in command.changes or "end" in command.changes:
                start, end = _validated_range(
                    command.changes.get("start", clip.start),
                    command.changes.get("end", clip.end),
                    context,
                    field=clip_id,
                )
                _validate_unique_range(clips, start, end, excluded_clip_id=clip_id)
                if clip.segment_id:
                    _validate_segment_range(context, clip.segment_id, start, end)
                clip = replace(clip, start=start, end=end)
            if "fit" in command.changes:
                fit = str(command.changes["fit"]).lower()
                if fit not in VALID_FIT_MODES:
                    raise ShortVideoError(f"clip.fit must be one of {VALID_FIT_MODES}")
                clip = replace(clip, fit=fit)
            if "background_color" in command.changes:
                try:
                    background_color = normalize_rgb_color(command.changes["background_color"])
                except (TypeError, ValueError, OverflowError) as error:
                    raise ShortVideoError(str(error)) from error
                clip = replace(clip, background_color=background_color)
            clips[index] = clip
            changed_clip_ids.add(clip_id)
        elif isinstance(command, UseShortVideoHighlightCandidate):
            clip_id = _validate_new_clip_id(used_clip_ids, command.clip_id)
            candidate, source_ids = _highlight_candidate(candidates, command.highlight_candidate_id)
            start, end = _validated_range(
                candidate.get("start"),
                candidate.get("end"),
                context,
                field=clip_id,
            )
            _validate_unique_range(clips, start, end)
            clips.append(
                ShortVideoClip(
                    proposal_id=clip_id,
                    highlight_candidate_id=_required_id(
                        command.highlight_candidate_id,
                        "highlight candidate id",
                    ),
                    segment_id=source_ids[0],
                    start=start,
                    end=end,
                )
            )
            used_clip_ids.add(clip_id)
            changed_clip_ids.add(clip_id)
        elif isinstance(command, SetShortVideoDurationTarget):
            target_seconds = _finite_seconds(command.target_seconds, "short duration target")
            if target_seconds <= 0.0:
                raise ShortVideoError("short duration target must be positive")
            if context.duration <= 0.0 or target_seconds > context.duration:
                raise ShortVideoError("short duration target is outside the source video")
            duration_target = target_seconds
        else:
            raise ShortVideoError(f"unsupported short video command: {type(command).__name__}")

    updated = replace(
        short_video,
        enabled=bool(clips),
        clips=clips,
        duration_target_seconds=duration_target,
    )
    return ShortVideoCommandResult(
        short_video=updated,
        changed_clip_ids=tuple(sorted(changed_clip_ids)),
    )
