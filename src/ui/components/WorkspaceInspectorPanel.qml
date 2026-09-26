pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root

    required property var colors
    required property bool projectLoaded
    required property string selectedTab
    required property Component settingsContent
    readonly property int tabBarHeight: inspectorTabBar.height

    signal tabRequested(string tab)

    objectName: "modeSettingsSlot"
    visible: root.projectLoaded
    radius: 12
    color: root.colors.panel
    border.color: root.colors.border
    clip: true

    Rectangle {
        id: inspectorTabBar
        objectName: "inspectorTabBar"
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 44
        color: root.colors.panel
        border.color: root.colors.border

        RowLayout {
            anchors.fill: parent
            anchors.margins: 6
            spacing: 4

            Button {
                id: inspectorSettingsTabButton
                objectName: "inspectorSettingsTabButton"
                Layout.fillWidth: true
                Layout.fillHeight: true
                text: "編集プロパティ"
                onClicked: root.tabRequested("settings")
                contentItem: Text {
                    text: inspectorSettingsTabButton.text
                    color: root.selectedTab === "settings" ? "#FFFFFF" : root.colors.textMuted
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 10
                    font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    radius: 7
                    color: root.selectedTab === "settings" ? root.colors.raised : "transparent"
                    border.color: root.selectedTab === "settings" ? root.colors.border : "transparent"
                }
            }
            Button {
                id: inspectorCodexTabButton
                objectName: "inspectorCodexTabButton"
                Layout.fillWidth: true
                Layout.fillHeight: true
                text: "AI Codex"
                onClicked: root.tabRequested("codex")
                contentItem: Text {
                    text: inspectorCodexTabButton.text
                    color: root.selectedTab === "codex" ? "#FFFFFF" : root.colors.textMuted
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 10
                    font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    radius: 7
                    color: root.selectedTab === "codex" ? root.colors.raised : "transparent"
                    border.color: root.selectedTab === "codex" ? root.colors.border : "transparent"
                }
            }
        }
    }

    Loader {
        id: modeSettingsContentLoader
        objectName: "modeSettingsContentLoader"
        anchors.top: inspectorTabBar.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        visible: root.selectedTab === "settings"
        active: root.projectLoaded && root.settingsContent !== null
        sourceComponent: root.settingsContent
    }
}
