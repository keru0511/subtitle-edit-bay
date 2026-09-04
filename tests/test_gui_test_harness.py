from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from PySide6.QtCore import QMetaObject, QObject, Signal
from PySide6.QtGui import QGuiApplication
from shiboken6 import delete

from tests.gui_test_harness import (
    AllowedQmlMessage,
    EventLoopLatencyProbe,
    GuiTestHarness,
    MediaPlayerSignalProbe,
    summarize_durations_ms,
)
from tests.gui_performance_scenarios import (
    _main_preview_contract_passed,
    _playback_follow_contract_passed,
    _short_visual_update_contract_passed,
)


FIXTURE_QML = """\
import QtQuick
import QtQuick.Window

Window {
    id: root
    objectName: "fixtureWindow"
    visible: true
    width: 320
    height: 200
    property bool ready: false
    property int clickCount: 0
    signal submitted(int amount)
    onSubmitted: function(amount) { clickCount += amount }

    Component.onCompleted: ready = true

    Rectangle {
        objectName: "targetButton"
        property string semanticRole: "fixture-delegate"
        property int semanticIndex: 7
        x: 20
        y: 20
        width: 100
        height: 40
        color: "tomato"

        MouseArea {
            anchors.fill: parent
            onClicked: root.clickCount += 1
        }
    }

    function emitWarning() {
        console.warn("HARNESS_TEST_WARNING")
    }
}
"""


class FakeMediaPlayer(QObject):
    sourceChanged = Signal(object)
    mediaStatusChanged = Signal(object)
    playbackStateChanged = Signal(object)
    positionChanged = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.current_video_sink = FakeVideoSink()

    def videoSink(self) -> QObject:
        return self.current_video_sink


class FakeVideoSink(QObject):
    videoFrameChanged = Signal(object)


class GuiTestHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        application = QGuiApplication.instance()
        cls._owns_application = application is None
        cls.application = application or QGuiApplication([])

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._owns_application:
            cls.application.quit()
            delete(cls.application)
            cls.application = None

    def setUp(self) -> None:
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.qml_path = Path(self.workspace.name) / "HarnessFixture.qml"
        self.qml_path.write_text(FIXTURE_QML, encoding="utf-8")
        self.harness = GuiTestHarness(
            self.application,
            qml_roots=(self.qml_path.parent,),
            qml_message_allowlist=(
                AllowedQmlMessage(
                    pattern="HARNESS_TEST_WARNING",
                    reason="One test deliberately exercises runtime message capture.",
                ),
            ),
        )
        self.addCleanup(self.harness.cleanup)

    def test_load_find_click_resize_bounds_and_cleanup(self) -> None:
        _engine, window = self.harness.load_qml(self.qml_path)
        self.harness.wait_until(
            lambda: bool(window.property("ready")),
            description="fixture completion",
        )
        target = self.harness.find_item(window, "targetButton")

        self.harness.click(window, target)
        self.harness.wait_until(
            lambda: int(window.property("clickCount")) == 1,
            description="fixture click",
        )
        self.harness.resize(window, 480, 300)
        self.harness.assert_item_within(window.contentItem(), target)
        self.assertEqual(
            self.harness.count_visual_items(window, object_name_prefix="target"),
            1,
        )
        self.assertIs(
            self.harness.find_visual_item_by_properties(
                window,
                {"semanticIndex": 7},
                required_properties=("semanticRole",),
            ),
            target,
        )
        self.assertEqual(
            self.harness.visual_items_with_properties(window, "semanticRole", "semanticIndex"),
            [target],
        )
        self.harness.emit_signal(window, "submitted", 2)
        self.assertEqual(window.property("clickCount"), 3)

        self.harness.cleanup()
        self.harness.cleanup()
        self.assertEqual(self.harness.engines, [])

    def test_wait_diagnostics_and_reasoned_qml_allowlist(self) -> None:
        _engine, window = self.harness.load_qml(self.qml_path)
        message_start = len(self.harness.messages)
        self.assertTrue(QMetaObject.invokeMethod(window, "emitWarning"))
        self.harness.wait_until(
            lambda: any("HARNESS_TEST_WARNING" in message.text for message in self.harness.messages[message_start:]),
            description="fixture warning",
        )

        with self.assertRaises(AssertionError) as raised:
            self.harness.wait_until(
                lambda: False,
                description="a condition that never becomes true",
                timeout_ms=20,
            )
        self.assertIn("a condition that never becomes true", str(raised.exception))
        self.assertIn("HARNESS_TEST_WARNING", str(raised.exception))

        with self.assertRaisesRegex(AssertionError, "HARNESS_TEST_WARNING"):
            self.harness.assert_no_unexpected_qml_messages(since=message_start)
        self.harness.assert_no_unexpected_qml_messages(
            since=message_start,
            allowlist=(
                AllowedQmlMessage(
                    pattern="HARNESS_TEST_WARNING",
                    reason="The fixture deliberately exercises runtime message capture.",
                ),
            ),
        )

    def test_duration_summary_and_event_loop_probe(self) -> None:
        summary = summarize_durations_ms(range(1, 21))
        self.assertEqual(
            summary.as_dict(),
            {
                "count": 20,
                "p50_ms": 10.0,
                "p95_ms": 19.0,
                "max_ms": 20.0,
            },
        )

        probe = EventLoopLatencyProbe(interval_ms=5)
        probe.start()
        self.harness.wait(25)
        measured = probe.stop()

        self.assertGreater(measured.count, 0)
        self.assertGreaterEqual(measured.max_ms, 0.0)

    def test_media_player_probe_reports_observable_transitions(self) -> None:
        player = FakeMediaPlayer()
        probe = MediaPlayerSignalProbe(player)

        player.sourceChanged.emit("file:///fixture.mp4")
        player.mediaStatusChanged.emit("LoadingMedia")
        player.playbackStateChanged.emit("PlayingState")
        player.positionChanged.emit(125)
        player.playbackStateChanged.emit("PausedState")
        player.playbackStateChanged.emit("StoppedState")

        self.assertEqual(
            probe.as_dict(),
            {
                "source_changes": 1,
                "sources": ["file:///fixture.mp4"],
                "media_status_transitions": 1,
                "loading_transitions": 1,
                "playback_state_transitions": 3,
                "play_starts": 1,
                "pauses": 1,
                "stops": 1,
                "position_events": 1,
                "video_frames": 0,
                "first_video_frame_ms": None,
            },
        )

    def test_media_player_probe_follows_reassigned_video_sink(self) -> None:
        player = FakeMediaPlayer()
        original_sink = player.current_video_sink
        probe = MediaPlayerSignalProbe(player)
        replacement_sink = FakeVideoSink()
        player.current_video_sink = replacement_sink
        probe.refresh_video_sink()
        probe.reset()

        original_sink.videoFrameChanged.emit(object())
        replacement_sink.videoFrameChanged.emit(object())

        snapshot = probe.as_dict()
        self.assertEqual(snapshot["video_frames"], 1)
        self.assertIsNotNone(snapshot["first_video_frame_ms"])

    def test_main_playback_contract_requires_decoded_frames(self) -> None:
        result = {
            "advanced_playback_ms": 30_000,
            "requested_playback_ms": 30_000,
            "media": {
                "play_starts": 1,
                "video_frames": 0,
                "first_video_frame_ms": None,
            },
        }

        self.assertFalse(_main_preview_contract_passed(result))
        result["media"].update({"video_frames": 450, "first_video_frame_ms": 125.0})
        self.assertTrue(_main_preview_contract_passed(result))

    def test_playback_follow_contract_rejects_seek_only_selection(self) -> None:
        result = {
            "advanced_playback_ms": 15_000,
            "requested_playback_ms": 15_000,
            "initial_selected_index": 16,
            "final_selected_index": 16,
            "selected_indices": [16],
            "timeline_viewport_before_x": 0.0,
            "timeline_viewport_after_x": 0.0,
        }

        self.assertFalse(_playback_follow_contract_passed(result))
        result.update(
            {
                "final_selected_index": 20,
                "selected_indices": [16, 17, 18, 19, 20],
                "timeline_viewport_after_x": 520.0,
            }
        )
        self.assertTrue(_playback_follow_contract_passed(result))

    def test_short_visual_contract_rejects_position_reset(self) -> None:
        result = {
            "media": {
                "source_changes": 0,
                "loading_transitions": 0,
                "stops": 0,
                "play_starts": 0,
            },
            "playback_state_after": "PlayingState",
            "position_before_ms": 600,
            "position_after_ms": 0,
        }

        self.assertFalse(_short_visual_update_contract_passed(result))
        result["position_after_ms"] = 625
        self.assertTrue(_short_visual_update_contract_passed(result))


if __name__ == "__main__":
    unittest.main()
