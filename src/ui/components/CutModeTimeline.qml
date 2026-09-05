pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    required property var backend
    required property var player
    property var timeline: root.backend.cutTimeline
    property real selectionStartMs: 0
    property real selectionEndMs: 0
    property string selectedCutId: ""
    property color panelColor: "#121715"
    property color raisedColor: "#19201D"
    property color borderColor: "#2A3530"
    property color textColor: "#F4F1E8"
    property color mutedColor: "#8E9B94"
    property color accentColor: "#C8FF3D"
    property color warningColor: "#FFB547"
    property color cutColor: "#FF6B5F"
    readonly property real sourceDurationMs: Math.max(1, Number(root.timeline.sourceDuration || 0) * 1000)
    signal rangeSelected(real sourceStartMs, real sourceEndMs)
    signal cutSelected(string cutId, real sourceStartMs, real sourceEndMs)
    signal seekRequested(real sourcePositionMs)

    function xToMilliseconds(positionX) {
        return Math.max(0, Math.min(root.sourceDurationMs, positionX / Math.max(1, cutTrack.width) * root.sourceDurationMs))
    }

    function millisecondsToX(milliseconds) {
        return Math.max(0, Math.min(cutTrack.width, Number(milliseconds || 0) / root.sourceDurationMs * cutTrack.width))
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 5

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: "素材タイムライン"
                color: root.textColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 11
                font.weight: Font.DemiBold
            }
            Item { Layout.fillWidth: true }
            Text {
                text: "素材 " + Number(root.timeline.sourceDuration || 0).toFixed(2)
                    + "秒  →  出力 " + Number(root.timeline.outputDuration || 0).toFixed(2) + "秒"
                color: root.timeline.hasCuts ? root.accentColor : root.mutedColor
                font.family: "Cascadia Mono"
                font.pixelSize: 9
            }
        }

        Rectangle {
            id: cutTrack
            objectName: "workspaceCutTimeline"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 62
            radius: 7
            color: root.raisedColor
            border.color: root.borderColor
            clip: true

            Repeater {
                model: root.timeline.keepRanges || []
                delegate: Rectangle {
                    id: keepRange
                    required property var modelData
                    x: root.millisecondsToX(Number(keepRange.modelData.source_start) * 1000)
                    y: 22
                    width: Math.max(1, root.millisecondsToX(Number(keepRange.modelData.source_end) * 1000) - x)
                    height: cutTrack.height - 30
                    color: root.accentColor
                    opacity: 0.22
                }
            }

            Repeater {
                model: root.timeline.cuts || []
                delegate: Rectangle {
                    id: cutRange
                    required property var modelData
                    z: 4
                    objectName: "workspaceCutRange-" + String(cutRange.modelData.id)
                    x: root.millisecondsToX(Number(cutRange.modelData.source_start) * 1000)
                    y: 22
                    width: Math.max(3, root.millisecondsToX(Number(cutRange.modelData.source_end) * 1000) - x)
                    height: cutTrack.height - 30
                    radius: 3
                    color: root.cutColor
                    opacity: root.selectedCutId === String(cutRange.modelData.id) ? 0.9 : 0.62
                    border.color: root.selectedCutId === String(cutRange.modelData.id) ? root.textColor : root.cutColor
                    border.width: root.selectedCutId === String(cutRange.modelData.id) ? 2 : 1

                    Text {
                        anchors.centerIn: parent
                        visible: parent.width > 34
                        text: "カット"
                        color: "#17100F"
                        font.family: "Yu Gothic UI"
                        font.pixelSize: 8
                        font.weight: Font.Bold
                    }

                    MouseArea {
                        anchors.fill: parent
                        onClicked: {
                            root.cutSelected(
                                String(cutRange.modelData.id),
                                Number(cutRange.modelData.source_start) * 1000,
                                Number(cutRange.modelData.source_end) * 1000
                            )
                        }
                    }
                }
            }

            Rectangle {
                visible: root.selectionEndMs > root.selectionStartMs
                x: root.millisecondsToX(root.selectionStartMs)
                y: 18
                width: Math.max(2, root.millisecondsToX(root.selectionEndMs) - x)
                height: cutTrack.height - 22
                color: "transparent"
                border.color: root.warningColor
                border.width: 2
                radius: 4
            }

            Repeater {
                model: 6
                delegate: Item {
                    id: tick
                    required property int index
                    x: index * cutTrack.width / 5
                    width: 1
                    height: cutTrack.height
                    Rectangle { y: 18; width: 1; height: parent.height - 18; color: root.borderColor }
                    Text {
                        x: tick.index === 5 ? -34 : 3
                        y: 3
                        width: 32
                        text: (Number(root.timeline.sourceDuration || 0) * tick.index / 5).toFixed(1)
                        color: root.mutedColor
                        font.family: "Cascadia Mono"
                        font.pixelSize: 8
                        horizontalAlignment: tick.index === 5 ? Text.AlignRight : Text.AlignLeft
                    }
                }
            }

            Rectangle {
                z: 5
                x: root.millisecondsToX(root.player ? root.player.position : 0)
                width: 2
                height: cutTrack.height
                color: root.accentColor
            }

            MouseArea {
                id: rangeMouse
                z: 3
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton
                property real pressedX: 0
                onPressed: function(mouse) {
                    pressedX = mouse.x
                    var at = root.xToMilliseconds(mouse.x)
                    root.rangeSelected(at, at)
                }
                onPositionChanged: function(mouse) {
                    if (!pressed)
                        return
                    var start = root.xToMilliseconds(Math.min(pressedX, mouse.x))
                    var end = root.xToMilliseconds(Math.max(pressedX, mouse.x))
                    root.rangeSelected(start, end)
                }
                onReleased: function(mouse) {
                    if (Math.abs(mouse.x - pressedX) < 4) {
                        var at = root.xToMilliseconds(mouse.x)
                        root.rangeSelected(at, at)
                        root.seekRequested(at)
                        return
                    }
                    root.rangeSelected(
                        root.xToMilliseconds(Math.min(pressedX, mouse.x)),
                        root.xToMilliseconds(Math.max(pressedX, mouse.x))
                    )
                }
            }
        }
    }
}
