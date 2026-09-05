pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    required property var backend
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
    signal selectionChanged(real sourceStartMs, real sourceEndMs)
    signal cutSelected(string cutId, real sourceStartMs, real sourceEndMs)

    function syncFields(force) {
        if (force || !cutStartField.activeFocus)
            cutStartField.text = (Number(root.selectionStartMs || 0) / 1000).toFixed(3)
        if (force || !cutEndField.activeFocus)
            cutEndField.text = (Number(root.selectionEndMs || 0) / 1000).toFixed(3)
    }

    function commitFields() {
        if (!cutStartField.acceptableInput || !cutEndField.acceptableInput)
            return false
        var start = Math.max(0, Number(cutStartField.text) * 1000)
        var end = Math.max(0, Number(cutEndField.text) * 1000)
        root.selectionChanged(Math.min(start, end), Math.max(start, end))
        return Math.abs(end - start) >= 50
    }

    onSelectionStartMsChanged: syncTimer.restart()
    onSelectionEndMsChanged: syncTimer.restart()
    Component.onCompleted: timelineSyncTimer.restart()

    Connections {
        target: root.backend
        function onCutTimelineChanged() {
            timelineSyncTimer.restart()
        }
    }

    Timer {
        id: timelineSyncTimer
        interval: 0
        repeat: false
        onTriggered: {
            if (root.selectedCutId) {
                var found = false
                for (var index = 0; index < root.timeline.cuts.length; ++index) {
                    if (String(root.timeline.cuts[index].id) === root.selectedCutId) {
                        found = true
                        root.cutSelected(
                            root.selectedCutId,
                            Number(root.timeline.cuts[index].source_start) * 1000,
                            Number(root.timeline.cuts[index].source_end) * 1000
                        )
                        break
                    }
                }
                if (!found)
                    root.cutSelected("", root.selectionStartMs, root.selectionEndMs)
            }
            root.syncFields(true)
        }
    }

    Timer {
        id: syncTimer
        interval: 0
        repeat: false
        onTriggered: root.syncFields()
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 7

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: "カット設定"
                color: root.textColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 13
                font.weight: Font.Bold
            }
            Item { Layout.fillWidth: true }
            Text {
                text: String((root.timeline.cuts || []).length) + "件"
                color: root.timeline.hasCuts ? root.cutColor : root.mutedColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 9
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 62
            radius: 7
            color: root.raisedColor
            border.color: root.borderColor
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 8
                spacing: 2
                Text { text: "編集後の長さ"; color: root.mutedColor; font.family: "Yu Gothic UI"; font.pixelSize: 9 }
                Text {
                    text: Number(root.timeline.outputDuration || 0).toFixed(3) + " 秒"
                    color: root.accentColor
                    font.family: "Cascadia Mono"
                    font.pixelSize: 16
                    font.weight: Font.Bold
                }
                Text {
                    text: "素材から " + Number(root.timeline.removedDuration || 0).toFixed(3) + " 秒を除外"
                    color: root.mutedColor
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 8
                }
            }
        }

        Text {
            text: root.selectedCutId ? "選択中のカット範囲" : "ドラッグで範囲を選択"
            color: root.textColor
            font.family: "Yu Gothic UI"
            font.pixelSize: 10
            font.weight: Font.DemiBold
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 5
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text { text: "開始（秒）"; color: root.mutedColor; font.family: "Yu Gothic UI"; font.pixelSize: 8 }
                TimeField {
                    id: cutStartField
                    objectName: "cutRangeStartField"
                    Layout.fillWidth: true
                    onEditingFinished: root.commitFields()
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text { text: "終了（秒）"; color: root.mutedColor; font.family: "Yu Gothic UI"; font.pixelSize: 8 }
                TimeField {
                    id: cutEndField
                    objectName: "cutRangeEndField"
                    Layout.fillWidth: true
                    onEditingFinished: root.commitFields()
                }
            }
        }

        SmallButton {
            objectName: "addCutButton"
            Layout.fillWidth: true
            text: root.selectedCutId ? "選択中のカットを変更" : "選択範囲をカット"
            enabled: !root.backend.running
                && Math.abs(Number(cutEndField.text) - Number(cutStartField.text)) >= 0.05
            onClicked: {
                if (!root.commitFields())
                    return
                var start = Math.min(Number(cutStartField.text), Number(cutEndField.text))
                var end = Math.max(Number(cutStartField.text), Number(cutEndField.text))
                if (root.selectedCutId)
                    root.backend.updateCutRange(root.selectedCutId, start, end)
                else
                    root.backend.addCut(start, end)
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 5
            SmallButton {
                objectName: "restoreCutRangeButton"
                Layout.fillWidth: true
                text: root.selectedCutId ? "このカットを復元" : "選択範囲を復元"
                enabled: !root.backend.running && Boolean(root.timeline.hasCuts)
                onClicked: {
                    if (root.selectedCutId)
                        root.backend.restoreCut(root.selectedCutId)
                    else if (root.commitFields()) {
                        var start = Math.min(Number(cutStartField.text), Number(cutEndField.text))
                        var end = Math.max(Number(cutStartField.text), Number(cutEndField.text))
                        root.backend.restoreRange(start, end)
                    }
                }
            }
            SmallButton {
                objectName: "clearCutsButton"
                Layout.fillWidth: true
                text: "全解除"
                enabled: !root.backend.running && Boolean(root.timeline.hasCuts)
                onClicked: root.backend.clearCuts()
            }
        }

        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.borderColor }
        Text { text: "カット一覧"; color: root.textColor; font.family: "Yu Gothic UI"; font.pixelSize: 10; font.weight: Font.DemiBold }

        ListView {
            id: cutList
            objectName: "workspaceCutList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 80
            clip: true
            spacing: 4
            boundsBehavior: Flickable.StopAtBounds
            model: root.timeline.cuts || []

            delegate: Rectangle {
                id: cutRow
                required property var modelData
                objectName: "workspaceCutRow-" + String(cutRow.modelData.id)
                width: cutList.width
                height: 42
                radius: 6
                color: root.selectedCutId === String(cutRow.modelData.id) ? "#3A2925" : root.raisedColor
                border.color: root.selectedCutId === String(cutRow.modelData.id) ? root.cutColor : root.borderColor
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 6
                    spacing: 5
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0
                        Text {
                            text: Number(cutRow.modelData.source_start).toFixed(3)
                                + " – " + Number(cutRow.modelData.source_end).toFixed(3)
                            color: root.textColor
                            font.family: "Cascadia Mono"
                            font.pixelSize: 9
                        }
                        Text {
                            text: Number(cutRow.modelData.duration).toFixed(3) + " 秒"
                            color: root.mutedColor
                            font.family: "Yu Gothic UI"
                            font.pixelSize: 8
                        }
                    }
                    SmallButton {
                        Layout.preferredWidth: 44
                        text: "選択"
                        onClicked: root.cutSelected(
                            String(cutRow.modelData.id),
                            Number(cutRow.modelData.source_start) * 1000,
                            Number(cutRow.modelData.source_end) * 1000
                        )
                    }
                }
            }

            Text {
                anchors.centerIn: parent
                visible: cutList.count === 0
                text: "カットはありません"
                color: root.mutedColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 9
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 5
            SmallButton { objectName: "undoCutButton"; Layout.fillWidth: true; text: "元に戻す"; enabled: root.backend.canUndo && !root.backend.running; onClicked: root.backend.undoCutEdit() }
            SmallButton { objectName: "redoCutButton"; Layout.fillWidth: true; text: "やり直す"; enabled: root.backend.canRedo && !root.backend.running; onClicked: root.backend.redoCutEdit() }
        }

        Text {
            Layout.fillWidth: true
            text: "元動画と元字幕の時刻は変更しません"
            color: root.mutedColor
            font.family: "Yu Gothic UI"
            font.pixelSize: 8
            wrapMode: Text.Wrap
        }
    }
}
