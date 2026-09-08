import QtQuick
import QtQuick.Layouts

Rectangle {
    id: sidebar

    property var backend
    property bool wasAuthenticated: false
    property bool drawerMode: false
    signal closeRequested()

    objectName: "codexChatSidebarContainer"
    radius: 12
    color: "#121715"
    border.color: "#2A3530"

    Component.onCompleted: {
        wasAuthenticated = backend && backend.codexAuthState === "authenticated"
        if (wasAuthenticated)
            chatPanel.expanded = true
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            Text {
                objectName: "codexChatSidebarTitle"
                Layout.fillWidth: true
                Layout.maximumHeight: implicitHeight
                text: "Codex"
                color: "#F4F1E8"
                font.family: "Yu Gothic UI"
                font.pixelSize: 15
                font.weight: Font.Bold
            }
            SmallButton {
                objectName: "codexDrawerCloseButton"
                visible: sidebar.drawerMode
                text: "閉じる"
                onClicked: sidebar.closeRequested()
            }
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

    Connections {
        target: sidebar.backend

        function onCodexChatChanged() {
            var authenticated = sidebar.backend
                && sidebar.backend.codexAuthState === "authenticated"
            if (authenticated && !sidebar.wasAuthenticated)
                chatPanel.expanded = true
            else if (!authenticated)
                chatPanel.expanded = false
            sidebar.wasAuthenticated = authenticated
        }
    }
}
