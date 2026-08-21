import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: panel
    property var backend
    property bool userExpanded: false
    property bool expanded: userExpanded || (backend && backend.stage === "ERROR")
    implicitHeight: expanded ? 280 : 118
    radius: 12
    color: "#0B100D"
    border.color: "#27312C"
    clip: true

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 6

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: backend && backend.stage === "ERROR" ? "処理ログ / エラー" : "処理ログ"
                color: "#E8EFEA"
                font.family: "Yu Gothic UI"
                font.pixelSize: 11
                font.weight: Font.Bold
            }
            Text {
                objectName: "workflowStatusText"
                Layout.fillWidth: true
                text: backend ? backend.status : ""
                color: "#AEBEB3"
                font.family: "Yu Gothic UI"
                font.pixelSize: 10
                elide: Text.ElideRight
            }
            Item { Layout.fillWidth: true }
            Button {
                objectName: "applicationLogToggleButton"
                text: panel.expanded ? "縮小" : "詳細"
                onClicked: panel.userExpanded = !panel.userExpanded
            }
            Button {
                objectName: "copyLogsButton"
                text: "ログをコピー"
                onClicked: backend.copyLogsToClipboard()
            }
            Button {
                objectName: "copyErrorLogsButton"
                text: "エラーをコピー"
                visible: backend && backend.stage === "ERROR"
                onClicked: backend.copyErrorLogsToClipboard()
            }
            Button {
                objectName: "copyApplicationInfoButton"
                text: "アプリ情報をコピー"
                onClicked: backend.copyApplicationInfoToClipboard()
            }
            Button {
                objectName: "openLogsButton"
                text: "ログフォルダを開く"
                onClicked: backend.openLogFolder()
            }
        }

        TextArea {
            objectName: "applicationLogTextArea"
            Layout.fillWidth: true
            Layout.fillHeight: true
            readOnly: true
            selectByMouse: true
            text: backend ? backend.logText : ""
            color: "#AEBEB3"
            font.family: "Cascadia Mono"
            font.pixelSize: 9
            wrapMode: TextEdit.WrapAnywhere
            placeholderText: "処理ログ"
            background: Rectangle { color: "transparent" }
        }
    }
}
