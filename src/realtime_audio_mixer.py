from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import time
from typing import Mapping

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtMultimedia import (
    QAudioBuffer,
    QAudioFormat,
    QAudioSink,
    QMediaDevices,
)


MIX_SAMPLE_RATE = 48_000
MIX_CHANNEL_COUNT = 2
MIX_BLOCK_FRAMES = 480
MIX_OUTPUT_BUFFER_MILLISECONDS = 100
MIX_PREROLL_MILLISECONDS = 120
MIX_PREROLL_TIMEOUT_SECONDS = 0.75
MIX_TIMESTAMP_TOLERANCE_FRAMES = 2
MIX_MAX_DECODE_LOOKAHEAD_SECONDS = 0.5
LIMITER_CEILING_DB = -1.5
LIMITER_CEILING = 10.0 ** (LIMITER_CEILING_DB / 20.0)
LIMITER_RELEASE_SECONDS = 0.08

def buffered_output_start_frame(
    base_frame: int,
    elapsed_frames: int,
    available_frames: int,
    reserve_frames: int,
) -> int:
    """Keep the output near the UI clock without consuming its PCM reserve."""
    base_frame = max(0, int(base_frame))
    elapsed_frames = max(0, int(elapsed_frames))
    skippable_frames = max(0, int(available_frames) - max(0, int(reserve_frames)))
    return base_frame + min(elapsed_frames, skippable_frames)


def build_mix_format() -> QAudioFormat:
    audio_format = QAudioFormat()
    audio_format.setSampleRate(MIX_SAMPLE_RATE)
    audio_format.setChannelCount(MIX_CHANNEL_COUNT)
    audio_format.setSampleFormat(QAudioFormat.Float)
    return audio_format


def audio_buffer_to_float32(buffer: QAudioBuffer) -> np.ndarray:
    """Return interleaved Qt audio as normalized float32 frames."""
    if not buffer.isValid() or buffer.byteCount() <= 0:
        return np.empty((0, 0), dtype=np.float32)
    audio_format = buffer.format()
    channel_count = max(1, audio_format.channelCount())
    raw = bytes(buffer.constData())
    sample_format = audio_format.sampleFormat()
    if sample_format == QAudioFormat.UInt8:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sample_format == QAudioFormat.Int16:
        usable = len(raw) - len(raw) % 2
        samples = np.frombuffer(raw[:usable], dtype="<i2").astype(np.float32) / 32768.0
    elif sample_format == QAudioFormat.Int32:
        usable = len(raw) - len(raw) % 4
        samples = np.frombuffer(raw[:usable], dtype="<i4").astype(np.float32) / 2147483648.0
    elif sample_format == QAudioFormat.Float:
        usable = len(raw) - len(raw) % 4
        samples = np.frombuffer(raw[:usable], dtype="<f4").astype(np.float32, copy=True)
        samples[~np.isfinite(samples)] = 0.0
    else:
        return np.empty((0, channel_count), dtype=np.float32)
    usable_samples = len(samples) - len(samples) % channel_count
    return samples[:usable_samples].reshape(-1, channel_count)


def encode_float32(samples: np.ndarray, audio_format: QAudioFormat) -> bytes:
    clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    sample_format = audio_format.sampleFormat()
    if sample_format == QAudioFormat.Float:
        return np.asarray(clipped, dtype="<f4").tobytes()
    if sample_format == QAudioFormat.Int16:
        return np.asarray(np.rint(clipped * 32767.0), dtype="<i2").tobytes()
    if sample_format == QAudioFormat.Int32:
        return np.asarray(np.rint(clipped * 2147483647.0), dtype="<i4").tobytes()
    if sample_format == QAudioFormat.UInt8:
        return np.asarray(np.rint(clipped * 127.0 + 128.0), dtype=np.uint8).tobytes()
    raise ValueError(f"Unsupported output sample format: {sample_format}")


def convert_channels(samples: np.ndarray, channel_count: int) -> np.ndarray:
    if samples.shape[1] == channel_count:
        return samples
    if channel_count == 1:
        return np.mean(samples, axis=1, keepdims=True, dtype=np.float32)
    if samples.shape[1] == 1:
        return np.repeat(samples, channel_count, axis=1)
    if channel_count == 2:
        return samples[:, :2]
    converted = np.zeros((samples.shape[0], channel_count), dtype=np.float32)
    copied = min(samples.shape[1], channel_count)
    converted[:, :copied] = samples[:, :copied]
    return converted


