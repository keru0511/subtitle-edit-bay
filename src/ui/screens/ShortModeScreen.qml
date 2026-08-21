import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import QtMultimedia
import "../components"

Item {
    id: shortRoot
    objectName: "shortModeScreen"
    anchors.fill: parent

    property var mainRoot: null
    property var appBackend: backend
    property int currentClipIndex: 0

    function currentClip() {
        if (!shortRoot.appBackend) return null
        var clips = shortRoot.appBackend.shortVideoClips
        if (currentClipIndex < 0 || currentClipIndex >= clips.length) return null
        return clips[currentClipIndex]
    }

    function initializeIfNeeded() {
        if (shortRoot.appBackend) shortRoot.appBackend.initializeShortVideoClips()
    }

    Component.onCompleted: shortRoot.initializeIfNeeded()

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 14

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Text {
                text: "ショート動画作成"
                color: "#E8EFEA"
                font.family: "Yu Gothic UI"
                font.pixelSize: 18
                font.weight: Font.Bold
            }
            Item { Layout.fillWidth: true }
            Button {
                id: exportButton
                objectName: "shortModeExportButton"
                implicitHeight: 32
                enabled: shortRoot.appBackend && !shortRoot.appBackend.running && shortRoot.appBackend.shortVideoClips.length > 0
                text: "書き出す"
                onClicked: {
                    shortRoot.appBackend.renderShortVideo()
                }
                contentItem: Text {
                    text: exportButton.text
                    color: exportButton.enabled ? "#10140F" : "#68716B"
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 10
                    font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    radius: 7
                    color: exportButton.enabled ? "#C8FF3D" : "#252C28"
                }
            }
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

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 20

            ShortModePreview {
                id: shortPreview
                objectName: "shortModePreview"
                Layout.fillHeight: true
                Layout.preferredWidth: parent ? parent.height * 9 / 16 : 540
                Layout.maximumWidth: 540
                Layout.minimumWidth: 200
                appBackend: shortRoot.appBackend
                clipData: shortRoot.currentClip()
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 14

                HighlightCandidateList {
                    id: highlightCandidates
                    objectName: "highlightCandidateList"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 250
                    appBackend: shortRoot.appBackend
                    onPreviewRequested: function (seconds) { shortPreview.previewAt(seconds) }
                }

                ShortModeClipList {
                    id: clipList
                    objectName: "shortModeClipList"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    appBackend: shortRoot.appBackend
                    selectedIndex: shortRoot.currentClipIndex
                    onSelected: function (index) { shortRoot.currentClipIndex = index }
                }

                ShortModeSettingsPanel {
                    id: settingsPanel
                    objectName: "shortModeSettingsPanel"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    appBackend: shortRoot.appBackend
                }
            }
        }
    }
}
