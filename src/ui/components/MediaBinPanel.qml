pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root

    required property var backend
    required property color panelColor
    required property color raisedColor
    required property color borderColor
    required property color textColor
    required property color mutedColor
    required property color accentColor
    required property color warningColor
    required property color dangerColor

    readonly property int dragThreshold: 10
    objectName: "mediaBinPanel"
    radius: 12
    color: root.panelColor
    border.color: root.borderColor
    signal sourceSettingsRequested()

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 7
        spacing: 5

        RowLayout {
            Layout.fillWidth: true
            Text {
                objectName: "mediaBinTitle"
                text: "素材一覧"
                color: root.textColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 10
                font.weight: Font.DemiBold
            }
            Item { Layout.fillWidth: true }
            SmallButton {
                objectName: "mediaBinAddButton"
                text: "追加"
                enabled: root.backend && !root.backend.running
                onClicked: root.backend.browseSequenceAsset()
            }
            Text {
                text: root.backend ? String(root.backend.mediaBinAssets.length) : "0"
                color: root.mutedColor
                font.family: "Cascadia Mono"
                font.pixelSize: 11
            }
        }

        SmallButton {
            objectName: "mediaBinSourceSettingsButton"
            Layout.fillWidth: true
            text: "動画・話者音声の設定"
            enabled: root.backend && !root.backend.running
            onClicked: root.sourceSettingsRequested()
        }

        ListView {
            id: mediaBinList
            objectName: "mediaBinList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 4
            model: root.backend ? root.backend.mediaBinAssets : []
            ScrollBar.vertical: ScrollBar {}
            Text {
                anchors.centerIn: parent
                visible: mediaBinList.count === 0
                text: "動画を追加してください"
                color: root.mutedColor
            }

            delegate: Rectangle {
                id: assetItem
                objectName: "mediaBinAssetCard"
                required property var modelData
                readonly property string assetId: String(modelData.id || "")
                Rectangle {
                    id: dragPreview
                    objectName: "mediaAssetDragPreview"
                    width: assetItem.width
                    height: assetItem.height
                    parent: Overlay.overlay
                    visible: assetDrag.dragging
                    color: root.raisedColor
                    border.color: root.accentColor
                    z: 100
                    Drag.active: assetDrag.dragging
                    Drag.source: assetItem
                    Drag.supportedActions: Qt.CopyAction
                    Drag.hotSpot.x: assetDrag.pressX
                    Drag.hotSpot.y: assetDrag.pressY
                    Text { anchors.centerIn: parent; text: "動画を配置"; color: root.textColor }
                }
                MouseArea {
                    id: assetDrag
                    objectName: "mediaAssetDragArea"
                    anchors.fill: parent
                    enabled: root.backend && !root.backend.running
                    property bool dragging: false
                    property real pressX: 0
                    property real pressY: 0
                    preventStealing: true
                    onPressed: function(mouse) { pressX = mouse.x; pressY = mouse.y }
                    onPositionChanged: function(mouse) {
                        if (!pressed)
                            return
                        if (Math.abs(mouse.x - pressX) + Math.abs(mouse.y - pressY) > root.dragThreshold) {
                            var position = mapToItem(dragPreview.parent, mouse.x, mouse.y)
                            dragPreview.x = position.x - pressX
                            dragPreview.y = position.y - pressY
                            dragging = true
                        }
                    }
                    onReleased: { dragPreview.Drag.drop(); dragging = false }
                    onCanceled: { dragPreview.Drag.cancel(); dragging = false }
                }
                width: mediaBinList.width
                height: 76
                radius: 6
                color: root.panelColor
                border.color: root.borderColor

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 5
                    spacing: 5
                    ColumnLayout {
                        enabled: false
                        Layout.fillWidth: true
                        spacing: 1
                        Text {
                            Layout.fillWidth: true
                            text: String(assetItem.modelData.name || assetItem.modelData.path || "動画")
                            color: root.textColor
                            font.family: "Yu Gothic UI"
                            font.pixelSize: 11
                            elide: Text.ElideMiddle
                        }
                        Text {
                            text: Number(assetItem.modelData.duration || 0).toFixed(2) + "秒  "
                                + String(assetItem.modelData.clipCount || 0) + " クリップ"
                            color: root.mutedColor
                            font.family: "Cascadia Mono"
                            font.pixelSize: 10
                        }
                    }
                    SmallButton {
                        objectName: "addSequenceClipButton"
                        property string sequenceAssetId: String(assetItem.modelData.id || "")
                        text: "+"
                        implicitWidth: 28
                        enabled: root.backend && !root.backend.running
                            && Number(assetItem.modelData.duration || 0) > 0
                        onClicked: root.backend.addSequenceClip(String(assetItem.modelData.id))
                    }
                }
            }
        }

        DropArea {
            id: mediaBinDropArea
            objectName: "mediaBinDropArea"
            Layout.fillWidth: true
            Layout.preferredHeight: 30
            enabled: root.backend && !root.backend.running
            onEntered: function(drag) {
                drag.accepted = drag.hasUrls
            }
            onDropped: function(drop) {
                if (drop.hasUrls && root.backend)
                    root.backend.addSequenceAssets(drop.urls)
                drop.acceptProposedAction()
            }

            Rectangle {
                anchors.fill: parent
                radius: 6
                color: mediaBinDropArea.containsDrag ? "#302A5A" : "transparent"
                border.color: mediaBinDropArea.containsDrag ? root.accentColor : root.borderColor
                Text {
                    anchors.centerIn: parent
                    text: mediaBinDropArea.containsDrag ? "ここへドロップ" : "動画をドロップして追加"
                    color: mediaBinDropArea.containsDrag ? root.accentColor : root.mutedColor
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 10
                }
            }
        }
    }
}
