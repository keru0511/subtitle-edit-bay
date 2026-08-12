pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: panelRoot
    objectName: "subtitleLineCountPanel"

    property int selectedSegmentIndex: -1
    property var segment: ({})
    property bool running: false
    property color panelColor: "#121715"
    property color raisedColor: "#19201D"
    property color borderColor: "#2A3530"
    property color textPrimaryColor: "#F4F1E8"
    property color textMutedColor: "#8E9B94"
    property color accentColor: "#C8FF3D"

    signal lineCountChanged(int segmentIndex, string lineCount)

    function normalizedLineCount(value) {
        var raw = value === undefined || value === null ? "auto" : String(value)
        if (raw === "1" || raw === "2")
            return raw
        return "auto"
    }

    function currentSegmentText() {
        if (!segment || segment.text === undefined || segment.text === null)
            return "セリフ未選択"
        var text = String(segment.text).replace(/\s+/g, " ")
        return text.length > 48 ? text.slice(0, 48) + "…" : text
    }

    function syncLineCount() {
        var value = normalizedLineCount(segment ? segment.subtitle_line_count : "auto")
        for (var i = 0; i < lineCountCombo.count; ++i) {
            if (lineCountCombo.valueAt(i) === value) {
                lineCountCombo.currentIndex = i
                return
            }
        }
        lineCountCombo.currentIndex = 0
    }

    onSegmentChanged: syncLineCount()
    onSelectedSegmentIndexChanged: syncLineCount()
    Component.onCompleted: syncLineCount()

    width: 240
    height: 126
    radius: 10
    color: panelRoot.panelColor
    border.color: panelRoot.borderColor
    opacity: selectedSegmentIndex >= 0 ? 1.0 : 0.7

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 7

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Text {
                text: "表示行数"
                color: panelRoot.textMutedColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 10
                font.weight: Font.Bold
                font.letterSpacing: 1.0
            }
            Item { Layout.fillWidth: true }
            Text {
                text: selectedSegmentIndex >= 0 ? "#" + String(selectedSegmentIndex + 1).padStart(4, "0") : "未選択"
                color: panelRoot.textPrimaryColor
                font.family: "Cascadia Mono"
                font.pixelSize: 9
            }
        }

        Text {
            Layout.fillWidth: true
            text: panelRoot.currentSegmentText()
            color: panelRoot.textPrimaryColor
            font.family: "Yu Gothic UI"
            font.pixelSize: 10
            elide: Text.ElideRight
        }

        ComboBox {
            id: lineCountCombo
            objectName: "subtitleLineCountCombo"
            Layout.fillWidth: true
            enabled: !panelRoot.running && panelRoot.selectedSegmentIndex >= 0
            textRole: "label"
            valueRole: "value"
            model: [
                {"label": "自動（既存ルール）", "value": "auto"},
                {"label": "1行", "value": "1"},
                {"label": "2行", "value": "2"}
            ]
            onActivated: panelRoot.lineCountChanged(panelRoot.selectedSegmentIndex, currentValue)
            contentItem: Text {
                leftPadding: 10
                rightPadding: lineCountCombo.indicator.width + lineCountCombo.spacing
                text: lineCountCombo.displayText
                color: lineCountCombo.enabled ? panelRoot.textPrimaryColor : panelRoot.textMutedColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 11
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
            background: Rectangle {
                radius: 7
                color: panelRoot.raisedColor
                border.color: lineCountCombo.activeFocus ? panelRoot.accentColor : panelRoot.borderColor
            }
        }

        Text {
            Layout.fillWidth: true
            text: "ASS更新・動画書き出し時に反映されます。"
            color: panelRoot.textMutedColor
            font.family: "Yu Gothic UI"
            font.pixelSize: 9
            wrapMode: Text.Wrap
        }
    }
}
