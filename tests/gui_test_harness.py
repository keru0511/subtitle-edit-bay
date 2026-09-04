from __future__ import annotations

import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, TypeVar

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    Slot,
    QTimer,
    Qt,
    QtMsgType,
    QUrl,
    qInstallMessageHandler,
)
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest


TObject = TypeVar("TObject", bound=QObject)


@dataclass(frozen=True)
class DurationSummary:
    count: int
    p50_ms: float
    p95_ms: float
    max_ms: float

    def as_dict(self) -> dict[str, int | float]:
        return {
            "count": self.count,
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
            "max_ms": self.max_ms,
        }


def summarize_durations_ms(values: Iterable[float]) -> DurationSummary:
    """Summarize durations with deterministic nearest-rank percentiles."""

    ordered = sorted(max(0.0, float(value)) for value in values)
    if not ordered:
        return DurationSummary(count=0, p50_ms=0.0, p95_ms=0.0, max_ms=0.0)

    def percentile(percent: float) -> float:
        rank = max(1, math.ceil(len(ordered) * percent))
        return ordered[min(len(ordered), rank) - 1]

    return DurationSummary(
        count=len(ordered),
        p50_ms=round(percentile(0.50), 3),
        p95_ms=round(percentile(0.95), 3),
        max_ms=round(ordered[-1], 3),
    )


class EventLoopLatencyProbe(QObject):
    """Measure how late a precise Qt timer runs while GUI work is in flight."""

    def __init__(self, *, interval_ms: int = 10, parent: QObject | None = None) -> None:
        if interval_ms <= 0:
            raise ValueError("interval_ms must be positive")
        super().__init__(parent)
        self.interval_ms = interval_ms
        self.samples_ms: list[float] = []
        self._expected_at = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._record_tick)

    def start(self) -> None:
        self.samples_ms.clear()
        self._expected_at = time.monotonic() + self.interval_ms / 1_000
        self._timer.start()

    def stop(self) -> DurationSummary:
        self._timer.stop()
        return summarize_durations_ms(self.samples_ms)

    def _record_tick(self) -> None:
        now = time.monotonic()
        self.samples_ms.append(max(0.0, (now - self._expected_at) * 1_000))
        self._expected_at = now + self.interval_ms / 1_000


def _enum_name(value: object) -> str:
    name = getattr(value, "name", None)
    return str(name if name is not None else value).rsplit(".", 1)[-1]


class MediaPlayerSignalProbe(QObject):
    """Collect observable source, loading, playback, and position transitions."""

    def __init__(self, player: QObject) -> None:
        # A QObject receiver keeps cross-thread multimedia signals queued onto
        # the player's thread.  A plain Python callback can otherwise be
        # invoked directly by the Windows decoding thread and intermittently
        # crash while wrapping or releasing QVideoFrame.
        super().__init__(player)
        self.player = player
        self.video_sink: QObject | None = None
        player.sourceChanged.connect(self._source_changed)
        player.mediaStatusChanged.connect(self._media_status_changed)
        player.playbackStateChanged.connect(self._playback_state_changed)
        player.positionChanged.connect(self._position_changed)
        self.refresh_video_sink()
        self.reset()

    def refresh_video_sink(self) -> None:
        """Follow the player's current output after QML reassigns videoOutput."""

        video_sink = getattr(self.player, "videoSink", None)
        next_sink = video_sink() if callable(video_sink) else None
        if next_sink is self.video_sink:
            return
        if self.video_sink is not None:
            try:
                self.video_sink.videoFrameChanged.disconnect(self._video_frame_changed)
            except (AttributeError, RuntimeError, TypeError):
                pass
        self.video_sink = next_sink
        if self.video_sink is not None:
            self.video_sink.videoFrameChanged.connect(self._video_frame_changed)

    def reset(self) -> None:
        self._started_at = time.perf_counter()
        self.sources: list[str] = []
        self.media_statuses: list[str] = []
        self.playback_states: list[str] = []
        self.positions_ms: list[int] = []
        self.video_frames = 0
        self.first_video_frame_ms: float | None = None

    def _source_changed(self, source: object) -> None:
        to_string = getattr(source, "toString", None)
        self.sources.append(str(to_string() if callable(to_string) else source))

    def _media_status_changed(self, status: object) -> None:
        self.media_statuses.append(_enum_name(status))

    def _playback_state_changed(self, state: object) -> None:
        self.playback_states.append(_enum_name(state))

    def _position_changed(self, position: int) -> None:
        self.positions_ms.append(int(position))

    @Slot()
    def _video_frame_changed(self) -> None:
        self.video_frames += 1
        if self.first_video_frame_ms is None:
            self.first_video_frame_ms = round((time.perf_counter() - self._started_at) * 1_000, 3)

    def as_dict(self) -> dict[str, object]:
        return {
            "source_changes": len(self.sources),
            "sources": list(self.sources),
            "media_status_transitions": len(self.media_statuses),
            "loading_transitions": self.media_statuses.count("LoadingMedia"),
            "playback_state_transitions": len(self.playback_states),
            "play_starts": self.playback_states.count("PlayingState"),
            "pauses": self.playback_states.count("PausedState"),
            "stops": self.playback_states.count("StoppedState"),
            "position_events": len(self.positions_ms),
            "video_frames": self.video_frames,
            "first_video_frame_ms": self.first_video_frame_ms,
        }


