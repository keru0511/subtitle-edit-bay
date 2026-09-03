from __future__ import annotations

import json
import math
import os
import re
import signal
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


MEDIA_COMMAND_TIMEOUT_SECONDS = 30.0
PROCESS_TREE_TERMINATION_TIMEOUT_SECONDS = 5.0


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
    sample_rate: int = 48_000
    audio_channel_layout: str = "mono"
    tone_volume_db: float = 0.0

    @property
    def duration_seconds(self) -> float:
        return sum(segment.duration_seconds for segment in self.segments)

    def describe(self) -> str:
        bands = ", ".join(
            f"{segment.label}:{segment.duration_seconds:.3f}s/{segment.color}/{segment.tone_frequency_hz}Hz"
            for segment in self.segments
        )
        return (
            f"{self.width}x{self.height}@{self.fps}fps, bands=[{bands}], "
            f"audio={self.sample_rate}Hz/{self.audio_channel_layout}/{self.tone_volume_db:g}dB"
        )


@dataclass(frozen=True)
class AudioFixture:
    path: Path
    frequency_hz: int
    duration_seconds: float
    sample_rate: int
    channel_layout: str
    volume_db: float

    def describe(self) -> str:
        return (
            f"path={self.path}, frequency={self.frequency_hz}Hz, "
            f"duration={self.duration_seconds:.3f}s, sample_rate={self.sample_rate}Hz, "
            f"channel_layout={self.channel_layout}, volume={self.volume_db:g}dB"
        )


@dataclass(frozen=True)
class AudioLevelMeasurement:
    path: Path
    frequency_hz: int | None
    bandwidth_hz: int | None
    mean_volume_db: float
    max_volume_db: float
    command: tuple[str, ...]
    stderr: str

    def describe(self) -> str:
        band = (
            f"frequency={self.frequency_hz}Hz, bandwidth={self.bandwidth_hz}Hz"
            if self.frequency_hz is not None
            else "frequency=broadband"
        )
        return (
            f"path={self.path}, {band}, mean_volume={self.mean_volume_db:.2f}dB, "
            f"max_volume={self.max_volume_db:.2f}dB\n"
            f"command: {subprocess.list2cmdline(list(self.command))}\n"
            f"ffmpeg stderr:\n{self.stderr or '(empty)'}"
        )


@dataclass(frozen=True)
class IntegratedLoudnessMeasurement:
    path: Path
    integrated_lufs: float
    start_seconds: float
    duration_seconds: float | None
    command: tuple[str, ...]
    stderr: str

    def describe(self) -> str:
        interval = (
            f"start={self.start_seconds:.3f}s, duration={self.duration_seconds:.3f}s"
            if self.duration_seconds is not None
            else f"start={self.start_seconds:.3f}s, duration=remaining"
        )
        return (
            f"path={self.path}, integrated_loudness={self.integrated_lufs:.2f} LUFS, {interval}\n"
            f"command: {subprocess.list2cmdline(list(self.command))}\n"
            f"ffmpeg stderr:\n{self.stderr or '(empty)'}"
        )


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


def _process_creation_options() -> tuple[bool, int]:
    if os.name == "nt":
        return False, int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    return True, 0


