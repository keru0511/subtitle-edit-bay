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
    property bool activeTimeInputIncomplete: false
    property var activeTimeDelegate: null
    signal selected(int index)

    function commitPendingEdits() {
        return !clipListRoot.activeTimeDelegate
            || clipListRoot.activeTimeDelegate.commitTimeFields()
    }

    function refreshIncompleteTimeInput() {
        var delegate = clipListRoot.activeTimeDelegate
        clipListRoot.activeTimeInputIncomplete = Boolean(delegate && delegate.hasIncompleteTimeDrafts())
    }

    GridLayout {
        Layout.fillWidth: true
        columns: 4
        columnSpacing: 8
        rowSpacing: 8
        ComboBox {
            id: clipSourceCombo
            objectName: "shortModeClipSourceCombo"
            Layout.columnSpan: 2
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            Layout.preferredWidth: 126
            enabled: clipListRoot.appBackend && !clipListRoot.appBackend.running
            model: [
                { "label": "字幕セグメント", "value": "segment" },
                { "label": "時間範囲を直接指定", "value": "range" }
            ]
            textRole: "label"
            valueRole: "value"
            currentIndex: clipListRoot.appBackend
                && clipListRoot.appBackend.subtitles.segmentCount > 0 ? 0 : 1
        }
        ComboBox {
            id: segmentCombo
            objectName: "shortModeSegmentCombo"
            Layout.columnSpan: 2
            Layout.minimumWidth: 0
            Layout.fillWidth: true
            model: clipListRoot.appBackend ? clipListRoot.appBackend.subtitles.subtitleModel : null
            textRole: "text"
            valueRole: "segmentId"
            enabled: clipListRoot.appBackend && !clipListRoot.appBackend.running
                && clipSourceCombo.currentValue === "segment"
        }
        Text { text: "範囲"; color: "#8E9B94"; font.pixelSize: 10 }
        TimeField {
            id: rangeStartField
            objectName: "shortModeRangeStartField"
            Layout.preferredWidth: 76
            text: "0.000"
            enabled: clipListRoot.appBackend && !clipListRoot.appBackend.running
                && clipSourceCombo.currentValue === "range"
        }
        Text { text: "-"; color: "#8E9B94"; font.pixelSize: 10 }
        TimeField {
            id: rangeEndField
            objectName: "shortModeRangeEndField"
            Layout.preferredWidth: 76
            text: clipListRoot.appBackend && clipListRoot.appBackend.projectDuration > 0
                  ? Math.min(5, clipListRoot.appBackend.projectDuration).toFixed(3)
                  : "1.000"
            enabled: clipListRoot.appBackend && !clipListRoot.appBackend.running
                && clipSourceCombo.currentValue === "range"
        }
        Button {
            id: addButton
            objectName: "shortModeAddClipButton"
            Layout.columnSpan: 4
            Layout.fillWidth: true
            text: "ショートに追加"
            enabled: clipListRoot.appBackend && !clipListRoot.appBackend.running
                && !clipListRoot.activeTimeInputIncomplete && (
                (clipSourceCombo.currentValue === "segment"
                    && segmentCombo.currentValue !== undefined && segmentCombo.currentValue !== "")
                || (clipSourceCombo.currentValue === "range"
                    && Number(rangeStartField.text) >= 0
                    && Number(rangeEndField.text) > Number(rangeStartField.text)
                    && (clipListRoot.appBackend.projectDuration <= 0
                        || Number(rangeEndField.text) <= clipListRoot.appBackend.projectDuration))
            )
            onClicked: {
                if (clipListRoot.appBackend && clipListRoot.commitPendingEdits()) {
                    if (clipSourceCombo.currentValue === "range") {
                        clipListRoot.appBackend.shortVideo.addShortVideoClipByRange(
                            Number(rangeStartField.text), Number(rangeEndField.text))
                    } else if (segmentCombo.currentValue !== undefined && segmentCombo.currentValue !== "") {
                        clipListRoot.appBackend.shortVideo.addShortVideoClip(segmentCombo.currentValue)
                    }
                }
            }
            contentItem: Text {
                text: addButton.text
                color: addButton.enabled ? "#FFFFFF" : "#8B949E"
                font.family: "Yu Gothic UI"
                font.pixelSize: 12
                font.weight: Font.Bold
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                radius: 8
                color: addButton.enabled ? (addButton.down ? "#4F46E5" : (addButton.hovered ? "#818CF8" : "#6366F1")) : "#21262D"
                border.color: addButton.enabled ? "#6366F1" : "#30363D"
            }
        }
    }

    ListView {
        id: clipListView
        objectName: "shortModeClipListView"
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        model: clipListRoot.appBackend ? clipListRoot.appBackend.shortVideo.shortVideoClipModel : null
        spacing: 6

        delegate: Rectangle {
            id: clipItem
            required property int index
            required property var clipData
            property bool committingTimeFields: false
            objectName: "shortModeClipItem" + index
            width: clipListView.width
            height: 174
            color: clipListRoot.selectedIndex === index ? "#21262D" : "#161B22"
            border.color: clipListRoot.selectedIndex === index ? "#6366F1" : "#30363D"
            radius: 8

            function hasIncompleteTimeDrafts() {
                return (startTimeField.draftEdited && !startTimeField.draftAcceptable)
                    || (endTimeField.draftEdited && !endTimeField.draftAcceptable)
            }

            function commitTimeFields() {
                if (clipItem.committingTimeFields)
                    return true
                var startEdited = startTimeField.draftEdited
                var endEdited = endTimeField.draftEdited
                if (!startEdited && !endEdited) {
                    if (clipListRoot.activeTimeDelegate === clipItem)
                        clipListRoot.activeTimeDelegate = null
                    clipListRoot.refreshIncompleteTimeInput()
                    return true
                }
                if ((startEdited && !startTimeField.draftAcceptable)
                        || (endEdited && !endTimeField.draftAcceptable))
                    return false
                var changes = {}
                if (startEdited) changes.start = Number(startTimeField.draftText)
                if (endEdited) changes.end = Number(endTimeField.draftText)
                clipItem.committingTimeFields = true
                try {
                    startTimeField.draftEdited = false
                    endTimeField.draftEdited = false
                    if (clipListRoot.activeTimeDelegate === clipItem)
                        clipListRoot.activeTimeDelegate = null
                    clipListRoot.refreshIncompleteTimeInput()
                    startTimeField.focus = false
                    endTimeField.focus = false
                    var accepted = clipListRoot.appBackend
                        && clipListRoot.appBackend.shortVideo.updateShortVideoClip(clipItem.index, changes)
                    if (!accepted) {
                        startTimeField.text = Number(clipItem.clipData.start).toFixed(3)
                        endTimeField.text = Number(clipItem.clipData.end).toFixed(3)
                    }
                    return accepted
                } finally {
                    clipItem.committingTimeFields = false
                }
            }

            Component.onDestruction: {
                if (clipListRoot.activeTimeDelegate === clipItem)
                    clipListRoot.activeTimeDelegate = null
                clipListRoot.refreshIncompleteTimeInput()
            }

            MouseArea {
                anchors.fill: parent
                onClicked: clipListRoot.selected(index)
            }

            GridLayout {
                columns: 2
                anchors.fill: parent
                anchors.margins: 8
                columnSpacing: 8
                rowSpacing: 8

                ColumnLayout {
                    Layout.columnSpan: 2
                    Layout.fillWidth: true
                    spacing: 4
                    Text {
                        text: clipItem.clipData.preview_text || clipItem.clipData.text || ""
                        color: "#F4F1E8"
                        font.family: "Yu Gothic UI"
                        font.pixelSize: 12
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                    Text {
                        text: (clipItem.clipData.speaker || "話者なし") + "  "
                            + clipItem.clipData.start.toFixed(2) + " - "
                            + clipItem.clipData.end.toFixed(2)
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
                            enabled: clipListRoot.appBackend && !clipListRoot.appBackend.running
                            text: Number(clipItem.clipData.start).toFixed(3)
                            property bool draftEdited: false
                            property string draftText: ""
                            property bool draftAcceptable: true
                            onTextEdited: {
                                draftEdited = true
                                draftText = text
                                draftAcceptable = acceptableInput
                                clipListRoot.activeTimeDelegate = clipItem
                                clipListRoot.refreshIncompleteTimeInput()
                            }
                            onAcceptableInputChanged: {
                                if (draftEdited) draftAcceptable = acceptableInput
                                clipListRoot.refreshIncompleteTimeInput()
                            }
                            onActiveFocusChanged: clipListRoot.refreshIncompleteTimeInput()
                            onEditingFinished: {
                                if (clipItem.committingTimeFields) return
                                if (!draftEdited && activeFocus
                                        && Number(text) !== Number(clipItem.clipData.start)) {
                                    draftEdited = true
                                    draftText = text
                                    draftAcceptable = acceptableInput
                                }
                                clipItem.commitTimeFields()
                            }
                            Binding {
                                target: startTimeField
                                property: "text"
                                value: startTimeField.draftEdited
                                    ? startTimeField.draftText
                                    : Number(clipItem.clipData.start).toFixed(3)
                                when: !startTimeField.activeFocus
                            }
                        }
                        Text { text: "終了"; color: "#8E9B94"; font.pixelSize: 10 }
                        TimeField {
                            id: endTimeField
                            objectName: "shortModeEndTimeField" + index
                            Layout.preferredWidth: 82
                            enabled: clipListRoot.appBackend && !clipListRoot.appBackend.running
                            text: Number(clipItem.clipData.end).toFixed(3)
                            property bool draftEdited: false
                            property string draftText: ""
                            property bool draftAcceptable: true
                            onTextEdited: {
                                draftEdited = true
                                draftText = text
                                draftAcceptable = acceptableInput
                                clipListRoot.activeTimeDelegate = clipItem
                                clipListRoot.refreshIncompleteTimeInput()
                            }
                            onAcceptableInputChanged: {
                                if (draftEdited) draftAcceptable = acceptableInput
                                clipListRoot.refreshIncompleteTimeInput()
                            }
                            onActiveFocusChanged: clipListRoot.refreshIncompleteTimeInput()
                            onEditingFinished: {
                                if (clipItem.committingTimeFields) return
                                if (!draftEdited && activeFocus
                                        && Number(text) !== Number(clipItem.clipData.end)) {
                                    draftEdited = true
                                    draftText = text
                                    draftAcceptable = acceptableInput
                                }
                                clipItem.commitTimeFields()
                            }
                            Binding {
                                target: endTimeField
                                property: "text"
                                value: endTimeField.draftEdited
                                    ? endTimeField.draftText
                                    : Number(clipItem.clipData.end).toFixed(3)
                                when: !endTimeField.activeFocus
                            }
                        }
                    }
                }

                ComboBox {
                    id: fitCombo
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    objectName: "shortModeFitCombo" + index
                    model: clipListRoot.fitOptions
                    textRole: "label"
                    valueRole: "value"
                    enabled: clipListRoot.appBackend && !clipListRoot.appBackend.running
                        && !clipListRoot.activeTimeInputIncomplete
                    currentIndex: clipListRoot.indexForFit(clipItem.clipData.fit)
                    onActivated: function(_controlIndex) {
                        if (clipListRoot.appBackend && clipListRoot.commitPendingEdits()) {
                            clipListRoot.appBackend.shortVideo.updateShortVideoClip(
                                clipItem.index,
                                {"fit": fitCombo.currentValue}
                            )
                        }
                    }
                }

                RowLayout {
                    spacing: 2
                    Button {
                        objectName: "shortModeMoveUpButton" + index
                        text: "▲"
                        enabled: clipListRoot.appBackend && !clipListRoot.appBackend.running
                            && !clipListRoot.activeTimeInputIncomplete && index > 0
                        onClicked: {
                            if (clipListRoot.appBackend && clipListRoot.commitPendingEdits()) {
                                clipListRoot.appBackend.shortVideo.moveShortVideoClip(index, index - 1)
                            }
                        }
                    }
                    Button {
                        objectName: "shortModeMoveDownButton" + index
                        text: "▼"
                        enabled: clipListRoot.appBackend && !clipListRoot.appBackend.running
                            && !clipListRoot.activeTimeInputIncomplete
                            && index < clipListView.count - 1
                        onClicked: {
                            if (clipListRoot.appBackend && clipListRoot.commitPendingEdits()) {
                                clipListRoot.appBackend.shortVideo.moveShortVideoClip(index, index + 2)
                            }
                        }
                    }
                }

                Button {
                    objectName: "shortModeDeleteButton" + index
                    text: "✕"
                    enabled: clipListRoot.appBackend && !clipListRoot.appBackend.running
                        && !clipListRoot.activeTimeInputIncomplete
                    onClicked: {
                        if (clipListRoot.appBackend && clipListRoot.commitPendingEdits()) {
                            clipListRoot.appBackend.shortVideo.removeShortVideoClip(index)
                        }
                    }
                }
            }
        }
    }
}
