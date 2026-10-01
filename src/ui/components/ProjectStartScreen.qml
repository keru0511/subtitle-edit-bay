pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root
    required property var appBackend
    required property var colors
    required property string transcriptionBlockReason
    signal newVideoEditRequested
    signal openProjectRequested
    signal startTranscriptionRequested
    signal sourceSettingsRequested
    signal dictionaryRequested
    signal processingSettingsRequested

    objectName: "projectStartScreen"
    radius: 14
    color: root.colors.panel
    border.color: root.colors.border

    ColumnLayout {
        anchors.centerIn: parent
        width: Math.min(640, parent.width - 64)
        spacing: 14

        Text {
            Layout.fillWidth: true
            text: "編集を始める"
            color: root.colors.textPrimary
            font.family: "Yu Gothic UI"
            font.pixelSize: 28
            font.weight: Font.Bold
            horizontalAlignment: Text.AlignHCenter
        }
        Text {
            Layout.fillWidth: true
            text: "文字起こしをしなくても、動画を選ぶだけで字幕・カット・音量の編集を始められます"
            color: root.colors.textMuted
            font.family: "Yu Gothic UI"
            font.pixelSize: 11
            wrapMode: Text.Wrap
            horizontalAlignment: Text.AlignHCenter
        }
        Rectangle {
            objectName: "startScreenStatusPanel"
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? 58 : 0
            visible: root.appBackend.stage === "ERROR" || root.appBackend.stage === "CHECK"
            radius: 8
            color: root.appBackend.stage === "ERROR" ? "#321C1C" : "#302A1C"
            border.color: root.appBackend.stage === "ERROR" ? root.colors.danger : root.colors.amber
            Text {
                objectName: "startScreenStatusText"
                anchors.fill: parent
                anchors.margins: 10
                text: root.appBackend.status
                color: root.colors.textPrimary
                font.family: "Yu Gothic UI"
                font.pixelSize: 10
                wrapMode: Text.Wrap
                verticalAlignment: Text.AlignVCenter
            }
        }
        Item {
            Layout.preferredHeight: 4
        }
        Button {
            id: newVideoEditButtonControl
            objectName: "newVideoEditButton"
            Layout.fillWidth: true
            Layout.preferredHeight: 54
            text: "新しい動画を編集"
            enabled: !root.appBackend.running
            onClicked: root.newVideoEditRequested()
            contentItem: Text {
                text: newVideoEditButtonControl.text
                color: "#10140F"
                font.family: "Yu Gothic UI"
                font.pixelSize: 15
                font.weight: Font.Bold
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                radius: 10
                color: newVideoEditButtonControl.enabled ? root.colors.acid : "#465044"
            }
        }
        Button {
            objectName: "startScreenOpenProjectButton"
            Layout.fillWidth: true
            Layout.preferredHeight: 48
            text: "プロジェクトを開く"
            enabled: !root.appBackend.running
            onClicked: root.openProjectRequested()
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            color: root.colors.border
        }
        Text {
            text: "必要に応じて"
            color: root.colors.textMuted
            font.family: "Yu Gothic UI"
            font.pixelSize: 10
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Button {
                objectName: "startWithTranscriptionButton"
                Layout.fillWidth: true
                text: "文字起こしから始める"
                enabled: !root.appBackend.running
                onClicked: root.startTranscriptionRequested()
                ToolTip.visible: hovered && root.transcriptionBlockReason.length > 0
                ToolTip.text: root.transcriptionBlockReason
            }
            Button {
                objectName: "startScreenSourceSetupButton"
                Layout.fillWidth: true
                text: "素材設定"
                enabled: !root.appBackend.running
                onClicked: root.sourceSettingsRequested()
            }
            Button {
                objectName: "startScreenDictionaryButton"
                Layout.fillWidth: true
                text: "文字起こし辞書"
                enabled: !root.appBackend.running
                onClicked: root.dictionaryRequested()
            }
            Button {
                objectName: "startScreenSettingsButton"
                Layout.fillWidth: true
                text: "処理設定"
                enabled: !root.appBackend.running
                onClicked: root.processingSettingsRequested()
            }
        }
        Text {
            objectName: "startScreenTranscriptionBlockReason"
            Layout.fillWidth: true
            visible: root.transcriptionBlockReason.length > 0
            text: root.transcriptionBlockReason
            color: root.colors.amber
            font.family: "Yu Gothic UI"
            font.pixelSize: 10
            wrapMode: Text.Wrap
            horizontalAlignment: Text.AlignHCenter
        }
        Text {
            Layout.fillWidth: true
            text: root.appBackend.sourceSelection.video ? "選択中: " + root.appBackend.sourceSelection.video : "既存の .subtitle-project.json もそのまま開けます"
            color: root.colors.textMuted
            font.family: "Yu Gothic UI"
            font.pixelSize: 9
            elide: Text.ElideMiddle
            horizontalAlignment: Text.AlignHCenter
        }
    }
}