def resample_linear(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if not len(samples) or source_rate <= 0 or source_rate == target_rate:
        return samples
    output_frames = max(1, round(len(samples) * target_rate / source_rate))
    source_positions = np.arange(len(samples), dtype=np.float64)
    target_positions = np.arange(output_frames, dtype=np.float64) * source_rate / target_rate
    target_positions = np.minimum(target_positions, max(0, len(samples) - 1))
    output = np.empty((output_frames, samples.shape[1]), dtype=np.float32)
    for channel in range(samples.shape[1]):
        output[:, channel] = np.interp(
            target_positions,
            source_positions,
            samples[:, channel],
        )
    return output


@dataclass
class AudioChunk:
    start_frame: int
    samples: np.ndarray

    @property
    def end_frame(self) -> int:
        return self.start_frame + len(self.samples)


class TimelineAudioMixer:
    """Timestamp-aligned PCM summing bus with a linked-channel peak limiter."""

    def __init__(self, sample_rate: int, channel_count: int) -> None:
        self.sample_rate = sample_rate
        self.channel_count = channel_count
        self._chunks: dict[str, deque[AudioChunk]] = {}
        self._gains: dict[str, float] = {}
        self._offset_frames: dict[str, int] = {}
        self._cursor_frame = 0
        self._limiter_gain = 1.0
        self.channel_peaks: dict[str, float] = {}
        self.master_peak = 0.0
        self.limiter_reduction_db = 0.0

    @property
    def cursor_frame(self) -> int:
        return self._cursor_frame

    @property
    def active_channel_ids(self) -> set[str]:
        return {channel_id for channel_id, gain in self._gains.items() if gain > 0.0}

    def set_channels(
        self,
        gains: Mapping[str, float],
        offsets_seconds: Mapping[str, float],
    ) -> None:
        self._gains = {
            str(channel_id): max(0.0, float(gain))
            for channel_id, gain in gains.items()
        }
        self._offset_frames = {
            str(channel_id): round(float(offset) * self.sample_rate)
            for channel_id, offset in offsets_seconds.items()
        }
        known_ids = set(self._gains)
        self._chunks = {
            channel_id: chunks
            for channel_id, chunks in self._chunks.items()
            if channel_id in known_ids
        }

    def reset(self, position_frame: int) -> None:
        self._chunks.clear()
        self._cursor_frame = max(0, int(position_frame))
        self._limiter_gain = 1.0
        self.channel_peaks = {}
        self.master_peak = 0.0
        self.limiter_reduction_db = 0.0

    def set_cursor(self, position_frame: int) -> None:
        self._cursor_frame = max(0, int(position_frame))

    def push(self, channel_id: str, stream_start_frame: int, samples: np.ndarray) -> None:
        channel_id = str(channel_id)
        if channel_id not in self._gains or not len(samples):
            return
        samples = convert_channels(np.asarray(samples, dtype=np.float32), self.channel_count)
        absolute_start = int(stream_start_frame) + self._offset_frames.get(channel_id, 0)
        chunk = AudioChunk(absolute_start, np.ascontiguousarray(samples))
        lookahead_frames = round(self.sample_rate * MIX_MAX_DECODE_LOOKAHEAD_SECONDS)
        if chunk.start_frame > self._cursor_frame + lookahead_frames:
            return
        if chunk.end_frame <= self._cursor_frame - self.sample_rate:
            return
        chunks = self._chunks.setdefault(channel_id, deque())
        if chunks and absolute_start < chunks[-1].start_frame:
            ordered = sorted((*chunks, chunk), key=lambda item: item.start_frame)
            self._chunks[channel_id] = deque(ordered)
        else:
            chunks.append(chunk)

    def available_ahead_frames(self, position_frame: int) -> int:
        """Return contiguous frames ready across every active channel.

        Time before a positive channel offset is known silence and therefore
        counts as available. Tiny timestamp rounding gaps are tolerated because
        Qt buffer timestamps can differ from the resampled frame count by one
        or two frames.
        """
        active_ids = self.active_channel_ids
        if not active_ids:
            return 2**63 - 1
        position_frame = max(0, int(position_frame))
        available: int | None = None
        for channel_id in active_ids:
            offset_frame = self._offset_frames.get(channel_id, 0)
            coverage_end = max(position_frame, offset_frame)
            for chunk in self._chunks.get(channel_id, ()):
                if chunk.end_frame <= coverage_end:
                    continue
                if chunk.start_frame > coverage_end + MIX_TIMESTAMP_TOLERANCE_FRAMES:
                    break
                coverage_end = max(coverage_end, chunk.end_frame)
            channel_available = max(0, coverage_end - position_frame)
            available = (
                channel_available
                if available is None
                else min(available, channel_available)
            )
        return available or 0

    def has_preroll(self, position_frame: int, frame_count: int = 1) -> bool:
        return self.available_ahead_frames(position_frame) >= max(0, int(frame_count))

    def can_mix(self, frame_count: int) -> bool:
        return self.has_preroll(self._cursor_frame, frame_count)

    def _mix_channel(self, channel_id: str, start_frame: int, frame_count: int) -> np.ndarray:
        mixed = np.zeros((frame_count, self.channel_count), dtype=np.float32)
        end_frame = start_frame + frame_count
        chunks = self._chunks.get(channel_id)
        if not chunks:
            return mixed
        while chunks and chunks[0].end_frame <= start_frame:
            chunks.popleft()
        for chunk in chunks:
            if chunk.start_frame >= end_frame:
                break
            overlap_start = max(start_frame, chunk.start_frame)
            overlap_end = min(end_frame, chunk.end_frame)
            if overlap_end <= overlap_start:
                continue
            output_start = overlap_start - start_frame
            input_start = overlap_start - chunk.start_frame
            length = overlap_end - overlap_start
            mixed[output_start : output_start + length] += chunk.samples[
                input_start : input_start + length
            ]
        return mixed

    def _limit(self, samples: np.ndarray) -> np.ndarray:
        input_peak = float(np.max(np.abs(samples), initial=0.0))
        target_gain = min(1.0, LIMITER_CEILING / input_peak) if input_peak > 0.0 else 1.0
        if target_gain < self._limiter_gain:
            self._limiter_gain = target_gain
        else:
            block_seconds = len(samples) / self.sample_rate
            release = 1.0 - math.exp(-block_seconds / LIMITER_RELEASE_SECONDS)
            self._limiter_gain += (target_gain - self._limiter_gain) * release
        limited = np.clip(samples * self._limiter_gain, -LIMITER_CEILING, LIMITER_CEILING)
        self.master_peak = float(np.max(np.abs(limited), initial=0.0))
        self.limiter_reduction_db = (
            -20.0 * math.log10(max(self._limiter_gain, 1e-9))
            if self._limiter_gain < 1.0
            else 0.0
        )
        return limited.astype(np.float32, copy=False)

    def mix(self, frame_count: int) -> np.ndarray:
        frame_count = max(0, int(frame_count))
        start_frame = self._cursor_frame
        master = np.zeros((frame_count, self.channel_count), dtype=np.float32)
        peaks: dict[str, float] = {}
        for channel_id, gain in self._gains.items():
            if gain <= 0.0:
                chunks = self._chunks.get(channel_id)
                discard_before = start_frame + frame_count
                while chunks and chunks[0].end_frame <= discard_before:
                    chunks.popleft()
                peaks[channel_id] = 0.0
                continue
            channel_audio = self._mix_channel(channel_id, start_frame, frame_count)
            channel_audio *= gain
            peaks[channel_id] = float(np.max(np.abs(channel_audio), initial=0.0))
            master += channel_audio
        self._cursor_frame += frame_count
        self.channel_peaks = peaks
        return self._limit(master)


class RealtimeAudioMixer(QObject):
    """Qt audio-device bridge for TimelineAudioMixer."""

    metricsChanged = Signal(float, float)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.audio_format = build_mix_format()
        self.core = TimelineAudioMixer(
            self.audio_format.sampleRate(),
            self.audio_format.channelCount(),
        )
        self._sink: QAudioSink | None = None
        self._device = None
        self._playing = False
        self._started = False
        self._base_frame = 0
        self._started_at = 0.0
        self._seen_channels: set[str] = set()
        self._pending_output = bytearray()
        self._timer = QTimer(self)
        self._timer.setInterval(5)
        self._timer.timeout.connect(self._pump)

    @property
    def playing(self) -> bool:
        return self._playing

    def set_channels(
        self,
        gains: Mapping[str, float],
        offsets_seconds: Mapping[str, float],
    ) -> None:
        self.core.set_channels(gains, offsets_seconds)

    def play(self, position_milliseconds: int) -> None:
        self.stop(reset_metrics=False)
        self._playing = True
        self._base_frame = round(
            max(0, int(position_milliseconds)) * self.audio_format.sampleRate() / 1000
        )
        self.core.reset(self._base_frame)
        self._started_at = time.monotonic()
        self._seen_channels.clear()
        self._timer.start()

    def seek(self, position_milliseconds: int, playing: bool) -> None:
        if playing:
            self.play(position_milliseconds)
        else:
            self.stop()
            self.core.reset(
                round(max(0, int(position_milliseconds)) * self.audio_format.sampleRate() / 1000)
            )

    def stop(self, *, reset_metrics: bool = True) -> None:
        self._playing = False
        self._started = False
        self._timer.stop()
        self._pending_output.clear()
        self._device = None
        if self._sink is not None:
            self._sink.stop()
            self._sink.deleteLater()
            self._sink = None
        if reset_metrics:
            self.metricsChanged.emit(0.0, 0.0)

    def push_buffer(self, channel_id: str, buffer: QAudioBuffer) -> float:
        if not buffer.isValid() or buffer.byteCount() <= 0:
            return 0.0
        samples = audio_buffer_to_float32(buffer)
        if not len(samples):
            return 0.0
        peak = float(np.max(np.abs(samples), initial=0.0))
        if not self._playing:
            return peak
        source_rate = buffer.format().sampleRate()
        samples = resample_linear(samples, source_rate, self.audio_format.sampleRate())
        samples = convert_channels(samples, self.audio_format.channelCount())
        start_microseconds = buffer.startTime()
        if start_microseconds < 0:
            start_frame = self.core.cursor_frame
        else:
            start_frame = round(start_microseconds * self.audio_format.sampleRate() / 1_000_000)
        self.core.push(channel_id, start_frame, samples)
        self._seen_channels.add(str(channel_id))
        return peak

    def _start_sink(self, position_frame: int) -> bool:
        output_device = QMediaDevices.defaultAudioOutput()
        if output_device.isNull() or not output_device.isFormatSupported(self.audio_format):
            return False
        self.core.set_cursor(position_frame)
        self._sink = QAudioSink(output_device, self.audio_format, self)
        self._sink.setBufferSize(
            self.audio_format.bytesForDuration(MIX_OUTPUT_BUFFER_MILLISECONDS * 1000)
        )
        self._device = self._sink.start()
        self._started = self._device is not None
        return self._started

    def _write_pending(self) -> bool:
        if not self._pending_output or self._device is None:
            return True
        written = self._device.write(bytes(self._pending_output))
        if written < 0:
            self.stop()
            return False
        if written:
            del self._pending_output[:written]
        return not self._pending_output

    def _pump(self) -> None:
        if not self._playing:
            return
        elapsed = max(0.0, time.monotonic() - self._started_at)
        if not self._started:
            required = self.core.active_channel_ids
            preroll_frames = round(
                MIX_PREROLL_MILLISECONDS * self.audio_format.sampleRate() / 1000
            )
            available_frames = self.core.available_ahead_frames(self._base_frame)
            ready = required.issubset(self._seen_channels) and available_frames >= preroll_frames
            if not ready and elapsed < MIX_PREROLL_TIMEOUT_SECONDS:
                return
            if not ready and not (
                required.issubset(self._seen_channels)
                and available_frames >= MIX_BLOCK_FRAMES
            ):
                return
            elapsed_frames = round(elapsed * self.audio_format.sampleRate())
            output_start_frame = buffered_output_start_frame(
                self._base_frame,
                elapsed_frames,
                available_frames,
                preroll_frames,
            )
            if not self._start_sink(output_start_frame):
                self.stop()
                return
        if not self._write_pending() or self._sink is None:
            return
        block_bytes = self.audio_format.bytesForFrames(MIX_BLOCK_FRAMES)
        blocks_written = 0
        max_blocks = max(
            1,
            math.ceil(
                MIX_OUTPUT_BUFFER_MILLISECONDS
                * self.audio_format.sampleRate()
                / 1000
                / MIX_BLOCK_FRAMES
            ),
        )
        while (
            self._sink.bytesFree() >= block_bytes
            and blocks_written < max_blocks
            and self.core.can_mix(MIX_BLOCK_FRAMES)
        ):
            mixed = self.core.mix(MIX_BLOCK_FRAMES)
            self._pending_output.extend(encode_float32(mixed, self.audio_format))
            if not self._write_pending():
                break
            blocks_written += 1
        if blocks_written:
            self.metricsChanged.emit(
                min(1.0, self.core.master_peak / LIMITER_CEILING),
                self.core.limiter_reduction_db,
            )
