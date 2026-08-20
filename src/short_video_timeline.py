from __future__ import annotations

from dataclasses import dataclass

from .short_video_schema import ShortVideo, ShortVideoClip


@dataclass(frozen=True)
class ShortVideoTimelineClip:
    clip: ShortVideoClip
    output_start: float
    overlap: float

    @property
    def duration(self) -> float:
        return self.clip.end - self.clip.start

    @property
    def output_end(self) -> float:
        return self.output_start + self.duration


@dataclass(frozen=True)
class ShortVideoTimeline:
    clips: tuple[ShortVideoTimelineClip, ...]
    total_duration: float


def build_short_video_timeline(short_video: ShortVideo) -> ShortVideoTimeline:
    """Map source clips onto the rendered timeline, including transition overlap."""
    timeline: list[ShortVideoTimelineClip] = []
    total_duration = 0.0

    for index, clip in enumerate(short_video.clips):
        duration = max(0.0, clip.end - clip.start)
        overlap = 0.0
        if (
            index > 0
            and short_video.transition.type != "cut"
            and short_video.transition.duration > 0.0
        ):
            overlap = min(short_video.transition.duration, total_duration, duration)
        output_start = max(0.0, total_duration - overlap)
        entry = ShortVideoTimelineClip(
            clip=clip,
            output_start=output_start,
            overlap=overlap,
        )
        timeline.append(entry)
        total_duration = entry.output_end

    return ShortVideoTimeline(clips=tuple(timeline), total_duration=total_duration)
