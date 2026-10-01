from __future__ import annotations

from array import array
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
import math
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Mapping, Protocol

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat

from .qt_decorators import Signal, Slot
from .qt_audio_buffer_output import AudioBufferSignal, QAudioBufferOutput
from .audio_mixer import (
    AUDIO_MIX_MASTER_GAIN,
    MAX_VOLUME_PERCENT,
    active_audio_mix_channels,
)
from .audio_preview_cache import (
    AudioPreviewCacheResult,
    AudioPreviewCacheStats,
    audio_preview_cache_entries,
    audio_preview_cache_stats,
    cached_audio_preview_paths,
    clear_audio_preview_cache,
    prepare_audio_preview_cache,
)
from .data_boundary import coerce_float, is_string_object_mapping, is_object_iterable
from .realtime_audio_mixer import RealtimeAudioMixer


class CachePreparation(Protocol):
    def __call__(
        self,
        project: Mapping[str, object],
        cache_root: Path,
        /,
        *,
        protected_paths: list[Path],
    ) -> AudioPreviewCacheResult: ...


CacheClear = Callable[[str | Path], object]
MixerFactory = Callable[[QObject], RealtimeAudioMixer]


class AudioPreviewController(QObject):
    """Own audio-preview cache, channel views, levels, and transport state.

    The GUI backend remains the QML facade.  This controller deliberately
    communicates with it through Qt signals and accepts a project mapping,
    rather than keeping a reference to the backend itself.  Existing cache,
    mixer, gain, and level algorithms are kept intact so this boundary only
    changes ownership.
    """

    cacheChanged = Signal()
    previewChannelsChanged = Signal()
    previewGainsChanged = Signal()
    levelsChanged = Signal()
    masterMetricsChanged = Signal()
    projectDataChanged = Signal()
    statusChanged = Signal(str, str)
    if TYPE_CHECKING:
        cacheCompleted = Signal(int, AudioPreviewCacheResult)
    else:
        cacheCompleted = Signal(int, object)

    def __init__(
        self,
        cache_root: str | Path,
        *,
        parent: QObject | None = None,
        prepare_cache: CachePreparation = prepare_audio_preview_cache,
        clear_cache: CacheClear = clear_audio_preview_cache,
        mixer_factory: MixerFactory = RealtimeAudioMixer,
    ) -> None:
        super().__init__(parent)
        self.cache_root = Path(cache_root)
        self._project: dict[str, object] | None = None
        self._prepare_cache = prepare_cache
        self._clear_cache = clear_cache
        self._cache_paths: dict[str, str] = {}
        self._cache_future: Future[AudioPreviewCacheResult] | None = None
        self._cache_request = 0
        self._generation = 0
        self._preparing = False
        self._force_cache_rebuild = False
        self._cache_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="audio-preview",
        )
        self._outputs: dict[str, QAudioBufferOutput] = {}
        self._mixer = mixer_factory(self)
        self._master_level = 0.0
        self._limiter_reduction_db = 0.0
        self._gains: dict[str, float] = {}
        self._levels: dict[str, float] = {}
        self._pending_levels: dict[str, float] = {}
        self._level_timer = QTimer(self)
        self._level_timer.setInterval(33)
        self._level_timer.timeout.connect(self.publish_levels)
        self._mixer.metricsChanged.connect(self._update_master_metrics)
        self.cacheCompleted.connect(self._apply_audio_preview_cache)

    @property
    def project(self) -> dict[str, object] | None:
        return self._project

    def set_project(self, project: dict[str, object] | None) -> None:
        """Update the project mapping used by preview derivation.

        The mapping is intentionally not copied: the backend mutates its
        existing project while editing audio-mix settings, and the controller
        must observe those changes without moving project ownership here.
        """

        self._project = project

    def _audio_mix(self) -> Mapping[str, object]:
        section = self._project.get("audio_mix") if self._project is not None else None
        return section if is_string_object_mapping(section) else {}

    def _channels(self) -> list[dict[str, object]]:
        value = self._audio_mix().get("channels")
        if not is_object_iterable(value):
            return []
        return [dict(channel) for channel in value if is_string_object_mapping(channel)]

    @property
    def cache_paths(self) -> dict[str, str]:
        return self._cache_paths

    def set_cache_paths(self, paths: Mapping[str, str]) -> None:
        self._cache_paths.clear()
        self._cache_paths.update({str(channel_id): str(path) for channel_id, path in paths.items()})

    @property
    def cache_future(self) -> Future[AudioPreviewCacheResult] | None:
        return self._cache_future

    @cache_future.setter
    def cache_future(self, value: Future[AudioPreviewCacheResult] | None) -> None:
        self._cache_future = value

    @property
    def cache_request(self) -> int:
        return self._cache_request

    @cache_request.setter
    def cache_request(self, value: int) -> None:
        self._cache_request = int(value)

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def preparing(self) -> bool:
        return self._preparing

    @preparing.setter
    def preparing(self, value: bool) -> None:
        self._preparing = bool(value)

    @property
    def outputs(self) -> dict[str, QAudioBufferOutput]:
        return self._outputs

    @property
    def mixer(self) -> RealtimeAudioMixer:
        return self._mixer

    @property
    def level_timer(self) -> QTimer:
        return self._level_timer

    @property
    def gains(self) -> dict[str, float]:
        return self._gains

    @property
    def levels(self) -> dict[str, float]:
        return self._levels

    @property
    def pending_levels(self) -> dict[str, float]:
        return self._pending_levels

    @property
    def master_level(self) -> float:
        return self._master_level

    @master_level.setter
    def master_level(self, value: float) -> None:
        self._master_level = float(value)

    @property
    def limiter_reduction_db(self) -> float:
        return self._limiter_reduction_db

    @limiter_reduction_db.setter
    def limiter_reduction_db(self, value: float) -> None:
        self._limiter_reduction_db = float(value)

    @property
    def audio_preview_cache_summary(self) -> str:
        stats: AudioPreviewCacheStats = audio_preview_cache_stats(self.cache_root)
        return f"{_format_bytes(stats.total_bytes)} / {_format_bytes(stats.max_bytes)}"

    @property
    def audio_preview_clock_url(self) -> str:
        if self._project is None:
            return ""
        video = self._project.get("video")
        if not is_string_object_mapping(video):
            return ""
        raw_path = video.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return ""
        video_path = Path(raw_path)
        return QUrl.fromLocalFile(video_path).toString() if video_path.is_file() else ""

    def reset_cache(self) -> None:
        self.stop_preview()
        self._cache_request += 1
        self._generation += 1
        self._cache_paths.clear()
        self._preparing = False
        self._force_cache_rebuild = False
        future = self._cache_future
        if future is not None and not future.done():
            future.cancel()
        self._cache_future = None
        self.cacheChanged.emit()
        self.notify_preview(structure_changed=True)

    def prepare_preview(self, *, ffmpeg_available: bool = True) -> None:
        project = self._project
        if project is None:
            return
        future = self._cache_future
        if future is not None and not future.done():
            return
        force_rebuild = self._force_cache_rebuild
        entries = audio_preview_cache_entries(project, self.cache_root)
        cached_paths = {} if force_rebuild else cached_audio_preview_paths(entries)
        protected_paths = [Path(path) for path in cached_paths.values()]
        required_ids = {entry.channel_id for entry in entries}
        if cached_paths != self._cache_paths:
            self._cache_paths.clear()
            self._cache_paths.update(cached_paths)
            self.notify_preview(structure_changed=True)
            self.projectDataChanged.emit()
        if not force_rebuild and required_ids.issubset(self._cache_paths):
            if self._preparing:
                self._preparing = False
                self.cacheChanged.emit()
            return
        if not ffmpeg_available:
            self._preparing = False
            self.cacheChanged.emit()
            self.statusChanged.emit("音声プレビューの準備にはFFmpegが必要です", "SETUP")
            return
        self._cache_request += 1
        request_id = self._cache_request
        project_snapshot = deepcopy(project)
        cache_root = self.cache_root
        self._preparing = True
        self.cacheChanged.emit()
        self.projectDataChanged.emit()
        future = self._cache_executor.submit(
            self._prepare_cache_after_clear if force_rebuild else self._prepare_cache,
            project_snapshot,
            cache_root,
            protected_paths=protected_paths,
        )
        self._cache_future = future
        self._force_cache_rebuild = False

        def report_completion(done: Future[AudioPreviewCacheResult]) -> None:
            try:
                result = done.result()
            except Exception as error:
                result = AudioPreviewCacheResult({}, (str(error),))
            self.cacheCompleted.emit(request_id, result)

        future.add_done_callback(report_completion)

    def _prepare_cache_after_clear(
        self,
        project: Mapping[str, object],
        cache_root: Path,
        *,
        protected_paths: list[Path],
    ) -> AudioPreviewCacheResult:
        # 旧FFmpeg処理がクリア後に出力しても、次の処理の開始直前に取り除く。
        self._clear_cache(cache_root)
        return self._prepare_cache(project, cache_root, protected_paths=protected_paths)

    @Slot(int, object)
    def _apply_audio_preview_cache(
        self,
        request_id: int,
        result: AudioPreviewCacheResult,
    ) -> None:
        if request_id != self._cache_request or self._project is None:
            return
        self._cache_future = None
        self._cache_paths.clear()
        self._cache_paths.update(
            {channel_id: path for channel_id, path in result.paths.items() if Path(path).is_file()}
        )
        self._preparing = False
        self.cacheChanged.emit()
        self.notify_preview(structure_changed=True)
        self.projectDataChanged.emit()
        if result.errors:
            self.statusChanged.emit(
                "音声プレビューの準備に失敗しました: " + "; ".join(result.errors),
                "CHECK",
            )
        else:
            self.statusChanged.emit("音声プレビューの準備が完了しました", "MIX")

    def apply_audio_preview_cache(
        self,
        request_id: int,
        result: AudioPreviewCacheResult,
    ) -> None:
        """Apply a completion result for legacy facade callers."""

        self._apply_audio_preview_cache(request_id, result)

    def clear_cache(self) -> None:
        self.stop_preview()
        self._cache_request += 1
        self._generation += 1
        self._cache_paths.clear()
        self._preparing = False
        self._force_cache_rebuild = True
        future = self._cache_future
        if future is not None and not future.done():
            future.cancel()
        self._cache_future = None
        self._clear_cache(self.cache_root)
        self.cacheChanged.emit()
        self.projectDataChanged.emit()
        self.notify_preview(structure_changed=True)
        self.statusChanged.emit("音声プレビューキャッシュをクリアしました", "CHECK")

    @property
    def mixer_channels(self) -> list[dict[str, object]]:
        if self._project is None:
            return []
        return [self.channel_view(channel) for channel in self._channels()]

    def enabled_channel_ids(self) -> set[str]:
        if self._project is None:
            return set()
        return {
            str(channel.get("id", ""))
            for channel in self._channels()
            if isinstance(channel, dict) and bool(channel.get("enabled")) and str(channel.get("id", "")).strip()
        }

    @property
    def preview_complete(self) -> bool:
        enabled_ids = self.enabled_channel_ids()
        if not enabled_ids:
            return False
        return all(
            bool(path := self._cache_paths.get(channel_id)) and Path(str(path)).is_file() for channel_id in enabled_ids
        )

    @property
    def intentional_silence(self) -> bool:
        return bool(self.mixer_channels) and not self.enabled_channel_ids()

    def channel_view(self, channel: Mapping[str, object]) -> dict[str, object]:
        project = self._project or {}
        view = deepcopy(dict(channel))
        is_external = view.get("kind") == "external"
        if is_external:
            view["preview_object_id"] = str(view.get("id", ""))
        else:
            view["preview_object_id"] = f"video:{view.get('selector', '')}"
        cache_path = self._cache_paths.get(str(view.get("id", "")), "")
        view["preview_url"] = (
            QUrl.fromLocalFile(cache_path).toString() if cache_path and Path(cache_path).is_file() else ""
        )
        view["preview_audio_track_index"] = 0
        transcription = project.get("transcription")
        view["preview_offset_seconds"] = (
            coerce_float(transcription.get("offset_seconds", 0.0))
            if is_external and is_string_object_mapping(transcription)
            else 0.0
        )
        return view

    def preview_state(
        self,
    ) -> tuple[list[tuple[dict[str, object], dict[str, object]]], dict[str, float]]:
        if self._project is None:
            self._gains.clear()
            return [], {}
        audio_mix = self._audio_mix()
        available = [
            (channel, self.channel_view(channel))
            for channel in self._channels()
            if bool(channel.get("enabled"))
        ]
        available = [item for item in available if item[1]["preview_url"]]
        active_ids = {str(channel.get("id", "")) for channel in active_audio_mix_channels(audio_mix)}
        gains = {
            str(view.get("id", "")): (
                min(
                    MAX_VOLUME_PERCENT / 100.0,
                    max(0.0, coerce_float(channel.get("volume_percent", 100.0))) / 100.0 * AUDIO_MIX_MASTER_GAIN,
                )
                if str(view.get("id", "")) in active_ids
                else 0.0
            )
            for channel, view in available
        }
        self._gains.clear()
        self._gains.update(gains)
        self._mixer.set_channels(
            gains,
            {str(view.get("id", "")): coerce_float(view.get("preview_offset_seconds", 0.0)) for _channel, view in available},
        )
        if any(channel_id not in gains or gains.get(channel_id, 0.0) <= 0.0 for channel_id in self._levels):
            self._level_timer.start()
        return available, gains

    def notify_preview(self, *, structure_changed: bool) -> None:
        self.preview_state()
        if structure_changed:
            self.previewChannelsChanged.emit()
        self.previewGainsChanged.emit()

    @property
    def preview_channels(self) -> list[dict[str, object]]:
        available, gains = self.preview_state()
        channels: list[dict[str, object]] = []
        for _channel, view in available:
            channel_id = str(view.get("id", ""))
            view["preview_volume"] = gains.get(channel_id, 0.0)
            view["preview_buffer_output"] = self.preview_output(channel_id)
            channels.append(view)
        return channels

    @property
    def preview_gains(self) -> dict[str, float]:
        _available, gains = self.preview_state()
        return dict(gains)

    def preview_output(self, channel_id: str) -> QAudioBufferOutput:
        output = self._outputs.get(channel_id)
        if output is None:
            output = QAudioBufferOutput(self._mixer.audio_format, self)
            buffer_signal: object = output.audioBufferReceived
            if not isinstance(buffer_signal, AudioBufferSignal):
                raise RuntimeError("音声バッファの通知を接続できません")
            buffer_signal.connect(lambda buffer: self.receive_preview_buffer(channel_id, buffer))
            self._outputs[channel_id] = output
        return output

    @staticmethod
    def audio_buffer_peak(buffer: QAudioBuffer) -> float:
        if not buffer.isValid() or buffer.byteCount() <= 0:
            return 0.0
        raw = bytes(buffer.constData())
        sample_format = buffer.format().sampleFormat()
        if sample_format == QAudioFormat.SampleFormat.UInt8:
            values = (abs(value - 128) / 128.0 for value in raw)
        elif sample_format == QAudioFormat.SampleFormat.Int16:
            samples = array("h")
            samples.frombytes(raw[: len(raw) - len(raw) % 2])
            stride = max(1, len(samples) // 4096)
            values = (abs(value) / 32768.0 for value in samples[::stride])
        elif sample_format == QAudioFormat.SampleFormat.Int32:
            samples = array("i")
            samples.frombytes(raw[: len(raw) - len(raw) % 4])
            stride = max(1, len(samples) // 4096)
            values = (abs(value) / 2147483648.0 for value in samples[::stride])
        elif sample_format == QAudioFormat.SampleFormat.Float:
            float_samples = array("f")
            float_samples.frombytes(raw[: len(raw) - len(raw) % 4])
            stride = max(1, len(float_samples) // 4096)
            values = (abs(float(value)) for value in float_samples[::stride] if math.isfinite(float(value)))
        else:
            return 0.0
        return min(1.0, max(values, default=0.0))

    def receive_preview_buffer(self, channel_id: str, buffer: QAudioBuffer) -> None:
        decoded_peak = self._mixer.push_buffer(channel_id, buffer)
        gain = self._gains.get(channel_id, 0.0)
        peak = decoded_peak * gain
        self._pending_levels[channel_id] = max(
            peak,
            self._pending_levels.get(channel_id, 0.0),
        )
        if not self._level_timer.isActive():
            self._level_timer.start()

    @Slot()
    def publish_levels(self) -> None:
        channel_ids = set(self._levels) | set(self._pending_levels) | set(self._gains)
        levels: dict[str, float] = {}
        for channel_id in channel_ids:
            target = self._pending_levels.pop(channel_id, 0.0) if channel_id in self._gains else 0.0
            level = max(target, self._levels.get(channel_id, 0.0) * 0.68)
            levels[channel_id] = 0.0 if level < 0.002 else round(min(1.0, level), 4)
        if levels != self._levels:
            self._levels.clear()
            self._levels.update(levels)
            self.levelsChanged.emit()
        if not self._pending_levels and not any(levels.values()):
            self._level_timer.stop()

    @Slot(float, float)
    def _update_master_metrics(self, level: float, reduction_db: float) -> None:
        next_level = round(max(0.0, min(1.0, float(level))), 4)
        next_reduction = round(max(0.0, float(reduction_db)), 2)
        if next_level == self._master_level and next_reduction == self._limiter_reduction_db:
            return
        self._master_level = next_level
        self._limiter_reduction_db = next_reduction
        self.masterMetricsChanged.emit()

    def start_preview(self, position_milliseconds: int) -> None:
        self.preview_state()
        self._mixer.play(position_milliseconds)

    def pause_preview(self) -> None:
        self._mixer.stop()

    def seek_preview(self, position_milliseconds: int, playing: bool) -> None:
        self.preview_state()
        self._mixer.seek(position_milliseconds, playing)

    def stop_preview(self) -> None:
        self._mixer.stop()

    def shutdown(self) -> None:
        self.stop_preview()
        self._level_timer.stop()
        future = self._cache_future
        if future is not None and not future.done():
            future.cancel()
        self._cache_future = None
        self._cache_executor.shutdown(wait=True, cancel_futures=True)


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    return f"{size:.1f} {units[index]}"


__all__ = ["AudioPreviewController"]
