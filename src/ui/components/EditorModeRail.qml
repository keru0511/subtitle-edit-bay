pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: rail

    required property string currentMode
    required property var capabilities
    required property color panelColor
    required property color raisedColor
    required property color borderColor
    required property color textColor
    required property color mutedColor
    required property color accentColor
    signal modeRequested(string mode)

    radius: 12
    color: rail.panelColor
    border.color: rail.borderColor

    function modeEnabled(mode) {
        if (mode === "subtitle")
            return Boolean(rail.capabilities.canEditSubtitles)
        if (mode === "cut")
            return Boolean(rail.capabilities.canCut)
        return Boolean(rail.capabilities.canMixAudio)
    }

    function modeReason(mode) {
        if (mode === "subtitle")
            return String(rail.capabilities.subtitleReason || "")
        if (mode === "cut")
            return String(rail.capabilities.cutReason || "")
        return String(rail.capabilities.audioReason || "")
    }

    component ModeButton: Button {
        id: modeControl
        required property string mode
        required property string label
        required property string mark

        Layout.fillWidth: true
        Layout.preferredHeight: 62
        enabled: rail.modeEnabled(modeControl.mode)
        opacity: enabled ? 1 : 0.48
        onClicked: rail.modeRequested(modeControl.mode)

        contentItem: Column {
            spacing: 3
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: modeControl.mark
                color: rail.currentMode === modeControl.mode ? "#10140F" : rail.textColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 16
                font.weight: Font.Bold
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: modeControl.label
                color: rail.currentMode === modeControl.mode ? "#10140F" : rail.textColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 9
            }
        }
        background: Rectangle {
            radius: 8
            color: rail.currentMode === modeControl.mode ? rail.accentColor : rail.raisedColor
            border.color: rail.currentMode === modeControl.mode ? rail.accentColor : rail.borderColor
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 8
        spacing: 8

        Text {
            Layout.fillWidth: true
            text: "編集"
            color: rail.mutedColor
            font.family: "Yu Gothic UI"
            font.pixelSize: 10
            horizontalAlignment: Text.AlignHCenter
        }

        ModeButton { objectName: "editorModeButton-subtitle"; mode: "subtitle"; label: "字幕"; mark: "字" }
        ModeButton { objectName: "editorModeButton-cut"; mode: "cut"; label: "カット"; mark: "✂" }
        Text { Layout.fillWidth: true; visible: !rail.capabilities.canCut; text: "準備中"; color: rail.mutedColor; font.family: "Yu Gothic UI"; font.pixelSize: 8; horizontalAlignment: Text.AlignHCenter }
        ModeButton { objectName: "editorModeButton-audio"; mode: "audio"; label: "音量"; mark: "音" }
        Text { Layout.fillWidth: true; visible: !rail.capabilities.canMixAudio; text: "利用不可"; color: rail.mutedColor; font.family: "Yu Gothic UI"; font.pixelSize: 8; horizontalAlignment: Text.AlignHCenter }

        Item { Layout.fillHeight: true }

        Text {
            Layout.fillWidth: true
            text: "1つの再生位置を\n全モードで共有"
            color: rail.mutedColor
            font.family: "Yu Gothic UI"
            font.pixelSize: 8
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
        }
    }
}
