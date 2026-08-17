import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: shortRoot
    objectName: "shortModeScreen"
    anchors.fill: parent

    property var mainRoot: null
    property var appBackend: backend

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 14

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: "ショート動画作成"
                color: "#E8EFEA"
                font.family: "Yu Gothic UI"
                font.pixelSize: 18
                font.weight: Font.Bold
            }
            Item { Layout.fillWidth: true }
            Button {
                id: shortModeBackButton
                objectName: "shortModeBackButton"
                implicitHeight: 32
                enabled: shortRoot.mainRoot !== null && !shortRoot.appBackend.running
                text: "メインへ戻る"
                onClicked: shortRoot.mainRoot.closeShortModeScreen()
                contentItem: Text {
                    text: shortModeBackButton.text
                    color: shortModeBackButton.enabled ? "#F4F1E8" : "#59635D"
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 10
                    font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    radius: 7
                    color: shortModeBackButton.down ? "#303B35" : (shortModeBackButton.hovered ? "#27312C" : "#1A211E")
                    border.color: shortModeBackButton.activeFocus ? "#C8FF3D" : "#2A3530"
                }
            }
        }

        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: "#2A3530" }

        Text {
            Layout.fillWidth: true
            Layout.fillHeight: true
            text: "Step 3 でクリップ選択・縦長プレビューが追加されます"
            color: "#8E9B94"
            font.family: "Yu Gothic UI"
            font.pixelSize: 14
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            wrapMode: Text.Wrap
        }
    }
}