def _terminate_process_tree(process: subprocess.Popen[str] | subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=PROCESS_TREE_TERMINATION_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass

    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def _collect_output_after_termination(
    process: subprocess.Popen[str] | subprocess.Popen[bytes],
    timeout_error: subprocess.TimeoutExpired,
) -> tuple[str | bytes | None, str | bytes | None]:
    try:
        return process.communicate(timeout=PROCESS_TREE_TERMINATION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as cleanup_error:
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        try:
            process.wait(timeout=PROCESS_TREE_TERMINATION_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        return (
            cleanup_error.stdout if cleanup_error.stdout is not None else timeout_error.stdout,
            cleanup_error.stderr if cleanup_error.stderr is not None else timeout_error.stderr,
        )


def run_media_command(
    command: Sequence[str],
    *,
    timeout_seconds: float = MEDIA_COMMAND_TIMEOUT_SECONDS,
    context: str = "",
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    start_new_session, creationflags = _process_creation_options()
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=start_new_session,
            creationflags=creationflags,
        )
    except OSError as error:
        raise _command_failure(
            command,
            context=context,
            stdout=None,
            stderr=str(error),
            detail="Media command could not start.",
        ) from error

    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        _terminate_process_tree(process)
        timed_out_stdout, timed_out_stderr = _collect_output_after_termination(process, error)
        raise _command_failure(
            command,
            context=context,
            stdout=timed_out_stdout,
            stderr=timed_out_stderr,
            detail=f"Media command timed out after {timeout_seconds:.1f}s.",
        ) from error
    if process.returncode != 0:
        raise _command_failure(
            command,
            context=context,
            stdout=stdout,
            stderr=stderr,
            detail=f"Media command failed with exit code {process.returncode}.",
        )
    return subprocess.CompletedProcess(list(command), process.returncode, stdout, stderr)


def _run_media_command_bytes(
    command: Sequence[str],
    *,
    timeout_seconds: float = MEDIA_COMMAND_TIMEOUT_SECONDS,
    context: str = "",
) -> subprocess.CompletedProcess[bytes]:
    start_new_session, creationflags = _process_creation_options()
    try:
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=start_new_session,
            creationflags=creationflags,
        )
    except OSError as error:
        raise _command_failure(
            command,
            context=context,
            stdout=None,
            stderr=str(error),
            detail="Media command could not start.",
        ) from error

    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        _terminate_process_tree(process)
        timed_out_stdout, timed_out_stderr = _collect_output_after_termination(process, error)
        raise _command_failure(
            command,
            context=context,
            stdout=timed_out_stdout,
            stderr=timed_out_stderr,
            detail=f"Media command timed out after {timeout_seconds:.1f}s.",
        ) from error
    if process.returncode != 0:
        raise _command_failure(
            command,
            context=context,
            stdout=stdout,
            stderr=stderr,
            detail=f"Media command failed with exit code {process.returncode}.",
        )
    return subprocess.CompletedProcess(list(command), process.returncode, stdout, stderr)


def create_lavfi_av_fixture(
    path: Path,
    segments: Sequence[MediaSegment],
    *,
    width: int = 320,
    height: int = 180,
    fps: int = 30,
    sample_rate: int = 48_000,
    audio_channel_layout: str = "mono",
    tone_volume_db: float = 0.0,
) -> MediaFixture:
    require_media_tools()
    resolved_segments = tuple(segments)
    if not resolved_segments:
        raise ValueError("At least one media segment is required.")
    if width <= 0 or height <= 0 or fps <= 0 or sample_rate <= 0:
        raise ValueError("Fixture dimensions, fps, and sample rate must be positive.")
    if audio_channel_layout not in {"mono", "stereo"}:
        raise ValueError("Fixture audio channel layout must be mono or stereo.")
    if not math.isfinite(tone_volume_db):
        raise ValueError("Fixture tone volume must be finite.")
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
                (
                    f"[{index * 2 + 1}:a]asetpts=PTS-STARTPTS,volume={tone_volume_db:g}dB,"
                    f"aformat=sample_rates={sample_rate}:channel_layouts={audio_channel_layout}[a{index}]"
                ),
            ]
        )
        concat_inputs.extend([f"[v{index}]", f"[a{index}]"])

    if len(resolved_segments) == 1:
        filter_parts.extend(["[v0]null[outv]", "[a0]anull[outa]"])
    else:
        filter_parts.append("".join(concat_inputs) + f"concat=n={len(resolved_segments)}:v=1:a=1[outv][outa]")
    fixture = MediaFixture(
        path,
        width,
        height,
        fps,
        resolved_segments,
        sample_rate=sample_rate,
        audio_channel_layout=audio_channel_layout,
        tone_volume_db=tone_volume_db,
    )
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


def create_lavfi_audio_fixture(
    path: Path,
    *,
    frequency_hz: int,
    duration_seconds: float,
    sample_rate: int = 48_000,
    channel_layout: str = "stereo",
    volume_db: float = 0.0,
) -> AudioFixture:
    require_media_tools()
    if frequency_hz <= 0 or duration_seconds <= 0 or sample_rate <= 0:
        raise ValueError("Audio fixture frequency, duration, and sample rate must be positive.")
    if channel_layout not in {"mono", "stereo"}:
        raise ValueError("Audio fixture channel layout must be mono or stereo.")
    if not math.isfinite(volume_db):
        raise ValueError("Audio fixture volume must be finite.")
    codecs = {".flac": "flac", ".wav": "pcm_s16le", ".wave": "pcm_s16le"}
    audio_codec = codecs.get(path.suffix.lower())
    if audio_codec is None:
        raise ValueError("Audio fixture output must use a .wav, .wave, or .flac extension.")

    fixture = AudioFixture(
        path=path,
        frequency_hz=frequency_hz,
        duration_seconds=duration_seconds,
        sample_rate=sample_rate,
        channel_layout=channel_layout,
        volume_db=volume_db,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency_hz}:sample_rate={sample_rate}:duration={duration_seconds:.6f}",
        "-af",
        (f"volume={volume_db:g}dB,aformat=sample_fmts=s16:sample_rates={sample_rate}:channel_layouts={channel_layout}"),
        "-c:a",
        audio_codec,
        str(path),
    ]
    run_media_command(command, context=f"lavfi audio fixture: {fixture.describe()}")
    if not path.is_file() or path.stat().st_size <= 0:
        raise AssertionError(f"FFmpeg did not create the audio fixture: {fixture.describe()}")
    return fixture


