from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


MEDIA_COMMAND_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class MediaSegment:
    label: str
    duration_seconds: float
    color: str
    tone_frequency_hz: int


@dataclass(frozen=True)
class MediaFixture:
    path: Path
    width: int
    height: int
    fps: int
    segments: tuple[MediaSegment, ...]

    @property
    def duration_seconds(self) -> float:
        return sum(segment.duration_seconds for segment in self.segments)

    def describe(self) -> str:
        bands = ", ".join(
            f"{segment.label}:{segment.duration_seconds:.3f}s/{segment.color}/{segment.tone_frequency_hz}Hz"
            for segment in self.segments
        )
        return f"{self.width}x{self.height}@{self.fps}fps, bands=[{bands}]"


@dataclass(frozen=True)
class FrameRegion:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class RgbFrame:
    width: int
    height: int
    pixels: bytes


@dataclass(frozen=True)
class FrameDifference:
    mean_absolute_channel_delta: float
    changed_pixels: int
    changed_ratio: float
    active_row_start: int | None
    active_row_end: int | None
    region: FrameRegion

    @property
    def occupied_height(self) -> int:
        if self.active_row_start is None or self.active_row_end is None:
            return 0
        return self.active_row_end - self.active_row_start + 1

    def describe(self) -> str:
        return (
            f"mean_delta={self.mean_absolute_channel_delta:.4f}, "
            f"changed_pixels={self.changed_pixels}, changed_ratio={self.changed_ratio:.6f}, "
            f"active_rows={self.active_row_start}..{self.active_row_end}, "
            f"occupied_height={self.occupied_height}, region={self.region}"
        )


def require_media_tools() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise AssertionError(f"Required media tools are missing: {', '.join(missing)}")


def _decode_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _command_failure(
    command: Sequence[str],
    *,
    context: str,
    stdout: str | bytes | None,
    stderr: str | bytes | None,
    detail: str,
) -> AssertionError:
    rendered_command = subprocess.list2cmdline(list(command))
    return AssertionError(
        "\n".join(
            [
                detail,
                f"context: {context or '(none)'}",
                f"command: {rendered_command}",
                "stdout:",
                _decode_output(stdout) or "(empty)",
                "stderr:",
                _decode_output(stderr) or "(empty)",
            ]
        )
    )


def run_media_command(
    command: Sequence[str],
    *,
    timeout_seconds: float = MEDIA_COMMAND_TIMEOUT_SECONDS,
    context: str = "",
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise _command_failure(
            command,
            context=context,
            stdout=error.stdout,
            stderr=error.stderr,
            detail=f"Media command timed out after {timeout_seconds:.1f}s.",
        ) from error
    if result.returncode != 0:
        raise _command_failure(
            command,
            context=context,
            stdout=result.stdout,
            stderr=result.stderr,
            detail=f"Media command failed with exit code {result.returncode}.",
        )
    return result


def _run_media_command_bytes(
    command: Sequence[str],
    *,
    timeout_seconds: float = MEDIA_COMMAND_TIMEOUT_SECONDS,
    context: str = "",
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            list(command),
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise _command_failure(
            command,
            context=context,
            stdout=error.stdout,
            stderr=error.stderr,
            detail=f"Media command timed out after {timeout_seconds:.1f}s.",
        ) from error
    if result.returncode != 0:
        raise _command_failure(
            command,
            context=context,
            stdout=result.stdout,
            stderr=result.stderr,
            detail=f"Media command failed with exit code {result.returncode}.",
        )
    return result


def create_lavfi_av_fixture(
    path: Path,
    segments: Sequence[MediaSegment],
    *,
    width: int = 320,
    height: int = 180,
    fps: int = 30,
    sample_rate: int = 48_000,
) -> MediaFixture:
    require_media_tools()
    resolved_segments = tuple(segments)
    if not resolved_segments:
        raise ValueError("At least one media segment is required.")
    if width <= 0 or height <= 0 or fps <= 0 or sample_rate <= 0:
        raise ValueError("Fixture dimensions, fps, and sample rate must be positive.")
    if any(segment.duration_seconds <= 0 for segment in resolved_segments):
        raise ValueError("Fixture segment durations must be positive.")

    path.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    filter_parts: list[str] = []
    concat_inputs: list[str] = []
    for index, segment in enumerate(resolved_segments):
        duration = f"{segment.duration_seconds:.6f}"
        command.extend(
            [
                "-f",
                "lavfi",
                "-i",
                f"color=c={segment.color}:s={width}x{height}:r={fps}:d={duration}",
                "-f",
                "lavfi",
                "-i",
                (f"sine=frequency={segment.tone_frequency_hz}:sample_rate={sample_rate}:duration={duration}"),
            ]
        )
        filter_parts.extend(
            [
                f"[{index * 2}:v]setpts=PTS-STARTPTS[v{index}]",
                f"[{index * 2 + 1}:a]asetpts=PTS-STARTPTS[a{index}]",
            ]
        )
        concat_inputs.extend([f"[v{index}]", f"[a{index}]"])

    if len(resolved_segments) == 1:
        filter_parts.extend(["[v0]null[outv]", "[a0]anull[outa]"])
    else:
        filter_parts.append("".join(concat_inputs) + f"concat=n={len(resolved_segments)}:v=1:a=1[outv][outa]")
    fixture = MediaFixture(path, width, height, fps, resolved_segments)
    command.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[outv]",
            "-map",
            "[outa]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(path),
        ]
    )
    run_media_command(command, context=f"lavfi fixture: {fixture.describe()}")
    if not path.is_file() or path.stat().st_size <= 0:
        raise AssertionError(f"FFmpeg did not create the fixture: {path}\n{fixture.describe()}")
    return fixture


