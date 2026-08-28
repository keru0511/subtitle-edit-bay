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
            Layout.maximumHeight: implicitHeight
            text: "Codex"
            color: "#F4F1E8"
            font.family: "Yu Gothic UI"
            font.pixelSize: 15
            font.weight: Font.Bold
        }
        Text {
            objectName: "codexChatSidebarSubtitle"
            Layout.maximumHeight: implicitHeight
            text: "チャット領域"
            color: "#8E9B94"
            font.family: "Yu Gothic UI"
            font.pixelSize: 10
        }
        CodexChatPanel {
            id: chatPanel
            objectName: "codexChatPanel"
            Layout.fillWidth: true
            Layout.fillHeight: expanded
            Layout.minimumHeight: expanded ? 180 : implicitHeight
            Layout.preferredHeight: implicitHeight
            Layout.maximumHeight: expanded ? sidebar.height : implicitHeight
            backend: sidebar.backend
            expanded: false
            panelColor: "#080A09"
        }
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: !chatPanel.expanded
        }
    }
}