@dataclass(frozen=True)
class QmlRuntimeMessage:
    message_type: QtMsgType
    category: str
    file: str
    line: int
    text: str

    def render(self) -> str:
        severity = self.message_type.name.removeprefix("Qt").removesuffix("Msg")
        location = self.file
        if self.line > 0:
            location = f"{location}:{self.line}" if location else f"line {self.line}"
        prefix = f"[{severity}]"
        if self.category:
            prefix += f" [{self.category}]"
        if location:
            prefix += f" {location}"
        return f"{prefix}: {self.text}"


@dataclass(frozen=True)
class AllowedQmlMessage:
    pattern: str
    reason: str


class QmlMessageCapture:
    def __init__(self) -> None:
        self.messages: list[QmlRuntimeMessage] = []
        self._previous_handler: object | None = None
        self._started = False
        self._handler = self._capture

    def start(self) -> None:
        if self._started:
            return
        self._previous_handler = qInstallMessageHandler(self._handler)
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        qInstallMessageHandler(self._previous_handler)
        self._previous_handler = None
        self._started = False

    def _capture(self, message_type: QtMsgType, context: object, text: str) -> None:
        self.messages.append(
            QmlRuntimeMessage(
                message_type=message_type,
                category=str(getattr(context, "category", "") or ""),
                file=str(getattr(context, "file", "") or ""),
                line=int(getattr(context, "line", 0) or 0),
                text=str(text),
            )
        )
        if callable(self._previous_handler):
            self._previous_handler(message_type, context, text)