def probe_media(path: Path) -> dict[str, object]:
    result = run_media_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        context=f"ffprobe: {path}",
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(f"ffprobe returned invalid JSON for {path}: {result.stdout}") from error
    if not isinstance(payload, dict):
        raise AssertionError(f"ffprobe returned a non-object payload for {path}: {payload!r}")
    return payload


def video_stream(probe: dict[str, object]) -> dict[str, object]:
    streams = probe.get("streams")
    if not isinstance(streams, list):
        raise AssertionError(f"ffprobe payload has no streams list: {probe!r}")
    for stream in streams:
        if isinstance(stream, dict) and stream.get("codec_type") == "video":
            return stream
    raise AssertionError(f"ffprobe payload has no video stream: {probe!r}")


def audio_streams(probe: dict[str, object]) -> list[dict[str, object]]:
    streams = probe.get("streams")
    if not isinstance(streams, list):
        raise AssertionError(f"ffprobe payload has no streams list: {probe!r}")
    return [stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "audio"]


def media_duration_seconds(probe: dict[str, object]) -> float:
    format_info = probe.get("format")
    if isinstance(format_info, dict):
        try:
            return float(format_info["duration"])
        except (KeyError, TypeError, ValueError):
            pass
    durations: list[float] = []
    streams = probe.get("streams")
    if isinstance(streams, list):
        for stream in streams:
            if not isinstance(stream, dict):
                continue
            try:
                durations.append(float(stream["duration"]))
            except (KeyError, TypeError, ValueError):
                continue
    if durations:
        return max(durations)
    raise AssertionError(f"ffprobe payload has no numeric duration: {probe!r}")


def extract_rgb_frame(
    path: Path,
    timestamp_seconds: float,
    *,
    probe: dict[str, object] | None = None,
) -> RgbFrame:
    metadata = probe or probe_media(path)
    stream = video_stream(metadata)
    width = int(stream["width"])
    height = int(stream["height"])
    result = _run_media_command_bytes(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-ss",
            f"{timestamp_seconds:.6f}",
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-an",
            "-sn",
            "-dn",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        context=f"extract frame at {timestamp_seconds:.6f}s from {path}",
    )
    expected_size = width * height * 3
    if len(result.stdout) != expected_size:
        raise AssertionError(
            f"Unexpected RGB frame size for {path} at {timestamp_seconds:.6f}s: "
            f"expected {expected_size}, got {len(result.stdout)}"
        )
    return RgbFrame(width, height, result.stdout)


def _resolved_region(frame: RgbFrame, region: FrameRegion | None) -> FrameRegion:
    selected = region or FrameRegion(0, 0, frame.width, frame.height)
    if (
        selected.x < 0
        or selected.y < 0
        or selected.width <= 0
        or selected.height <= 0
        or selected.x + selected.width > frame.width
        or selected.y + selected.height > frame.height
    ):
        raise ValueError(f"Region {selected} is outside {frame.width}x{frame.height}.")
    return selected


