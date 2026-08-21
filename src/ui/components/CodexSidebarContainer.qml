import QtQuick
import QtQuick.Layouts

Rectangle {
    id: sidebar

    property var backend

    objectName: "codexChatSidebarContainer"
    radius: 12
    color: "#121715"
    border.color: "#2A3530"

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 8

        Text {
            objectName: "codexChatSidebarTitle"
            text: "Codex"
            color: "#F4F1E8"
            font.family: "Yu Gothic UI"
            font.pixelSize: 15
            font.weight: Font.Bold
        }
        Text {
            text: "チャット領域"
            color: "#8E9B94"
            font.family: "Yu Gothic UI"
            font.pixelSize: 10
        }
        CodexChatPanel {
            objectName: "codexChatPanel"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 180
            backend: sidebar.backend
            expanded: false
            panelColor: "#080A09"
        }
    }
}
