pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtMultimedia

Item {
    id: root

    required property var appBackend
    required property MediaPlayer player
    required property SubtitleEditorState editorState
    required property var colors
    required property var formatTimestamp
    required property var projectSpeakerCache
    required property var subtitleLayoutMetricsCache
    required property real selectedSubtitleFontSize
    required property real defaultSubtitleFontSize
    required property color selectedSubtitleOutlineColor
    required property real selectedSubtitleOutlineThickness
    required property string statusText
    property bool active: true
    property int codexDrawerHeaderInset: 0
    signal previewAttached(var output)
    signal previewDetached
    signal playheadSyncRequested(real positionMs)
    signal selectionSyncRequested(var segments)
    signal speakerColorRequested(int index, string color)
    signal editRequested(string action, real atSeconds)
    signal saveRequested
    signal previewRequested
    signal renderRequested
    signal closeRequested
    property bool selectionSyncReady: false

    Component.onCompleted: {
        // Reuse the decoded source and move its output to the editor preview.
        var requestedPosition = root.editorState.positionMs;
        root.player.pause();
        root.previewAttached(editorVideo);
        root.player.position = requestedPosition;
        editorSeek.to = Math.max(1, root.player.duration);
        editorSeek.value = root.player.position;
        root.selectionSyncReady = true;
        root.playheadSyncRequested(requestedPosition);
    }
    Component.onDestruction: {
        root.editorState.positionMs = root.player.position;
        root.player.pause();
        root.previewDetached();
    }
    Connections {
        target: root.player
        function onPositionChanged() {
            root.editorState.positionMs = root.player.position;
            if (!editorSeek.pressed)
                editorSeek.value = root.player.position;
        }
        function onDurationChanged() {
            editorSeek.to = Math.max(1, root.player.duration);
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 58
            Layout.leftMargin: 14
            Layout.rightMargin: 10 + root.codexDrawerHeaderInset
            spacing: 8
            Text {
                text: "字幕編集"
                color: root.colors.textPrimary
                font.family: "Yu Gothic UI"
                font.pixelSize: 17
                font.weight: Font.Bold
                font.letterSpacing: 1.0
            }
            Text {
                text: root.appBackend.projectDirty ? "● 編集あり" : "✓ 保存済み"
                color: root.appBackend.projectDirty ? root.colors.amber : root.colors.acid
                font.family: "Yu Gothic UI"
                font.pixelSize: 9
            }
            Text {
                objectName: "editorStatusText"
                Layout.fillWidth: true
                Layout.minimumWidth: 80
                text: root.statusText
                color: root.appBackend.stage === "ERROR" ? root.colors.danger : ((root.appBackend.stage === "CHECK" || root.appBackend.stage === "BUSY") ? root.colors.amber : root.colors.textMuted)
                font.family: "Yu Gothic UI"
                font.pixelSize: 9
                horizontalAlignment: Text.AlignRight
                elide: Text.ElideRight
            }
            SubtitleEditorButton {
                colors: root.colors
                objectName: "undoCaptionButton"
                text: "元に戻す"
                enabled: !root.appBackend.running && (root.appBackend.subtitles.canUndo || root.editorState.hasPendingSubtitleText)
                onClicked: root.editRequested("undo", 0)
            }
            SubtitleEditorButton {
                colors: root.colors
                objectName: "redoCaptionButton"
                text: "やり直す"
                enabled: !root.appBackend.running && root.appBackend.subtitles.canRedo && !root.editorState.hasPendingSubtitleText
                onClicked: root.editRequested("redo", 0)
            }
            SubtitleEditorButton {
                colors: root.colors
                objectName: "addCaptionButton"
                text: "+ 字幕追加"
                onClicked: root.editRequested("add", root.player.position / 1000)
            }
            SubtitleEditorButton {
                colors: root.colors
                objectName: "splitCaptionButton"
                text: "分割"
                enabled: root.editorState.canSplitSelectedSegment(root.player.position)
                onClicked: root.editRequested("split", root.player.position / 1000)
            }
            SubtitleEditorButton {
                colors: root.colors
                objectName: "deleteCaptionButton"
                text: "削除"
                enabled: root.appBackend.subtitles.selectedSegmentIndex >= 0
                onClicked: root.editRequested("delete", 0)
            }
            SubtitleEditorButton {
                colors: root.colors
                objectName: "saveProjectButton"
                text: "保存"
                onClicked: root.saveRequested()
            }
            SubtitleEditorButton {
                colors: root.colors
                objectName: "buildAssButton"
                text: "プレビューを更新"
                onClicked: root.previewRequested()
            }
            Button {
                id: editorRenderButton
                objectName: "editorRenderButton"
                implicitHeight: 34
                text: root.appBackend.workflow.activeJob === "render" ? "焼き付け中..." : "字幕を焼き付ける"
                enabled: root.appBackend.projectLoaded && !root.appBackend.running
                onClicked: root.renderRequested()
                contentItem: Text {
                    text: editorRenderButton.text
                    color: editorRenderButton.enabled ? "#10140F" : "#68716B"
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 10
                    font.weight: Font.Bold
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    radius: 7
                    color: editorRenderButton.enabled ? root.colors.acid : "#252C28"
                }
            }
            SubtitleEditorButton {
                colors: root.colors
                objectName: "editorBackButton"
                text: "メインへ戻る"
                onClicked: root.closeRequested()
            }
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            color: root.colors.border
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: 10
            spacing: 10
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 760
                spacing: 8
                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 220
                    radius: 10
                    color: "#060806"
                    border.color: root.colors.border
                    clip: true
                    VideoOutput {
                        id: editorVideo
                        anchors.fill: parent
                        anchors.bottomMargin: 54
                        fillMode: VideoOutput.PreserveAspectFit
                    }
                    SubtitleOverlay {
                        id: editorOverlay
                        objectName: "editorSubtitleOverlay"
                        anchors.fill: editorVideo
                        appBackend: root.appBackend
                        player: root.player
                        layoutMetrics: root.subtitleLayoutMetricsCache
                        active: root.active
                        captionObjectPrefix: "editorSubtitleOverlayCaption"
                        baseFontSize: root.selectedSubtitleFontSize
                        defaultSubtitleFontSize: root.defaultSubtitleFontSize
                        outlineColor: root.selectedSubtitleOutlineColor
                        outlineThickness: root.selectedSubtitleOutlineThickness
                        speakerColors: root.projectSpeakerCache
                        subtitleTextResolver: function (segmentData) {
                            return root.editorState.subtitlePreviewText(segmentData);
                        }
                        onActiveSegmentsChanged: {
                            if (root.selectionSyncReady)
                                root.selectionSyncRequested(editorOverlay.activeSegments);
                        }
                    }
                    ColumnLayout {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        anchors.margins: 8
                        spacing: 1
                        Slider {
                            id: editorSeek
                            Layout.fillWidth: true
                            from: 0
                            to: 1
                            onMoved: root.player.position = value
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            ToolButton {
                                text: root.player.playbackState === MediaPlayer.PlayingState ? "Ⅱ" : "▶"
                                onClicked: root.player.playbackState === MediaPlayer.PlayingState ? root.player.pause() : root.player.play()
                            }
                            Text {
                                Layout.fillWidth: true
                                text: root.formatTimestamp(root.player.position / 1000)
                                color: root.colors.textPrimary
                                font.family: "Cascadia Mono"
                                font.pixelSize: 11
                            }
                            Text {
                                text: editorOverlay.activeSegments.length + "件表示中"
                                color: root.colors.textMuted
                                font.pixelSize: 10
                            }
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    PanelTitle {
                        titleColor: root.colors.textMuted
                        text: "タイムライン"
                    }
                    Item {
                        Layout.fillWidth: true
                    }
                    Text {
                        text: "スナップ"
                        color: root.colors.textMuted
                        font.pixelSize: 9
                    }
                    SpinBox {
                        id: snapSpin
                        from: 0
                        to: 1000
                        stepSize: 10
                        value: root.editorState.snapMilliseconds
                        editable: true
                        onValueModified: root.editorState.snapMilliseconds = value
                    }
                    Text {
                        text: "ms"
                        color: root.colors.textMuted
                        font.pixelSize: 9
                    }
                    Text {
                        text: "表示倍率"
                        color: root.colors.textMuted
                        font.pixelSize: 9
                    }
                    Slider {
                        Layout.preferredWidth: 140
                        from: 16
                        to: 180
                        value: root.editorState.pixelsPerSecond
                        onMoved: root.editorState.pixelsPerSecond = value
                    }
                }
                SubtitleTimeline {
                    appBackend: root.appBackend
                    colors: root.colors
                    formatTimestamp: root.formatTimestamp
                    speakers: root.projectSpeakerCache
                    waveforms: root.appBackend.subtitles.subtitleWaveforms

                    objectName: "editorTimeline"
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(320, 90 + Math.max(1, root.projectSpeakerCache.length) * 42)
                    player: root.player
                    pixelsPerSecond: root.editorState.pixelsPerSecond
                    snapSeconds: root.editorState.snapMilliseconds / 1000
                    editable: true
                    Component.onCompleted: Qt.callLater(function () {
                        viewportX = root.editorState.timelineScrollX;
                    })
                    onViewportXChanged: root.editorState.timelineScrollX = viewportX
                    onSegmentActivated: function (index) {
                        var segment = root.appBackend.subtitles.segmentAt(index);
                        if (segment)
                            root.player.position = Number(segment.start) * 1000;
                    }
                }
            }

            Rectangle {
                Layout.preferredWidth: 620
                Layout.fillHeight: true
                radius: 10
                color: root.colors.panel
                border.color: root.colors.border
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 7
                    RowLayout {
                        Layout.fillWidth: true
                        PanelTitle {
                            titleColor: root.colors.textMuted
                            text: "話者ごとの字幕色"
                        }
                        Item {
                            Layout.fillWidth: true
                        }
                        Text {
                            text: "色を押して変更"
                            color: root.colors.textMuted
                            font.pixelSize: 8
                        }
                    }
                    ListView {
                        id: projectSpeakerColorList
                        objectName: "projectSpeakerColorList"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 36
                        orientation: ListView.Horizontal
                        spacing: 6
                        clip: true
                        model: root.projectSpeakerCache
                        delegate: Button {
                            id: projectSpeakerColorButton
                            required property int index
                            required property var modelData
                            width: 128
                            height: 34
                            enabled: !root.appBackend.running
                            onClicked: root.speakerColorRequested(index, modelData.color)
                            contentItem: Row {
                                spacing: 6
                                Rectangle {
                                    width: 20
                                    height: 20
                                    radius: 5
                                    color: projectSpeakerColorButton.modelData.color
                                    border.color: root.colors.textPrimary
                                }
                                Text {
                                    width: 94
                                    text: projectSpeakerColorButton.modelData.name
                                    color: root.colors.textPrimary
                                    elide: Text.ElideRight
                                    verticalAlignment: Text.AlignVCenter
                                }
                            }
                            background: Rectangle {
                                radius: 7
                                color: projectSpeakerColorButton.hovered ? "#27312C" : root.colors.raised
                                border.color: projectSpeakerColorButton.hovered ? root.colors.acid : root.colors.border
                            }
                            ToolTip.visible: hovered
                            ToolTip.text: modelData.name + " の字幕色を変更"
                        }
                        ScrollBar.horizontal: ScrollBar {
                            policy: ScrollBar.AsNeeded
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        PanelTitle {
                            titleColor: root.colors.textMuted
                            text: "字幕一覧"
                        }
                        Item {
                            Layout.fillWidth: true
                        }
                        Text {
                            text: "開始 / 終了 / 話者 / フォント / サイズ"
                            color: root.colors.textMuted
                            font.family: "Yu Gothic UI"
                            font.pixelSize: 8
                        }
                    }
                    ListView {
                        id: captionTable
                        objectName: "captionTable"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 5
                        function revealSelectedCaption() {
                            var selectedIndex = root.appBackend.subtitles.selectedSegmentIndex;
                            if (selectedIndex >= 0)
                                positionViewAtIndex(selectedIndex, ListView.Contain);
                        }
                        model: root.appBackend.subtitles.subtitleModel
                        // 行移動中の内部行番号を選択状態へ逆流させない。
                        currentIndex: -1
                        keyNavigationEnabled: false
                        Keys.onUpPressed: root.appBackend.subtitles.selectSegment(Math.max(0, root.appBackend.subtitles.selectedSegmentIndex - 1))
                        Keys.onDownPressed: root.appBackend.subtitles.selectSegment(Math.min(count - 1, root.appBackend.subtitles.selectedSegmentIndex + 1))
                        Component.onCompleted: Qt.callLater(function () {
                            contentY = root.editorState.captionScrollY;
                        })
                        onContentYChanged: root.editorState.captionScrollY = contentY
                        delegate: Rectangle {
                            id: captionRow
                            required property int index
                            objectName: "captionRow-" + index
                            required property string segmentId
                            required property real start
                            required property real end
                            required property string text
                            required property string editorText
                            required property string speaker
                            required property int layoutRow
                            required property real subtitleFontScale
                            required property string subtitleFontFamily
                            width: captionTable.width
                            height: 122
                            radius: 8
                            color: root.appBackend.subtitles.selectedSegmentIndex === index ? "#263326" : root.colors.raised
                            border.color: root.appBackend.subtitles.selectedSegmentIndex === index ? root.colors.acid : root.colors.border
                            MouseArea {
                                anchors.fill: parent
                                z: -1
                                onClicked: {
                                    root.appBackend.subtitles.selectSegment(captionRow.index);
                                    root.player.position = captionRow.start * 1000;
                                }
                            }
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 7
                                spacing: 5
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 5
                                    Text {
                                        text: String(captionRow.index + 1).padStart(4, "0")
                                        color: root.colors.textMuted
                                        font.family: "Cascadia Mono"
                                        font.pixelSize: 9
                                    }
                                    TimeField {
                                        textPrimary: root.colors.textPrimary
                                        borderColor: root.colors.border
                                        focusColor: root.colors.acid
                                        inputBackground: "#101512"
                                        Layout.preferredWidth: 72
                                        objectName: "captionStartTimeField"
                                        text: captionRow.start.toFixed(3)
                                        onEditingFinished: root.appBackend.subtitles.updateSegment(captionRow.index, {
                                            "start": Number(text)
                                        })
                                    }
                                    TimeField {
                                        textPrimary: root.colors.textPrimary
                                        borderColor: root.colors.border
                                        focusColor: root.colors.acid
                                        inputBackground: "#101512"
                                        Layout.preferredWidth: 72
                                        objectName: "captionEndTimeField"
                                        text: captionRow.end.toFixed(3)
                                        onEditingFinished: root.appBackend.subtitles.updateSegment(captionRow.index, {
                                            "end": Number(text)
                                        })
                                    }
                                    ComboBox {
                                        id: captionSpeakerCombo
                                        objectName: "captionSpeakerCombo"
                                        Layout.preferredWidth: 105
                                        model: root.projectSpeakerCache
                                        textRole: "name"
                                        valueRole: "style"
                                        function syncCurrentSpeaker() {
                                            currentIndex = indexOfValue(captionRow.speaker);
                                        }
                                        Component.onCompleted: syncCurrentSpeaker()
                                        onModelChanged: Qt.callLater(syncCurrentSpeaker)
                                        Connections {
                                            target: captionRow
                                            function onSpeakerChanged() { captionSpeakerCombo.syncCurrentSpeaker(); }
                                        }
                                        onActivated: root.appBackend.subtitles.updateSegment(captionRow.index, {
                                            "speaker": currentValue
                                        })
                                    }
                                    ComboBox {
                                        id: captionFontCombo
                                        objectName: "captionFontCombo"
                                        Layout.preferredWidth: 130
                                        model: root.appBackend.subtitles.fontChoices
                                        textRole: "label"
                                        valueRole: "family"
                                        function syncCurrentFont() {
                                            for (var i = 0; i < count; ++i) {
                                                if (valueAt(i) === captionRow.subtitleFontFamily) {
                                                    currentIndex = i;
                                                    return;
                                                }
                                            }
                                            currentIndex = 0;
                                        }
                                        Component.onCompleted: syncCurrentFont()
                                        Connections {
                                            target: captionRow
                                            function onSubtitleFontFamilyChanged() {
                                                captionFontCombo.syncCurrentFont();
                                            }
                                        }
                                        onActivated: root.appBackend.subtitles.updateSegment(captionRow.index, {
                                            "subtitle_font_family": currentValue
                                        })
                                    }
                                    CompactSpinBox {
                                        textPrimary: root.colors.textPrimary
                                        borderColor: root.colors.border
                                        focusColor: root.colors.acid
                                        inputBackground: "#101512"
                                        pressedBackground: "#303B35"
                                        selectedTextColor: "#10140F"
                                        objectName: "captionSizeSpin"
                                        Layout.preferredWidth: 106
                                        from: 50
                                        to: 200
                                        stepSize: 5
                                        value: Math.round(captionRow.subtitleFontScale * 100)
                                        onValueModified: root.appBackend.subtitles.updateSegment(captionRow.index, {
                                            "subtitle_font_scale": value / 100
                                        })
                                    }
                                    Text {
                                        text: "%"
                                        color: root.colors.textMuted
                                        font.pixelSize: 9
                                    }
                                }
                                TextArea {
                                    id: captionTextArea
                                    objectName: "captionTextArea"
                                    property string editingSegmentId: ""
                                    function commitText() {
                                        var id = editingSegmentId;
                                        editingSegmentId = "";
                                        if (id)
                                            root.editorState.commitSubtitleDraft(id);
                                    }
                                    Component.onDestruction: commitText()
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 52
                                    text: captionRow.editorText
                                    color: root.colors.textPrimary
                                    selectionColor: root.colors.acid
                                    font.family: captionRow.subtitleFontFamily || "Yu Gothic UI"
                                    font.pixelSize: 12
                                    wrapMode: TextEdit.Wrap
                                    selectByMouse: true
                                    onTextChanged: {
                                        if (activeFocus && editingSegmentId !== "")
                                            root.editorState.updateSubtitleDraft(captionRow.index, text);
                                    }
                                    onActiveFocusChanged: {
                                        if (activeFocus) {
                                            root.appBackend.subtitles.selectSegment(captionRow.index);
                                            editingSegmentId = captionRow.segmentId;
                                            root.editorState.beginSubtitleDraft(captionRow.index, text);
                                        } else {
                                            commitText();
                                        }
                                    }
                                    background: Rectangle {
                                        radius: 6
                                        color: "#101512"
                                        border.color: parent.activeFocus ? root.colors.acid : root.colors.border
                                    }
                                }
                            }
                        }
                        ScrollBar.vertical: ScrollBar {
                            policy: ScrollBar.AlwaysOn
                        }
                    }
                }
            }
        }
    }

    Rectangle {
        objectName: "editorEmptyState"
        anchors.centerIn: parent
        width: 360
        height: 112
        visible: root.appBackend.subtitles.segmentCount === 0
        z: 10
        radius: 10
        color: "#17201B"
        border.color: root.colors.border
        Column {
            anchors.centerIn: parent
            spacing: 8
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "字幕がありません"
                color: root.colors.textPrimary
                font.family: "Yu Gothic UI"
                font.pixelSize: 16
                font.weight: Font.Bold
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "上部の「+ 字幕追加」から手動で追加できます"
                color: root.colors.textMuted
                font.family: "Yu Gothic UI"
                font.pixelSize: 11
            }
        }
    }

    Connections {
        target: root.appBackend.subtitles
        function onSegmentsChanged() {
            Qt.callLater(function () {
                if (captionTable && typeof captionTable.revealSelectedCaption === "function")
                    captionTable.revealSelectedCaption();
            });
        }
        function onSelectionChanged() {
            Qt.callLater(function () {
                if (captionTable && typeof captionTable.revealSelectedCaption === "function")
                    captionTable.revealSelectedCaption();
            });
        }
    }
}