def mean_rgb(frame: RgbFrame, region: FrameRegion | None = None) -> tuple[float, float, float]:
    selected = _resolved_region(frame, region)
    totals = [0, 0, 0]
    for y in range(selected.y, selected.y + selected.height):
        row_start = (y * frame.width + selected.x) * 3
        row_end = row_start + selected.width * 3
        row = frame.pixels[row_start:row_end]
        totals[0] += sum(row[0::3])
        totals[1] += sum(row[1::3])
        totals[2] += sum(row[2::3])
    pixel_count = selected.width * selected.height
    return tuple(total / pixel_count for total in totals)


def mean_luma(frame: RgbFrame, region: FrameRegion | None = None) -> float:
    red, green, blue = mean_rgb(frame, region)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def compare_rgb_frames(
    reference: RgbFrame,
    actual: RgbFrame,
    *,
    region: FrameRegion | None = None,
    changed_pixel_threshold: int = 18,
    minimum_changed_pixels_per_active_row: int | None = None,
) -> FrameDifference:
    if (reference.width, reference.height) != (actual.width, actual.height):
        raise ValueError(f"Frame sizes differ: {reference.width}x{reference.height} != {actual.width}x{actual.height}")
    selected = _resolved_region(reference, region)
    active_row_threshold = minimum_changed_pixels_per_active_row or max(
        2,
        math.ceil(selected.width * 0.01),
    )
    total_channel_delta = 0
    changed_pixels = 0
    active_rows: list[int] = []
    for y in range(selected.y, selected.y + selected.height):
        changed_in_row = 0
        for x in range(selected.x, selected.x + selected.width):
            offset = (y * reference.width + x) * 3
            deltas = (
                abs(reference.pixels[offset] - actual.pixels[offset]),
                abs(reference.pixels[offset + 1] - actual.pixels[offset + 1]),
                abs(reference.pixels[offset + 2] - actual.pixels[offset + 2]),
            )
            total_channel_delta += sum(deltas)
            if max(deltas) >= changed_pixel_threshold:
                changed_pixels += 1
                changed_in_row += 1
        if changed_in_row >= active_row_threshold:
            active_rows.append(y)

    pixel_count = selected.width * selected.height
    return FrameDifference(
        mean_absolute_channel_delta=total_channel_delta / (pixel_count * 3),
        changed_pixels=changed_pixels,
        changed_ratio=changed_pixels / pixel_count,
        active_row_start=min(active_rows) if active_rows else None,
        active_row_end=max(active_rows) if active_rows else None,
        region=selected,
    )


def assert_frame_difference_present(
    difference: FrameDifference,
    *,
    context: str,
    minimum_changed_pixels: int = 80,
    minimum_mean_delta: float = 0.5,
) -> None:
    if (
        difference.changed_pixels < minimum_changed_pixels
        or difference.mean_absolute_channel_delta < minimum_mean_delta
        or difference.occupied_height <= 0
    ):
        raise AssertionError(f"Expected a visible frame difference for {context}: {difference.describe()}")


def assert_frame_difference_absent(
    difference: FrameDifference,
    *,
    context: str,
    maximum_changed_pixels: int = 24,
    maximum_mean_delta: float = 0.2,
) -> None:
    if (
        difference.changed_pixels > maximum_changed_pixels
        or difference.mean_absolute_channel_delta > maximum_mean_delta
    ):
        raise AssertionError(f"Expected no visible frame difference for {context}: {difference.describe()}")


def assert_mp4_faststart(path: Path) -> None:
    data = path.read_bytes()
    moov_offset: int | None = None
    mdat_offset: int | None = None
    index = 0
    while index + 8 <= len(data):
        size = int.from_bytes(data[index : index + 4], "big")
        header_size = 8
        if size == 1:
            if index + 16 > len(data):
                break
            size = int.from_bytes(data[index + 8 : index + 16], "big")
            header_size = 16
        elif size == 0:
            size = len(data) - index
        if size < header_size:
            break
        box_type = data[index + 4 : index + 8]
        if box_type == b"moov" and moov_offset is None:
            moov_offset = index
        elif box_type == b"mdat" and mdat_offset is None:
            mdat_offset = index
        index += size
    if moov_offset is None or mdat_offset is None or moov_offset >= mdat_offset:
        raise AssertionError(
            f"Expected faststart MP4 with moov before mdat: path={path}, moov={moov_offset}, mdat={mdat_offset}"
        )
