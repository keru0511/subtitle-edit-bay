from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from PySide6.QtCore import QMetaObject
from PySide6.QtGui import QGuiApplication
from shiboken6 import delete

from tests.gui_test_harness import AllowedQmlMessage, GuiTestHarness


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

    Component.onCompleted: ready = true

    Rectangle {
        objectName: "targetButton"
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
        self.addCleanup(self.workspace.cleanup)
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


if __name__ == "__main__":
    unittest.main()