def _parse_decibel_value(value: str) -> float:
    return float(value.lower())


def measure_audio_level(
    path: Path,
    *,
    frequency_hz: int | None = None,
    bandwidth_hz: int = 80,
) -> AudioLevelMeasurement:
    require_media_tools()
    if frequency_hz is not None and (frequency_hz <= 0 or bandwidth_hz <= 0):
        raise ValueError("Band frequency and bandwidth must be positive.")
    audio_filter = "volumedetect"
    resolved_bandwidth: int | None = None
    if frequency_hz is not None:
        resolved_bandwidth = bandwidth_hz
        audio_filter = f"bandpass=frequency={frequency_hz}:width_type=h:width={bandwidth_hz},volumedetect"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-vn",
        "-af",
        audio_filter,
        "-f",
        "null",
        os.devnull,
    ]
    result = run_media_command(
        command,
        context=(
            f"measure audio level: path={path}, "
            f"frequency={f'{frequency_hz}Hz' if frequency_hz is not None else 'broadband'}, "
            f"bandwidth={resolved_bandwidth}Hz"
        ),
    )
    mean_matches = re.findall(r"mean_volume:\s*(-?(?:[0-9]+(?:\.[0-9]+)?|inf))\s*dB", result.stderr)
    max_matches = re.findall(r"max_volume:\s*(-?(?:[0-9]+(?:\.[0-9]+)?|inf))\s*dB", result.stderr)
    if not mean_matches or not max_matches:
        raise _command_failure(
            command,
            context=f"parse volumedetect output for {path}",
            stdout=result.stdout,
            stderr=result.stderr,
            detail="FFmpeg volumedetect output did not contain mean_volume and max_volume.",
        )
    return AudioLevelMeasurement(
        path=path,
        frequency_hz=frequency_hz,
        bandwidth_hz=resolved_bandwidth,
        mean_volume_db=_parse_decibel_value(mean_matches[-1]),
        max_volume_db=_parse_decibel_value(max_matches[-1]),
        command=tuple(command),
        stderr=result.stderr,
    )


def measure_integrated_loudness(
    path: Path,
    *,
    start_seconds: float = 0.0,
    duration_seconds: float | None = None,
) -> IntegratedLoudnessMeasurement:
    require_media_tools()
    if start_seconds < 0 or not math.isfinite(start_seconds):
        raise ValueError("Integrated loudness measurement start must be finite and non-negative.")
    if duration_seconds is not None and (duration_seconds <= 0 or not math.isfinite(duration_seconds)):
        raise ValueError("Integrated loudness measurement duration must be finite and positive.")
    filters = [f"atrim=start={start_seconds:g}"]
    if duration_seconds is not None:
        filters[0] += f":duration={duration_seconds:g}"
    filters.extend(["asetpts=PTS-STARTPTS", "ebur128"])
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-vn",
        "-af",
        ",".join(filters),
        "-f",
        "null",
        os.devnull,
    ]
    result = run_media_command(
        command,
        context=(
            f"measure EBU R128 integrated loudness: path={path}, "
            f"start={start_seconds:g}s, duration={duration_seconds if duration_seconds is not None else 'remaining'}s"
        ),
    )
    matches = re.findall(r"\bI:\s*(-?(?:[0-9]+(?:\.[0-9]+)?|inf))\s*LUFS", result.stderr)
    if not matches:
        raise _command_failure(
            command,
            context=f"parse ebur128 output for {path}",
            stdout=result.stdout,
            stderr=result.stderr,
            detail="FFmpeg ebur128 output did not contain integrated loudness.",
        )
    return IntegratedLoudnessMeasurement(
        path=path,
        integrated_lufs=_parse_decibel_value(matches[-1]),
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
        command=tuple(command),
        stderr=result.stderr,
    )


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
    try:
        width = int(str(stream["width"]))
        height = int(str(stream["height"]))
    except (KeyError, ValueError) as error:
        raise AssertionError(f"Video stream has invalid dimensions: {stream!r}") from error
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
    return (
        totals[0] / pixel_count,
        totals[1] / pixel_count,
        totals[2] / pixel_count,
    )


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
