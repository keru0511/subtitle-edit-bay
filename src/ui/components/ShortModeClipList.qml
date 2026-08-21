import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

ColumnLayout {
    id: clipListRoot
    objectName: "shortModeClipList"
    spacing: 10

    property var appBackend: null
    property var fitOptions: [
        { "label": "画面いっぱい", "value": "cover" },
        { "label": "全体を表示", "value": "contain" },
        { "label": "ぼかし背景", "value": "blur" }
    ]

    function indexForFit(value) {
        for (var index = 0; index < clipListRoot.fitOptions.length; index += 1) {
            if (clipListRoot.fitOptions[index].value === value)
                return index
        }
        return 0
    }
    property int selectedIndex: 0
    signal selected(int index)

    function clampSelected() {
        if (!clipListRoot.appBackend) return
        var count = clipListRoot.appBackend.shortVideoClips.length
        if (selectedIndex >= count) selectedIndex = Math.max(0, count - 1)
    }

    Connections {
        target: clipListRoot.appBackend
        function onShortVideoChanged() { clipListRoot.clampSelected() }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        ComboBox {
            id: segmentCombo
            objectName: "shortModeSegmentCombo"
            Layout.fillWidth: true
            model: clipListRoot.appBackend ? clipListRoot.appBackend.subtitleSegments : []
            textRole: "preview_text"
            valueRole: "id"
        }
        Text { text: "範囲"; color: "#8E9B94"; font.pixelSize: 10 }
        TimeField {
            id: rangeStartField
            objectName: "shortModeRangeStartField"
            Layout.preferredWidth: 76
            text: "0.000"
        }
        Text { text: "-"; color: "#8E9B94"; font.pixelSize: 10 }
        TimeField {
            id: rangeEndField
            objectName: "shortModeRangeEndField"
            Layout.preferredWidth: 76
            text: clipListRoot.appBackend && clipListRoot.appBackend.projectDuration > 0
                  ? Math.min(5, clipListRoot.appBackend.projectDuration).toFixed(3)
                  : "1.000"
        }
        Button {
            id: addButton
            objectName: "shortModeAddClipButton"
            text: "ショートに追加"
            enabled: clipListRoot.appBackend && !clipListRoot.appBackend.running && (
                (segmentCombo.currentValue !== undefined && segmentCombo.currentValue !== "")
                || (Number(rangeStartField.text) >= 0
                    && Number(rangeEndField.text) > Number(rangeStartField.text)
                    && (clipListRoot.appBackend.projectDuration <= 0
                        || Number(rangeEndField.text) <= clipListRoot.appBackend.projectDuration))
            )
            onClicked: {
                if (clipListRoot.appBackend) {
                    if (segmentCombo.currentValue !== undefined && segmentCombo.currentValue !== "") {
                        clipListRoot.appBackend.addShortVideoClip(segmentCombo.currentValue)
                    } else {
                        clipListRoot.appBackend.addShortVideoClipByRange(
                            Number(rangeStartField.text), Number(rangeEndField.text))
                    }
                }
            }
            contentItem: Text {
                text: addButton.text
                color: addButton.enabled ? "#10140F" : "#68716B"
                font.family: "Yu Gothic UI"
                font.pixelSize: 12
                font.weight: Font.Bold
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                radius: 8
                color: addButton.enabled ? "#C8FF3D" : "#252C28"
            }
        }
    }

    ListView {
        id: clipListView
        objectName: "shortModeClipListView"
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        model: clipListRoot.appBackend ? clipListRoot.appBackend.shortVideoClips : []
        spacing: 6

        delegate: Rectangle {
            id: clipItem
            objectName: "shortModeClipItem" + index
            width: clipListView.width
            height: 124
            color: clipListRoot.selectedIndex === index ? "#2A3530" : "#121715"
            border.color: clipListRoot.selectedIndex === index ? "#C8FF3D" : "#2A3530"
            radius: 8

            MouseArea {
                anchors.fill: parent
                onClicked: clipListRoot.selected(index)
            }

            RowLayout {
                anchors.fill: parent
                anchors.margins: 8
                spacing: 8

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    Text {
                        text: modelData.preview_text || modelData.text || ""
                        color: "#F4F1E8"
                        font.family: "Yu Gothic UI"
                        font.pixelSize: 12
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                    Text {
                        text: (modelData.speaker || "話者なし") + "  " + modelData.start.toFixed(2) + " - " + modelData.end.toFixed(2)
                        color: "#8E9B94"
                        font.family: "Cascadia Mono"
                        font.pixelSize: 10
                    }
                    RowLayout {
                        spacing: 4
                        Text { text: "開始"; color: "#8E9B94"; font.pixelSize: 10 }
                        TimeField {
                            id: startTimeField
                            objectName: "shortModeStartTimeField" + index
                            Layout.preferredWidth: 82
                            text: Number(modelData.start).toFixed(3)
                            onEditingFinished: {
                                var accepted = clipListRoot.appBackend
                                    && clipListRoot.appBackend.updateShortVideoClip(index, {"start": Number(text)})
                                if (!accepted) text = Number(modelData.start).toFixed(3)
                                focus = false
                            }
                            Binding {
                                target: startTimeField
                                property: "text"
                                value: Number(modelData.start).toFixed(3)
                                when: !startTimeField.activeFocus
                            }
                        }
                        Text { text: "終了"; color: "#8E9B94"; font.pixelSize: 10 }
                        TimeField {
                            id: endTimeField
                            objectName: "shortModeEndTimeField" + index
                            Layout.preferredWidth: 82
                            text: Number(modelData.end).toFixed(3)
                            onEditingFinished: {
                                var accepted = clipListRoot.appBackend
                                    && clipListRoot.appBackend.updateShortVideoClip(index, {"end": Number(text)})
                                if (!accepted) text = Number(modelData.end).toFixed(3)
                                focus = false
                            }
                            Binding {
                                target: endTimeField
                                property: "text"
                                value: Number(modelData.end).toFixed(3)
                                when: !endTimeField.activeFocus
                            }
                        }
                    }
                }

                ComboBox {
                    id: fitCombo
                    objectName: "shortModeFitCombo" + index
                    model: clipListRoot.fitOptions
                    textRole: "label"
                    valueRole: "value"
                    currentIndex: clipListRoot.indexForFit(modelData.fit)
                    onActivated: {
                        if (clipListRoot.appBackend) {
                            clipListRoot.appBackend.updateShortVideoClip(index, {"fit": fitCombo.currentValue})
                        }
                    }
                }

                ColumnLayout {
                    spacing: 2
                    Button {
                        text: "▲"
                        enabled: index > 0
                        onClicked: {
                            if (clipListRoot.appBackend) {
                                clipListRoot.appBackend.moveShortVideoClip(index, index - 1)
                            }
                        }
                    }
                    Button {
                        text: "▼"
                        enabled: index < clipListView.count - 1
                        onClicked: {
                            if (clipListRoot.appBackend) {
                                clipListRoot.appBackend.moveShortVideoClip(index, index + 2)
                            }
                        }
                    }
                }

                Button {
                    text: "✕"
                    onClicked: {
                        if (clipListRoot.appBackend) {
                            clipListRoot.appBackend.removeShortVideoClip(index)
                        }
                    }
                }
            }
        }
    }
}
