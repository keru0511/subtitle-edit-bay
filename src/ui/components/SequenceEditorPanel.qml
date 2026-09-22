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

    property int hoverIndex: -1
    property string activeDragClipId: ""
    property string selectedClipId: ""

    objectName: "sequenceEditorPanel"
    radius: 12
    color: root.panelColor
    border.color: root.borderColor

    function transitionIndex(value) {
        var options = ["cut", "crossfade", "fade"]
        var normalized = String(value || "cut")
        var index = options.indexOf(normalized)
        return index >= 0 ? index : 0
    }

    function assetIdFromDrag(event) {
        var source = event ? event.source : null
        return source ? String(source["assetId"] || "") : ""
    }

    function resetTimeField(field, value) {
        field.text = Number(value || 0).toFixed(3)
        field.focus = false
    }

    // DropArea exposes drag.source as a QObject.  Bracket access keeps the
    // dynamic delegate property out of qmllint's static QObject contract.
    function clipIdFromDrag(dragEvent) {
        var source = dragEvent ? dragEvent.source : null
        return source ? String(source["clipId"] || "") : ""
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 6

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1
                Text {
                    objectName: "sequenceEditorTitle"
                    text: "動画結合・シーケンス"
                    color: root.textColor
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                }
                Text {
                    Layout.fillWidth: true
                    text: root.backend && root.backend.sequenceError
                        ? root.backend.sequenceError
                        : (root.backend && root.backend.projectDirty ? "編集内容を保存しています" : "保存済み")
                    color: root.backend && root.backend.sequenceError ? root.dangerColor : root.mutedColor
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 8
                    elide: Text.ElideRight
                }
            }

            Text {
                objectName: "sequenceOutputDurationText"
                text: root.backend
                    ? "出力 " + Number(root.backend.sequenceOutputDuration || 0).toFixed(2) + "秒"
                    : "出力 0.00秒"
                color: root.accentColor
                font.family: "Cascadia Mono"
                font.pixelSize: 9
            }
            SmallButton {
                objectName: "sequenceUndoButton"
                text: "元に戻す"
                enabled: root.backend && root.backend.canUndo && !root.backend.running
                onClicked: root.backend.undoCutEdit()
            }
            SmallButton {
                objectName: "sequenceRedoButton"
                text: "やり直す"
                enabled: root.backend && root.backend.canRedo && !root.backend.running
                onClicked: root.backend.redoCutEdit()
            }
            SmallButton {
                objectName: "sequenceAddAssetButton"
                text: "動画を追加"
                enabled: root.backend && !root.backend.running
                onClicked: root.backend.browseSequenceAsset()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 8

            Rectangle {
                id: sequencePanel
                objectName: "sequencePanel"
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: 8
                color: root.raisedColor
                border.color: root.borderColor

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 7
                    spacing: 5

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            objectName: "sequencePanelTitle"
                            text: "配置済みクリップ"
                            color: root.textColor
                            font.family: "Yu Gothic UI"
                            font.pixelSize: 10
                            font.weight: Font.DemiBold
                        }
                        Item { Layout.fillWidth: true }
                        Text {
                            objectName: "sequencePlayheadText"
                            text: root.backend
                                ? ("再生位置 " + Number(root.backend.sequencePlayhead.outputSeconds || 0).toFixed(2)
                                    + "秒 / " + Number(root.backend.sequenceOutputDuration || 0).toFixed(2) + "秒")
                                : "再生位置 0.00秒 / 0.00秒"
                            color: root.mutedColor
                            font.family: "Cascadia Mono"
                            font.pixelSize: 8
                        }
                    }

                    Slider {
                        id: sequencePlayheadSlider
                        objectName: "sequencePlayheadSlider"
                        Layout.fillWidth: true
                        from: 0
                        to: Math.max(1, Number(root.backend ? root.backend.sequenceOutputDuration : 0) * 1000)
                        Binding {
                            target: sequencePlayheadSlider
                            property: "value"
                            value: Number(root.backend ? root.backend.sequencePlayhead.outputMs : 0)
                            when: !sequencePlayheadSlider.pressed
                        }
                        onMoved: {
                            if (root.backend)
                                root.backend.setSequencePlayhead(Math.round(value))
                        }
                    }

                    ListView {
                        id: sequenceClipList
                        objectName: "sequenceClipList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.minimumHeight: 138
                        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                        clip: true
                        spacing: 4
                        model: root.backend ? root.backend.sequenceClips : []

                        delegate: Rectangle {
                            id: clipItem
                            required property int index
                            required property var modelData
                            readonly property string clipId: String(clipItem.modelData.clipId || "")
                            width: sequenceClipList.width
                            height: 132
                            radius: 6
                            color: root.selectedClipId === clipItem.clipId ? "#302A5A" : root.panelColor
                            border.color: root.hoverIndex === clipItem.index || root.selectedClipId === clipItem.clipId
                                ? root.accentColor : root.borderColor
                            border.width: root.hoverIndex === clipItem.index ? 2 : 1

                            Drag.active: reorderHandler.active
                            Drag.source: clipItem
                            Drag.supportedActions: Qt.MoveAction
                            Drag.hotSpot.x: width / 2
                            Drag.hotSpot.y: height / 2

                            TapHandler {
                                onTapped: {
                                    root.selectedClipId = clipItem.clipId
                                    if (root.backend)
                                        root.backend.setSequencePlayhead(
                                            Math.round(Number(clipItem.modelData.outputStart || 0) * 1000)
                                        )
                                }
                            }
                            DragHandler {
                                id: reorderHandler
                                enabled: root.backend && !root.backend.running
                                target: null
                                onActiveChanged: {
                                    if (active) {
                                        root.activeDragClipId = clipItem.clipId
                                    } else {
                                        if (root.activeDragClipId === clipItem.clipId)
                                            root.activeDragClipId = ""
                                        root.hoverIndex = -1
                                    }
                                }
                            }

                            DropArea {
                                id: clipDropArea
                                objectName: "sequenceClipDropArea"
                                anchors.fill: parent
                                enabled: root.backend && !root.backend.running
                                z: 5
                                onEntered: function(drag) {
                                    var assetId = root.assetIdFromDrag(drag)
                                    drag.accepted = assetId.length > 0 || root.clipIdFromDrag(drag).length > 0
                                    if (assetId.length > 0)
                                        root.hoverIndex = clipItem.index
                                    var clipId = root.clipIdFromDrag(drag)
                                    if (clipId.length > 0) {
                                        root.activeDragClipId = clipId
                                        root.hoverIndex = clipItem.index
                                    }
                                }
                                onExited: {
                                    if (root.hoverIndex === clipItem.index)
                                        root.hoverIndex = -1
                                }
                                onDropped: function(drop) {
                                    var assetId = root.assetIdFromDrag(drop)
                                    if (assetId.length > 0) {
                                        root.backend.insertSequenceClip(assetId, clipItem.index)
                                        root.hoverIndex = -1
                                        drop.acceptProposedAction()
                                        return
                                    }
                                    var clipId = root.clipIdFromDrag(drop)
                                    if (clipId.length > 0)
                                        root.activeDragClipId = clipId
                                    if (root.activeDragClipId.length > 0 && root.backend)
                                        root.backend.moveSequenceClip(root.activeDragClipId, clipItem.index)
                                    root.activeDragClipId = ""
                                    root.hoverIndex = -1
                                    drop.acceptProposedAction()
                                }
                            }

                            ColumnLayout {
                                enabled: root.backend && !root.backend.running
                                anchors.fill: parent
                                anchors.margins: 5
                                spacing: 3
                                RowLayout {
                                    Layout.fillWidth: true
                                    Text {
                                        Layout.fillWidth: true
                                        text: String(clipItem.modelData.assetName || "動画")
                                        color: root.textColor
                                        font.family: "Yu Gothic UI"
                                        font.pixelSize: 9
                                        font.weight: Font.DemiBold
                                        elide: Text.ElideMiddle
                                    }
                                    Text {
                                        text: Number(clipItem.modelData.outputStart || 0).toFixed(2) + " - "
                                            + Number(clipItem.modelData.outputEnd || 0).toFixed(2) + "秒"
                                        color: root.mutedColor
                                        font.family: "Cascadia Mono"
                                        font.pixelSize: 8
                                    }
                                    SmallButton {
                                        objectName: "removeSequenceClipButton"
                                        text: "削除"
                                        implicitWidth: 40
                                        enabled: root.backend && !root.backend.running
                                        onClicked: root.backend.removeSequenceClip(clipItem.clipId)
                                    }
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 3
                                    Text { text: "範囲"; color: root.mutedColor; font.pixelSize: 8 }
                                    TimeField {
                                        id: clipStartField
                                        objectName: "sequenceClipStartField"
                                        Layout.preferredWidth: 66
                                        text: Number(clipItem.modelData.sourceStart || 0).toFixed(3)
                                        onEditingFinished: {
                                            var accepted = root.backend && root.backend.trimSequenceClip(
                                                clipItem.clipId, Number(text), Number(clipEndField.text))
                                            if (!accepted)
                                                root.resetTimeField(clipStartField, clipItem.modelData.sourceStart)
                                        }
                                        Binding {
                                            target: clipStartField
                                            property: "text"
                                            value: Number(clipItem.modelData.sourceStart || 0).toFixed(3)
                                            when: !clipStartField.activeFocus
                                        }
                                    }
                                    Text { text: "-"; color: root.mutedColor; font.pixelSize: 8 }
                                    TimeField {
                                        id: clipEndField
                                        objectName: "sequenceClipEndField"
                                        Layout.preferredWidth: 66
                                        text: Number(clipItem.modelData.sourceEnd || 0).toFixed(3)
                                        onEditingFinished: {
                                            var accepted = root.backend && root.backend.trimSequenceClip(
                                                clipItem.clipId, Number(clipStartField.text), Number(text))
                                            if (!accepted)
                                                root.resetTimeField(clipEndField, clipItem.modelData.sourceEnd)
                                        }
                                        Binding {
                                            target: clipEndField
                                            property: "text"
                                            value: Number(clipItem.modelData.sourceEnd || 0).toFixed(3)
                                            when: !clipEndField.activeFocus
                                        }
                                    }
                                    Text { text: "切替"; color: root.mutedColor; font.pixelSize: 8 }
                                    ComboBox {
                                        id: transitionCombo
                                        objectName: "sequenceTransitionCombo"
                                        Layout.preferredWidth: 92
                                        model: ["cut", "crossfade", "fade"]
                                        currentIndex: root.transitionIndex(clipItem.modelData.transition
                                            ? clipItem.modelData.transition.type : "cut")
                                        onActivated: function(_index) {
                                            if (root.backend)
                                                root.backend.setSequenceTransition(
                                                    clipItem.clipId, currentText, transitionDuration.value / 1000)
                                        }
                                    }
                                    SpinBox {
                                        id: transitionDuration
                                        Layout.preferredWidth: 110
                                        objectName: "sequenceTransitionDuration"
                                        from: 0
                                        to: 10000
                                        stepSize: 50
                                        value: Math.round(Number(clipItem.modelData.transition
                                            ? clipItem.modelData.transition.duration : 0) * 1000)
                                        editable: true
                                        onValueModified: {
                                            if (root.backend)
                                                root.backend.setSequenceTransition(
                                                    clipItem.clipId, transitionCombo.currentText, value / 1000)
                                        }
                                    }
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 4
                                    CheckBox {
                                        id: audioLinkedCheck
                                        objectName: "sequenceAudioLinkedCheck"
                                        text: "音声連動"
                                        checked: Boolean(clipItem.modelData.audioLinked)
                                        onToggled: {
                                            if (root.backend)
                                                root.backend.setSequenceClipAudio(
                                                    clipItem.clipId, checked, Number(volumeSlider.value),
                                                    Number(audioOffset.value) / 1000, mutedCheck.checked)
                                        }
                                    }
                                    Text { text: "音量"; color: root.mutedColor; font.pixelSize: 8 }
                                    Slider {
                                        id: volumeSlider
                                        objectName: "sequenceClipVolumeSlider"
                                        Layout.preferredWidth: 80
                                        from: 0
                                        to: 2
                                        value: Number(clipItem.modelData.volume || 0)
                                        onMoved: {
                                            if (root.backend)
                                                root.backend.setSequenceClipAudio(
                                                    clipItem.clipId, audioLinkedCheck.checked, value,
                                                    Number(audioOffset.value) / 1000, mutedCheck.checked)
                                        }
                                    }
                                    CheckBox {
                                        id: mutedCheck
                                        objectName: "sequenceClipMutedCheck"
                                        text: "ミュート"
                                        checked: Boolean(clipItem.modelData.muted)
                                        onToggled: {
                                            if (root.backend)
                                                root.backend.setSequenceClipAudio(
                                                    clipItem.clipId, audioLinkedCheck.checked,
                                                    Number(volumeSlider.value), Number(audioOffset.value) / 1000, checked)
                                        }
                                    }
                                    SpinBox {
                                        id: audioOffset
                                        Layout.preferredWidth: 110
                                        objectName: "sequenceAudioOffset"
                                        from: -10000
                                        to: 10000
                                        stepSize: 10
                                        value: Math.round(Number(clipItem.modelData.audioOffset || 0) * 1000)
                                        editable: true
                                        onValueModified: {
                                            if (root.backend)
                                                root.backend.setSequenceClipAudio(
                                                    clipItem.clipId, audioLinkedCheck.checked,
                                                    Number(volumeSlider.value), value / 1000, mutedCheck.checked)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