class GuiTestHarness:
    """Owns QML engines, event-loop waits, interactions, and runtime diagnostics."""

    def __init__(
        self,
        application: QCoreApplication,
        *,
        backend: QObject | None = None,
        qml_roots: tuple[Path, ...] = (),
        qml_message_allowlist: tuple[AllowedQmlMessage, ...] = (),
    ) -> None:
        self.application = application
        self.backend = backend
        self.engines: list[QQmlApplicationEngine] = []
        self._qml_roots = {path.resolve() for path in qml_roots}
        self._validate_allowlist(qml_message_allowlist)
        self._qml_message_allowlist = qml_message_allowlist
        self._message_capture = QmlMessageCapture()
        self._message_capture.start()
        self._closed = False

    @property
    def messages(self) -> list[QmlRuntimeMessage]:
        return self._message_capture.messages

    def process_events(self, *, deferred_deletes: bool = False) -> None:
        self.application.processEvents()
        if deferred_deletes:
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.application.processEvents()

    def wait_until(
        self,
        predicate: Callable[[], bool],
        *,
        description: str,
        timeout_ms: int = 1_000,
        interval_ms: int = 10,
    ) -> None:
        if timeout_ms < 0:
            raise ValueError("timeout_ms must not be negative")
        if interval_ms <= 0:
            raise ValueError("interval_ms must be positive")

        deadline = time.monotonic() + timeout_ms / 1_000
        last_error: Exception | None = None
        while True:
            self.process_events()
            try:
                if predicate():
                    return
                last_error = None
            except Exception as error:  # Keep polling while queued QML work settles.
                last_error = error

            remaining_ms = round((deadline - time.monotonic()) * 1_000)
            if remaining_ms <= 0:
                break
            QTest.qWait(min(interval_ms, remaining_ms))

        details = [f"Timed out waiting for {description} after {timeout_ms} ms."]
        if last_error is not None:
            details.append(f"Last predicate error: {last_error!r}")
        diagnostics = self.format_messages()
        if diagnostics:
            details.append(f"Captured Qt/QML messages:\n{diagnostics}")
        raise AssertionError("\n".join(details))

    def load_qml(
        self,
        qml_path: Path,
        *,
        width: int = 1_220,
        height: int = 760,
    ) -> tuple[QQmlApplicationEngine, QObject]:
        if self._closed:
            raise RuntimeError("GUI test harness is already closed")
        qml_path = qml_path.resolve()
        self._qml_roots.add(qml_path.parent)
        engine = QQmlApplicationEngine()
        self.engines.append(engine)
        if self.backend is not None:
            engine.rootContext().setContextProperty("backend", self.backend)
        engine.load(QUrl.fromLocalFile(str(qml_path)))
        self.process_events()

        roots = engine.rootObjects()
        if not roots:
            diagnostics = self.format_messages()
            suffix = f"\nCaptured Qt/QML messages:\n{diagnostics}" if diagnostics else ""
            raise AssertionError(f"QML did not create a root object: {qml_path}{suffix}")

        window = roots[0]
        self.resize(window, width, height)
        return engine, window

    def find_object(
        self,
        root: QObject,
        name: str,
        object_type: type[TObject] = QObject,
    ) -> TObject:
        item = root.findChild(object_type, name)
        if item is None:
            available = sorted(child.objectName() for child in root.findChildren(QObject) if child.objectName())
            available_text = ", ".join(available[:30]) or "(none)"
            diagnostics = self.format_messages()
            suffix = f"\nCaptured Qt/QML messages:\n{diagnostics}" if diagnostics else ""
            raise AssertionError(
                f"Could not find {object_type.__name__} objectName={name!r}. "
                f"Available object names: {available_text}{suffix}"
            )
        return item

    def find_item(self, root: QObject, name: str) -> QQuickItem:
        return self.find_object(root, name, QQuickItem)

    def find_visual_item(self, root: QQuickItem, name: str) -> QQuickItem:
        pending = [root]
        available: list[str] = []
        while pending:
            item = pending.pop()
            if item.objectName():
                available.append(item.objectName())
            if item.objectName() == name:
                return item
            pending.extend(reversed(item.childItems()))
        available_text = ", ".join(sorted(available)[:30]) or "(none)"
        raise AssertionError(
            f"Could not find visual item objectName={name!r}. Available visual object names: {available_text}"
        )

    def visual_items(self, root: QObject) -> list[QQuickItem]:
        """Return the live visual tree, including dynamically-created delegates."""

        if isinstance(root, QQuickItem):
            pending = [root]
        else:
            content_item = getattr(root, "contentItem", None)
            pending = [content_item()] if callable(content_item) else []
        found: list[QQuickItem] = []
        seen: set[int] = set()
        while pending:
            item = pending.pop()
            if not isinstance(item, QQuickItem) or id(item) in seen:
                continue
            seen.add(id(item))
            found.append(item)
            pending.extend(item.childItems())
        return found

    def count_visual_items(self, root: QObject, *, object_name_prefix: str) -> int:
        return sum(item.objectName().startswith(object_name_prefix) for item in self.visual_items(root))

    @staticmethod
    def _has_qml_properties(item: QQuickItem, property_names: Iterable[str]) -> bool:
        meta_object = item.metaObject()
        return all(meta_object.indexOfProperty(name) >= 0 for name in property_names)

    def visual_items_with_properties(
        self,
        root: QObject,
        *property_names: str,
    ) -> list[QQuickItem]:
        """Find semantic QML items without relying on revision-specific object names."""

        return [item for item in self.visual_items(root) if self._has_qml_properties(item, property_names)]

    def find_visual_item_by_properties(
        self,
        root: QObject,
        expected: Mapping[str, object],
        *,
        required_properties: Iterable[str] = (),
    ) -> QQuickItem:
        property_names = (*required_properties, *expected)
        for item in self.visual_items(root):
            if not self._has_qml_properties(item, property_names):
                continue
            if all(item.property(name) == value for name, value in expected.items()):
                return item
        formatted = ", ".join(f"{name}={value!r}" for name, value in expected.items())
        raise AssertionError(f"Could not find visual item with QML properties: {formatted}")

    def emit_signal(self, item: QObject, signal_name: str, *arguments: object) -> None:
        """Emit a control signal so its real QML handler performs the operation."""

        signal = getattr(item, signal_name, None)
        emit = getattr(signal, "emit", None)
        if not callable(emit):
            raise AssertionError(f"Could not emit signal {signal_name!r} on {item.objectName() or type(item).__name__}")
        emit(*arguments)
        self.process_events()

    def wait(self, milliseconds: int) -> None:
        if milliseconds < 0:
            raise ValueError("milliseconds must not be negative")
        QTest.qWait(milliseconds)
        self.process_events()

    def click(self, window: QObject, item: QQuickItem) -> None:
        self.click_at(window, item, item.width() / 2, item.height() / 2)

    def click_at(self, window: QObject, item: QQuickItem, x: float, y: float) -> None:
        name = item.objectName() or type(item).__name__
        if not item.isVisible():
            raise AssertionError(f"Cannot click hidden item: {name}")
        if item.property("enabled") is False:
            raise AssertionError(f"Cannot click disabled item: {name}")
        if item.width() <= 0 or item.height() <= 0:
            raise AssertionError(f"Cannot click zero-sized item: {name} ({item.width()} x {item.height()})")
        if not 0 <= x <= item.width() or not 0 <= y <= item.height():
            raise AssertionError(f"Click position is outside {name}: ({x}, {y})")
        position = item.mapToScene(QPointF(x, y))
        QTest.mouseClick(
            window,
            Qt.MouseButton.LeftButton,
            pos=QPoint(round(position.x()), round(position.y())),
        )
        self.process_events()

    def key_click(
        self,
        window: QObject,
        key: Qt.Key,
        modifier: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
    ) -> None:
        QTest.keyClick(window, key, modifier)
        self.process_events()

    def set_property(self, item: QObject, name: str, value: object) -> None:
        item.setProperty(name, value)
        self.process_events()

    def resize(self, window: QObject, width: int, height: int) -> None:
        resize = getattr(window, "resize", None)
        if callable(resize):
            resize(width, height)
        else:
            window.setProperty("width", width)
            window.setProperty("height", height)
        self.wait_until(
            lambda: (
                round(float(window.property("width"))) == width and round(float(window.property("height"))) == height
            ),
            description=f"window resize to {width}x{height}",
        )

    def assert_item_within(
        self,
        container: QQuickItem,
        item: QQuickItem,
        *,
        tolerance: float = 1.0,
    ) -> None:
        name = item.objectName() or type(item).__name__
        if not item.isVisible():
            raise AssertionError(f"Expected visible item: {name}")
        top_left = item.mapToItem(container, QPointF(0, 0))
        bottom_right = item.mapToItem(container, QPointF(item.width(), item.height()))
        if (
            top_left.x() < -tolerance
            or top_left.y() < -tolerance
            or bottom_right.x() > container.width() + tolerance
            or bottom_right.y() > container.height() + tolerance
        ):
            raise AssertionError(
                f"{name} bounds ({top_left.x():.1f}, {top_left.y():.1f})-"
                f"({bottom_right.x():.1f}, {bottom_right.y():.1f}) exceed container "
                f"{container.objectName() or type(container).__name__} "
                f"({container.width():.1f} x {container.height():.1f})"
            )

    def assert_no_messages_containing(
        self,
        *patterns: str,
        since: int = 0,
    ) -> None:
        matches = [
            message
            for message in self.messages[since:]
            if any(re.search(pattern, message.text, re.IGNORECASE) for pattern in patterns)
        ]
        if matches:
            rendered = "\n".join(message.render() for message in matches)
            raise AssertionError(f"Unexpected Qt/QML messages:\n{rendered}")

    def assert_no_unexpected_qml_messages(
        self,
        *,
        since: int = 0,
        allowlist: tuple[AllowedQmlMessage, ...] = (),
    ) -> None:
        self._validate_allowlist(allowlist)

        unexpected = [
            message
            for message in self.messages[since:]
            if self._is_application_qml_warning(message)
            and not any(re.search(rule.pattern, message.text) for rule in allowlist)
        ]
        if unexpected:
            rendered = "\n".join(message.render() for message in unexpected)
            raise AssertionError(f"Unexpected application QML messages:\n{rendered}")

    @staticmethod
    def _validate_allowlist(allowlist: tuple[AllowedQmlMessage, ...]) -> None:
        for allowed in allowlist:
            if not allowed.reason.strip():
                raise ValueError(f"Allowlisted QML message needs a reason: {allowed.pattern!r}")
            re.compile(allowed.pattern)

    def _is_application_qml_warning(self, message: QmlRuntimeMessage) -> bool:
        if message.message_type not in {
            QtMsgType.QtWarningMsg,
            QtMsgType.QtCriticalMsg,
            QtMsgType.QtFatalMsg,
        }:
            return False

        candidates = [message.file, message.text]
        for candidate in candidates:
            if not candidate:
                continue
            local = QUrl(candidate).toLocalFile() if candidate.startswith("file:") else candidate
            try:
                path = Path(local).resolve()
            except (OSError, ValueError):
                path = None
            if path is not None and any(path == root or root in path.parents for root in self._qml_roots):
                return True
            normalized = candidate.replace("\\", "/")
            if any(str(root).replace("\\", "/") in normalized for root in self._qml_roots):
                return True
        return False

    def format_messages(self, *, since: int = 0, limit: int = 20) -> str:
        messages = self.messages[since:]
        if limit > 0:
            messages = messages[-limit:]
        return "\n".join(message.render() for message in messages)

    def cleanup(self) -> None:
        if self._closed:
            return
        try:
            for engine in reversed(self.engines):
                for root in engine.rootObjects():
                    close = getattr(root, "close", None)
                    if callable(close):
                        close()
                    root.deleteLater()
                engine.clearComponentCache()
                engine.deleteLater()
            self.engines.clear()
            self.process_events(deferred_deletes=True)
            self.assert_no_unexpected_qml_messages(
                allowlist=self._qml_message_allowlist,
            )
        finally:
            self._message_capture.stop()
            self._closed = True
