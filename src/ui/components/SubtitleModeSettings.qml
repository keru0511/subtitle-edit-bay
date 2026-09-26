pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    required property var backend
    property var speakers: []
    property var fontChoices: []
    property color panelColor: "#161B22"
    property color raisedColor: "#21262D"
    property color borderColor: "#30363D"
    property color textColor: "#F0F6FC"
    property color mutedColor: "#8B949E"
    property color accentColor: "#6366F1"
    property real savedContentY: 0
    property bool restoringContentY: true
    property var selectedSegment: ({})
    property var beginDraft: null
    property var updateDraft: null
    property var commitDraft: null
    property var pendingDraftTextForSegment: null
    signal seekRequested(real positionMilliseconds)
    signal speakerColorRequested(int speakerIndex, string currentColor)
    signal contentYChangedByUser(real value)

    function speakerIndex(style) {
        for (var index = 0; index < root.speakers.length; ++index) {
            if (String(root.speakers[index].style) === String(style))
                return index
        }
        return -1
    }

    function selectComboValue(combo, value) {
        for (var index = 0; index < combo.count; ++index) {
            if (String(combo.valueAt(index)) === String(value)) {
                combo.currentIndex = index
                return
            }
        }
        combo.currentIndex = combo.count > 0 ? 0 : -1
    }

    function refreshSelectedEditor() {
        root.selectedSegment = root.backend.subtitles.segmentAt(root.backend.subtitles.selectedSegmentIndex)
        syncEditorTimer.restart()
    }

    function segmentIdAt(index) {
        if (index < 0)
            return ""
        var segment = root.backend.subtitles.segmentAt(index)
        return segment && segment.id !== undefined ? String(segment.id) : ""
    }

    function segmentIndexForId(segmentId) {
        if (!segmentId)
            return -1
        for (var index = 0; index < root.backend.subtitles.segmentCount; ++index) {
            if (root.segmentIdAt(index) === segmentId)
                return index
        }
        return -1
    }

    function beginTimeEdit(field) {
        field.editingSegmentIndex = root.backend.subtitles.selectedSegmentIndex
        field.editingSegmentId = root.segmentIdAt(field.editingSegmentIndex)
    }

    function restoreSelection(selectedIndex, selectedId, editedId) {
        if (selectedIndex < 0) {
            root.backend.subtitles.selectSegment(-1)
            return
        }
        if (selectedId === editedId)
            return
        var restoreIndex = root.segmentIndexForId(selectedId)
        if (restoreIndex >= 0)
            root.backend.subtitles.selectSegment(restoreIndex)
    }

    function commitTimeEdit(field, propertyName) {
        var editedId = field.editingSegmentId
        var editIndex = editedId
            ? root.segmentIndexForId(editedId)
            : field.editingSegmentIndex
        var selectedIndex = root.backend.subtitles.selectedSegmentIndex
        var selectedId = root.segmentIdAt(selectedIndex)
        if (editIndex >= 0 && field.acceptableInput) {
            var changes = ({})
            changes[propertyName] = Number(field.text)
            root.backend.subtitles.updateSegment(editIndex, changes)
            root.restoreSelection(selectedIndex, selectedId, editedId)
        }
        field.editingSegmentIndex = -1
        field.editingSegmentId = ""
    }

    function syncEditorFields() {
        var segment = root.selectedSegment || ({})
        if (!startField.activeFocus)
            startField.text = segment.start === undefined ? "" : Number(segment.start).toFixed(3)
        if (!endField.activeFocus)
            endField.text = segment.end === undefined ? "" : Number(segment.end).toFixed(3)
        if (!captionText.activeFocus) {
            var storedText = String(segment.editorText || segment.text || "")
            captionText.text = root.pendingDraftTextForSegment
                ? root.pendingDraftTextForSegment(segment.id || "", storedText) : storedText
        }
        root.selectComboValue(speakerCombo, segment.speaker || "")
        root.selectComboValue(fontCombo, segment.subtitle_font_family || "")
        sizeSpin.value = Math.round(Number(segment.subtitle_font_scale || 1) * 100)
    }

    function commitCaptionText() {
        var id = captionText.editingSegmentId
        captionText.editingSegmentIndex = -1
        captionText.editingSegmentId = ""
        if (id && root.commitDraft)
            root.commitDraft(id)
    }

    Component.onCompleted: refreshSelectedEditor()
    Component.onDestruction: commitCaptionText()

    Connections {
        target: root.backend.subtitles
        function onSelectionChanged() { root.refreshSelectedEditor() }
        function onSegmentsChanged() { root.refreshSelectedEditor() }
    }

    Timer {
        id: syncEditorTimer
        interval: 0
        repeat: false
        onTriggered: root.syncEditorFields()
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 7

        RowLayout {
            Layout.fillWidth: true
            Text { text: "字幕設定"; color: root.textColor; font.family: "Yu Gothic UI"; font.pixelSize: 13; font.weight: Font.Bold }
            Item { Layout.fillWidth: true }
            Text { text: root.backend.subtitles.segmentCount + "件"; color: root.accentColor; font.family: "Yu Gothic UI"; font.pixelSize: 9 }
        }

        ListView {
            id: subtitleList
            objectName: "workspaceSubtitleList"
            interactive: !root.backend.running
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(190, Math.max(76, root.height * 0.3))
            clip: true
            spacing: 5
            boundsBehavior: Flickable.StopAtBounds
            model: root.backend.subtitles.subtitleModel
            currentIndex: root.backend.subtitles.selectedSegmentIndex
            onContentYChanged: {
                if (!root.restoringContentY)
                    root.contentYChangedByUser(contentY)
            }

            Timer {
                interval: 0
                running: true
                repeat: false
                onTriggered: {
                    subtitleList.contentY = root.savedContentY
                    root.restoringContentY = false
                }
            }

            delegate: Rectangle {
                id: subtitleRow
                required property int index
                required property real start
                required property real end
                required property string editorText
                width: subtitleList.width
                height: 46
                radius: 7
                color: root.backend.subtitles.selectedSegmentIndex === subtitleRow.index ? "#263326" : root.raisedColor
                border.color: root.backend.subtitles.selectedSegmentIndex === subtitleRow.index ? root.accentColor : root.borderColor
                Column {
                    anchors.fill: parent
                    anchors.margins: 6
                    spacing: 2
                    Text { width: parent.width; text: String(subtitleRow.index + 1).padStart(3, "0") + "  " + subtitleRow.start.toFixed(2) + "–" + subtitleRow.end.toFixed(2); color: root.mutedColor; font.family: "Cascadia Mono"; font.pixelSize: 8 }
                    Text { width: parent.width; text: subtitleRow.editorText; color: root.textColor; font.family: "Yu Gothic UI"; font.pixelSize: 9; elide: Text.ElideRight }
                }
                MouseArea {
                    anchors.fill: parent
                    enabled: !root.backend.running
                    onClicked: {
                        root.backend.subtitles.selectSegment(subtitleRow.index)
                        root.seekRequested(subtitleRow.start * 1000)
                    }
                }
            }
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            Text {
                anchors.centerIn: parent
                visible: root.backend.subtitles.segmentCount === 0
                text: "字幕はまだありません\n下部の「字幕追加」から作成できます"
                color: root.mutedColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 9
                horizontalAlignment: Text.AlignHCenter
            }
        }

        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.borderColor }
        Text { text: root.backend.subtitles.selectedSegmentIndex >= 0 ? "選択中の字幕" : "字幕を選択してください"; color: root.textColor; font.family: "Yu Gothic UI"; font.pixelSize: 10; font.weight: Font.DemiBold }

        Flickable {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: width
            contentHeight: selectedEditor.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            visible: root.backend.subtitles.selectedSegmentIndex >= 0

            ColumnLayout {
                id: selectedEditor
                width: parent.width
                spacing: 6
                RowLayout {
                    Layout.fillWidth: true
                    TimeField {
                        id: startField
                        objectName: "workspaceSubtitleStartField"
                        enabled: !root.backend.running
                        property int editingSegmentIndex: -1
                        property string editingSegmentId: ""
                        Layout.fillWidth: true
                        placeholderText: "開始"
                        onActiveFocusChanged: {
                            if (activeFocus)
                                root.beginTimeEdit(startField)
                        }
                        onEditingFinished: root.commitTimeEdit(startField, "start")
                    }
                    TimeField {
                        id: endField
                        objectName: "workspaceSubtitleEndField"
                        enabled: !root.backend.running
                        property int editingSegmentIndex: -1
                        property string editingSegmentId: ""
                        Layout.fillWidth: true
                        placeholderText: "終了"
                        onActiveFocusChanged: {
                            if (activeFocus)
                                root.beginTimeEdit(endField)
                        }
                        onEditingFinished: root.commitTimeEdit(endField, "end")
                    }
                }
                ComboBox {
                    id: speakerCombo
                    objectName: "workspaceSubtitleSpeakerCombo"
                    enabled: !root.backend.running
                    Layout.fillWidth: true
                    model: root.speakers
                    textRole: "name"
                    valueRole: "style"
                    onActivated: root.backend.subtitles.updateSegment(root.backend.subtitles.selectedSegmentIndex, {"speaker": currentValue})
                }
                RowLayout {
                    Layout.fillWidth: true
                    ComboBox {
                        id: fontCombo
                        objectName: "workspaceSubtitleFontCombo"
                        enabled: !root.backend.running
                        Layout.fillWidth: true
                        model: root.fontChoices
                        textRole: "label"
                        valueRole: "family"
                        onActivated: root.backend.subtitles.updateSegment(root.backend.subtitles.selectedSegmentIndex, {"subtitle_font_family": currentValue})
                    }
                    Button {
                        objectName: "workspaceSubtitleSpeakerColorButton"
                        Layout.preferredWidth: 30
                        Layout.preferredHeight: 30
                        enabled: !root.backend.running && root.speakerIndex(root.selectedSegment.speaker || "") >= 0
                        onClicked: {
                            var index = root.speakerIndex(root.selectedSegment.speaker || "")
                            if (index >= 0)
                                root.speakerColorRequested(index, String(root.speakers[index].color || "#FFFFFF"))
                        }
                        contentItem: Rectangle {
                            radius: 5
                            color: {
                                var index = root.speakerIndex(root.selectedSegment.speaker || "")
                                return index >= 0 ? root.speakers[index].color : root.mutedColor
                            }
                            border.color: root.textColor
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: "文字サイズ"; color: root.mutedColor; font.family: "Yu Gothic UI"; font.pixelSize: 9 }
                    CompactSpinBox {
                        id: sizeSpin
                        objectName: "workspaceSubtitleSizeSpin"
                        enabled: !root.backend.running
                        Layout.fillWidth: true
                        from: 50
                        to: 200
                        stepSize: 5
                        onValueModified: root.backend.subtitles.updateSegment(root.backend.subtitles.selectedSegmentIndex, {"subtitle_font_scale": value / 100})
                    }
                    Text { text: "%"; color: root.mutedColor; font.pixelSize: 9 }
                }
                TextArea {
                    id: captionText
                    objectName: "workspaceSubtitleTextArea"
                    enabled: !root.backend.running
                    property int editingSegmentIndex: -1
                    property string editingSegmentId: ""
                    Layout.fillWidth: true
                    Layout.preferredHeight: 92
                    color: root.textColor
                    selectionColor: root.accentColor
                    font.family: root.selectedSegment.subtitle_font_family || "Yu Gothic UI"
                    font.pixelSize: 11
                    wrapMode: TextEdit.Wrap
                    selectByMouse: true
                    onTextChanged: {
                        if (activeFocus && editingSegmentIndex >= 0 && root.updateDraft)
                            root.updateDraft(editingSegmentIndex, text)
                    }
                    onActiveFocusChanged: {
                        if (activeFocus) {
                            editingSegmentIndex = root.backend.subtitles.selectedSegmentIndex
                            editingSegmentId = root.segmentIdAt(editingSegmentIndex)
                            if (root.beginDraft && editingSegmentIndex >= 0)
                                root.beginDraft(editingSegmentIndex, text)
                        } else if (editingSegmentIndex >= 0) {
                            root.commitCaptionText()
                        }
                    }
                    background: Rectangle { radius: 6; color: "#101512"; border.color: captionText.activeFocus ? root.accentColor : root.borderColor }
                }
            }
        }
    }
}
