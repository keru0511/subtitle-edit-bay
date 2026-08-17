import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: clipListRoot
    objectName: "shortModeClipList"
    spacing: 10

    property var appBackend: null
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
        Button {
            id: addButton
            objectName: "shortModeAddClipButton"
            text: "追加"
            enabled: segmentCombo.currentValue !== undefined && segmentCombo.currentValue !== ""
            onClicked: {
                if (clipListRoot.appBackend) {
                    clipListRoot.appBackend.addShortVideoClip(segmentCombo.currentValue)
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
            height: 76
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
                }

                ComboBox {
                    id: fitCombo
                    objectName: "shortModeFitCombo" + index
                    model: ["cover", "contain", "blur"]
                    currentIndex: modelData.fit === "contain" ? 1 : (modelData.fit === "blur" ? 2 : 0)
                    onActivated: {
                        if (clipListRoot.appBackend) {
                            clipListRoot.appBackend.updateShortVideoClip(index, {"fit": fitCombo.currentText})
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
