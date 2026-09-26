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
    property int sequenceZoomPercent: 100
    readonly property real sequencePixelsPerSecond: 34 * root.sequenceZoomPercent / 100

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

    function sequenceClipWidth(clip) {
        return Math.max(120, Number(clip ? clip.duration : 0) * root.sequencePixelsPerSecond)
    }

    function setSequenceZoom(value) {
        root.sequenceZoomPercent = Math.max(50, Math.min(250, Math.round(Number(value) / 10) * 10))
    }

    function assetDuration(assetId) {
        var assets = root.backend ? root.backend.sequence.mediaBinAssets : []
        for (var index = 0; index < assets.length; ++index) {
            if (String(assets[index].id || "") === String(assetId || ""))
                return Number(assets[index].duration || 0)
        }
        return 0
    }

    function sequenceTargetIndex(sceneX) {
        var clips = root.backend ? root.backend.sequence.sequenceClips : []
        if (clips.length === 0)
            return 0
        var localX = sequenceTimelineList.mapFromItem(null, sceneX, 0).x
            + sequenceTimelineList.contentX
        var cursor = 0
        for (var index = 0; index < clips.length; ++index) {
            var clipWidth = root.sequenceClipWidth(clips[index])
            if (localX < cursor + clipWidth / 2)
                return index
            cursor += clipWidth + sequenceTimelineList.spacing
        }
        return clips.length
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
                    text: root.backend && root.backend.sequence.sequenceError
                        ? root.backend.sequence.sequenceError
                        : (root.backend && root.backend.projectDirty ? "編集内容を保存しています" : "保存済み")
                    color: root.backend && root.backend.sequence.sequenceError ? root.dangerColor : root.mutedColor
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 8
                    elide: Text.ElideRight
                }
            }

            Text {
                objectName: "sequenceOutputDurationText"
                text: root.backend
                    ? "出力 " + Number(root.backend.sequence.sequenceOutputDuration || 0).toFixed(2) + "秒"
                    : "出力 0.00秒"
                color: root.accentColor
                font.family: "Cascadia Mono"
                font.pixelSize: 9
            }
            SmallButton {
                objectName: "sequenceUndoButton"
                text: "元に戻す"
                enabled: root.backend && root.backend.subtitles.canUndo && !root.backend.running
                onClicked: root.backend.subtitles.undoCutEdit()
            }
            SmallButton {
                objectName: "sequenceRedoButton"
                text: "やり直す"
                enabled: root.backend && root.backend.subtitles.canRedo && !root.backend.running
                onClicked: root.backend.subtitles.redoCutEdit()
            }
            SmallButton {
                objectName: "sequenceAddAssetButton"
                text: "動画を追加"
                enabled: root.backend && !root.backend.running
                onClicked: root.backend.sequence.browseSequenceAsset()
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
                                ? ("再生位置 " + Number(root.backend.sequence.sequencePlayhead.outputSeconds || 0).toFixed(2)
                                    + "秒 / " + Number(root.backend.sequence.sequenceOutputDuration || 0).toFixed(2) + "秒")
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
                        to: Math.max(1, Number(root.backend ? root.backend.sequence.sequenceOutputDuration : 0) * 1000)
                        Binding {
                            target: sequencePlayheadSlider
                            property: "value"
                            value: Number(root.backend ? root.backend.sequence.sequencePlayhead.outputMs : 0)
                            when: !sequencePlayheadSlider.pressed
                        }
                        onMoved: {
                            if (root.backend)
                                root.backend.sequence.setSequencePlayhead(Math.round(value))
                        }
                    }

                    Rectangle {
                        id: sequenceTimelineViewport
                        objectName: "sequenceTimelineViewport"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 86
                        Layout.minimumHeight: 86
                        radius: 6
                        color: root.panelColor
                        border.color: root.borderColor
                        clip: true

                        RowLayout {
                            anchors.top: parent.top
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.topMargin: 3
                            anchors.leftMargin: 5
                            anchors.rightMargin: 5
                            height: 19
                            spacing: 5

                            Text {
                                Layout.fillWidth: true
                                text: "V1  映像シーケンス  ｜  中央をドラッグして移動・両端をドラッグして長さ変更"
                                color: root.mutedColor
                                font.family: "Yu Gothic UI"
                                font.pixelSize: 8
                                elide: Text.ElideRight
                            }
                            Text {
                                text: "ズーム"
                                color: root.mutedColor
                                font.family: "Yu Gothic UI"
                                font.pixelSize: 8
                            }
                            Slider {
                                id: sequenceTimelineZoomSlider
                                objectName: "sequenceTimelineZoomSlider"
                                Layout.preferredWidth: 96
                                Layout.preferredHeight: 18
                                from: 50
                                to: 250
                                stepSize: 10
                                value: root.sequenceZoomPercent
                                onMoved: root.setSequenceZoom(value)
                            }
                            Text {
                                objectName: "sequenceTimelineZoomLabel"
                                Layout.preferredWidth: 31
                                text: root.sequenceZoomPercent + "%"
                                color: root.textColor
                                font.family: "Cascadia Mono"
                                font.pixelSize: 8
                                horizontalAlignment: Text.AlignRight
                            }
                        }

                        ListView {
                            id: sequenceTimelineList
                            objectName: "sequenceTimelineList"
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.bottom: parent.bottom
                            anchors.topMargin: 24
                            anchors.margins: 5
                            orientation: ListView.Horizontal
                            spacing: 4
                            clip: true
                            boundsBehavior: Flickable.StopAtBounds
                            ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
                            model: root.backend ? root.backend.sequence.sequenceClips : []

                            delegate: Rectangle {
                                id: timelineClip
                                required property int index
                                required property var modelData
                                readonly property string clipId: String(timelineClip.modelData.clipId || "")
                                property real displaySourceStart: Number(timelineClip.modelData.sourceStart || 0)
                                property real displaySourceEnd: Number(timelineClip.modelData.sourceEnd || 0)

                                objectName: "sequenceTimelineClip-" + timelineClip.clipId
                                width: Math.max(
                                    120,
                                    (timelineClip.displaySourceEnd - timelineClip.displaySourceStart)
                                        * root.sequencePixelsPerSecond
                                )
                                height: sequenceTimelineList.height - 10
                                radius: 5
                                color: root.selectedClipId === timelineClip.clipId ? "#433878" : "#302A5A"
                                border.color: root.selectedClipId === timelineClip.clipId
                                    ? root.textColor : root.accentColor
                                border.width: root.selectedClipId === timelineClip.clipId ? 2 : 1

                                transform: Translate { x: timelineMoveArea.dragOffset }

                                Binding {
                                    target: timelineClip
                                    property: "displaySourceStart"
                                    value: Number(timelineClip.modelData.sourceStart || 0)
                                    when: !timelineTrimStartArea.pressed
                                }
                                Binding {
                                    target: timelineClip
                                    property: "displaySourceEnd"
                                    value: Number(timelineClip.modelData.sourceEnd || 0)
                                    when: !timelineTrimEndArea.pressed
                                }

                                Text {
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.top: parent.top
                                    anchors.leftMargin: 15
                                    anchors.rightMargin: 15
                                    anchors.topMargin: 8
                                    text: String(timelineClip.modelData.assetName || "動画")
                                    color: root.textColor
                                    font.family: "Yu Gothic UI"
                                    font.pixelSize: 9
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideMiddle
                                    horizontalAlignment: Text.AlignHCenter
                                }
                                Text {
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.bottom: parent.bottom
                                    anchors.leftMargin: 15
                                    anchors.rightMargin: 15
                                    anchors.bottomMargin: 7
                                    text: Number(timelineClip.displaySourceStart).toFixed(2) + " - "
                                        + Number(timelineClip.displaySourceEnd).toFixed(2) + "秒"
                                    color: root.mutedColor
                                    font.family: "Cascadia Mono"
                                    font.pixelSize: 8
                                    horizontalAlignment: Text.AlignHCenter
                                }

                                MouseArea {
                                    id: timelineMoveArea
                                    property real pressSceneX: 0
                                    property real dragOffset: 0
                                    property bool moved: false
                                    property string clipId: timelineClip.clipId
                                    objectName: "sequenceTimelineMoveArea-" + timelineClip.clipId
                                    anchors.fill: parent
                                    anchors.leftMargin: 12
                                    anchors.rightMargin: 12
                                    z: 10
                                    enabled: root.backend && !root.backend.running
                                    cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                                    onPressed: function(mouse) {
                                        var scenePoint = mapToItem(null, mouse.x, mouse.y)
                                        pressSceneX = scenePoint.x
                                        dragOffset = 0
                                        moved = false
                                        root.selectedClipId = timelineClip.clipId
                                    }
                                    onPositionChanged: function(mouse) {
                                        if (!pressed)
                                            return
                                        var scenePoint = mapToItem(null, mouse.x, mouse.y)
                                        dragOffset = scenePoint.x - pressSceneX
                                        moved = moved || Math.abs(dragOffset) >= 6
                                    }
                                    onReleased: function(mouse) {
                                        var scenePoint = mapToItem(null, mouse.x, mouse.y)
                                        if (moved && root.backend) {
                                            root.backend.sequence.moveSequenceClip(
                                                timelineClip.clipId,
                                                root.sequenceTargetIndex(scenePoint.x)
                                            )
                                        } else if (root.backend) {
                                            root.backend.sequence.setSequencePlayhead(
                                                Math.round(Number(timelineClip.modelData.outputStart || 0) * 1000)
                                            )
                                        }
                                        dragOffset = 0
                                    }
                                    onCanceled: dragOffset = 0
                                }

                                MouseArea {
                                    id: timelineTrimStartArea
                                    property real pressSceneX: 0
                                    property real initialValue: 0
                                    property string clipId: timelineClip.clipId
                                    objectName: "sequenceTimelineTrimStart-" + timelineClip.clipId
                                    anchors.left: parent.left
                                    anchors.top: parent.top
                                    anchors.bottom: parent.bottom
                                    width: 12
                                    z: 30
                                    enabled: root.backend && !root.backend.running
                                    cursorShape: Qt.SizeHorCursor
                                    onPressed: function(mouse) {
                                        var scenePoint = mapToItem(null, mouse.x, mouse.y)
                                        pressSceneX = scenePoint.x
                                        initialValue = Number(timelineClip.modelData.sourceStart || 0)
                                        root.selectedClipId = timelineClip.clipId
                                    }
                                    onPositionChanged: function(mouse) {
                                        if (!pressed)
                                            return
                                        var scenePoint = mapToItem(null, mouse.x, mouse.y)
                                        var candidate = initialValue
                                            + (scenePoint.x - pressSceneX) / root.sequencePixelsPerSecond
                                        candidate = Math.max(
                                            0,
                                            Math.min(timelineClip.displaySourceEnd - 0.1, candidate)
                                        )
                                        timelineClip.displaySourceStart = Math.round(candidate * 100) / 100
                                    }
                                    onReleased: {
                                        if (root.backend
                                                && Math.abs(timelineClip.displaySourceStart - initialValue) >= 0.001)
                                            root.backend.sequence.trimSequenceClip(
                                                timelineClip.clipId,
                                                timelineClip.displaySourceStart,
                                                Number(timelineClip.modelData.sourceEnd || 0)
                                            )
                                    }
                                }

                                MouseArea {
                                    id: timelineTrimEndArea
                                    property real pressSceneX: 0
                                    property real initialValue: 0
                                    property string clipId: timelineClip.clipId
                                    objectName: "sequenceTimelineTrimEnd-" + timelineClip.clipId
                                    anchors.right: parent.right
                                    anchors.top: parent.top
                                    anchors.bottom: parent.bottom
                                    width: 12
                                    z: 30
                                    enabled: root.backend && !root.backend.running
                                    cursorShape: Qt.SizeHorCursor
                                    onPressed: function(mouse) {
                                        var scenePoint = mapToItem(null, mouse.x, mouse.y)
                                        pressSceneX = scenePoint.x
                                        initialValue = Number(timelineClip.modelData.sourceEnd || 0)
                                        root.selectedClipId = timelineClip.clipId
                                    }
                                    onPositionChanged: function(mouse) {
                                        if (!pressed)
                                            return
                                        var scenePoint = mapToItem(null, mouse.x, mouse.y)
                                        var candidate = initialValue
                                            + (scenePoint.x - pressSceneX) / root.sequencePixelsPerSecond
                                        var maximum = root.assetDuration(timelineClip.modelData.assetId)
                                        candidate = Math.max(
                                            timelineClip.displaySourceStart + 0.1,
                                            Math.min(maximum, candidate)
                                        )
                                        timelineClip.displaySourceEnd = Math.round(candidate * 100) / 100
                                    }
                                    onReleased: {
                                        if (root.backend
                                                && Math.abs(timelineClip.displaySourceEnd - initialValue) >= 0.001)
                                            root.backend.sequence.trimSequenceClip(
                                                timelineClip.clipId,
                                                Number(timelineClip.modelData.sourceStart || 0),
                                                timelineClip.displaySourceEnd
                                            )
                                    }
                                }

                                Rectangle {
                                    anchors.left: parent.left
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: 4
                                    height: parent.height - 12
                                    radius: 2
                                    color: root.textColor
                                    opacity: timelineTrimStartArea.containsMouse || timelineTrimStartArea.pressed ? 1 : 0.5
                                }
                                Rectangle {
                                    anchors.right: parent.right
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: 4
                                    height: parent.height - 12
                                    radius: 2
                                    color: root.textColor
                                    opacity: timelineTrimEndArea.containsMouse || timelineTrimEndArea.pressed ? 1 : 0.5
                                }
                            }
                        }

                        DropArea {
                            id: sequenceTimelineDropArea
                            objectName: "sequenceTimelineDropArea"
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.bottom: parent.bottom
                            anchors.topMargin: 24
                            anchors.margins: 5
                            z: 100
                            enabled: root.backend && !root.backend.running
                            onEntered: function(drag) {
                                drag.accepted = root.assetIdFromDrag(drag).length > 0
                            }
                            onDropped: function(drop) {
                                var assetId = root.assetIdFromDrag(drop)
                                if (assetId.length > 0 && root.backend) {
                                    var scenePoint = mapToItem(null, drop.x, drop.y)
                                    root.backend.sequence.insertSequenceClip(
                                        assetId,
                                        root.sequenceTargetIndex(scenePoint.x)
                                    )
                                    drop.acceptProposedAction()
                                }
                            }
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
                        model: root.backend ? root.backend.sequence.sequenceClips : []

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
                                        root.backend.sequence.setSequencePlayhead(
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
                                        root.backend.sequence.insertSequenceClip(assetId, clipItem.index)
                                        root.hoverIndex = -1
                                        drop.acceptProposedAction()
                                        return
                                    }
                                    var clipId = root.clipIdFromDrag(drop)
                                    if (clipId.length > 0)
                                        root.activeDragClipId = clipId
                                    if (root.activeDragClipId.length > 0 && root.backend)
                                        root.backend.sequence.moveSequenceClip(root.activeDragClipId, clipItem.index)
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
                                        onClicked: root.backend.sequence.removeSequenceClip(clipItem.clipId)
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
                                            var accepted = root.backend && root.backend.sequence.trimSequenceClip(
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
                                            var accepted = root.backend && root.backend.sequence.trimSequenceClip(
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
                                                root.backend.sequence.setSequenceTransition(
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
                                                root.backend.sequence.setSequenceTransition(
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
                                                root.backend.sequence.setSequenceClipAudio(
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
                                                root.backend.sequence.setSequenceClipAudio(
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
                                                root.backend.sequence.setSequenceClipAudio(
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
                                                root.backend.sequence.setSequenceClipAudio(
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
