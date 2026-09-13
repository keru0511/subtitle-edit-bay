pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: panel
    // qmllint disable unqualified
    property var backend
    property color panelColor: "#161B22"
    property color raisedColor: "#21262D"
    property color borderColor: "#30363D"
    property color textColor: "#F0F6FC"
    property color mutedColor: "#8B949E"
    property color accentColor: "#6366F1"
    property color warningColor: "#F59E0B"
    property color errorColor: "#EF4444"

    visible: backend && backend.progressVisible
    implicitHeight: 126
    radius: 12
    color: panel.panelColor
    border.color: panel.borderColor
    border.width: 1
    clip: true

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 7

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: "処理進捗"
                color: panel.textColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 12
                font.weight: Font.Bold
            }
            Text {
                objectName: "processingProgressStatus"
                Layout.fillWidth: true
                text: backend && backend.progressCurrentStepDisplay
                    ? backend.progressCurrentStepDisplay + "：" + backend.status
                    : (backend ? backend.status : "")
                color: panel.mutedColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 10
                elide: Text.ElideRight
            }
            Text {
                objectName: "processingProgressPercent"
                text: backend ? String(backend.progressPercent) + "%" : "0%"
                color: panel.accentColor
                font.family: "Cascadia Mono"
                font.pixelSize: 16
                font.weight: Font.Bold
            }
            Button {
                id: stopButton
                objectName: "processingProgressStopButton"
                text: "停止"
                visible: backend && backend.running
                enabled: visible
                onClicked: backend.cancelProcessing()
                contentItem: Text {
                    text: "停止"
                    color: panel.errorColor
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    implicitWidth: 52
                    implicitHeight: 24
                    radius: 6
                    color: stopButton.down ? "#3A1B1F" : "#241518"
                    border.color: panel.errorColor
                    border.width: 1
                }
            }
        }

        ProgressBar {
            objectName: "processingProgressBar"
            Layout.fillWidth: true
            Layout.preferredHeight: 6
            from: 0
            to: 1
            value: backend ? backend.progress : 0
            background: Rectangle {
                radius: 3
                color: panel.raisedColor
                border.color: panel.borderColor
            }
            contentItem: Item {
                Rectangle {
                    width: parent.width * (backend ? backend.progress : 0)
                    height: parent.height
                    radius: 3
                    color: panel.accentColor
                }
            }
        }

        RowLayout {
            objectName: "processingProgressStepList"
            Layout.fillWidth: true
            spacing: 5
            Repeater {
                model: backend ? backend.progressSteps : []
                delegate: Rectangle {
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.preferredHeight: 39
                    radius: 7
                    color: modelData.state === "running" ? panel.raisedColor : "transparent"
                    border.color: modelData.state === "running" ? panel.accentColor : panel.borderColor
                    border.width: modelData.state === "running" ? 1 : 0

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 5
                        spacing: 4
                        Text {
                            text: modelData.state === "completed" ? "✓"
                                : (modelData.state === "error" ? "!"
                                : (modelData.state === "cancelled" ? "Ⅱ"
                                : (modelData.state === "running" ? "●" : "○")))
                            color: modelData.state === "error" ? panel.errorColor
                                : (modelData.state === "cancelled" ? panel.warningColor
                                : (modelData.state === "pending" ? panel.mutedColor : panel.accentColor))
                            font.pixelSize: 12
                            font.weight: Font.Bold
                        }
                        Text {
                            Layout.fillWidth: true
                            text: modelData.label
                            color: modelData.state === "pending" ? panel.mutedColor : panel.textColor
                            font.family: "Yu Gothic UI"
                            font.pixelSize: 9
                            elide: Text.ElideRight
                        }
                    }
                }
            }
        }
    }
    // qmllint enable unqualified
}
