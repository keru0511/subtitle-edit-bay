pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

MainWorkflowScreen {
    id: screenRoot

    Rectangle {
        id: dictionaryPage
        objectName: "transcriptionDictionaryPage"
        anchors.fill: parent
        visible: screenRoot.dictionaryMode
        z: 150
        color: "#0D1210"
        border.color: "#46564E"
        focus: visible
        Keys.onEscapePressed: dictionaryPage.saveAndClose()
        onVisibleChanged: if (visible) forceActiveFocus()

        function saveContext() {
            contextPanel.commitContext()
            screenRoot.appBackend.saveSettings(screenRoot.currentSettings())
        }

        function saveAndClose() {
            saveContext()
            screenRoot.closeDictionaryScreen()
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 14

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Text { text: "文字起こし辞書"; color: screenRoot.textPrimary; font.family: "Yu Gothic UI"; font.pixelSize: 18; font.weight: Font.Bold }
                    Text { text: "ゲーム固有の名称や表記を文字起こしへ反映します"; color: screenRoot.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 9 }
                }
                Text { text: screenRoot.userFacingStatusLabel(screenRoot.appBackend.stage, screenRoot.appBackend.status); color: screenRoot.appBackend.stage === "ERROR" ? screenRoot.danger : screenRoot.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 9; Layout.maximumWidth: 360; elide: Text.ElideRight }
                SmallButton { objectName: "transcriptionDictionarySaveButton"; text: "保存"; enabled: !screenRoot.appBackend.running; onClicked: dictionaryPage.saveContext() }
                SmallButton { objectName: "transcriptionDictionaryBackButton"; text: "メインへ戻る"; enabled: !screenRoot.appBackend.running; onClicked: dictionaryPage.saveAndClose() }
            }

            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: screenRoot.border }

            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true
                TranscriptionContextPanel {
                    id: contextPanel
                    objectName: "mainTranscriptionContextPanel"
                    width: Math.min(760, parent.width)
                    height: Math.min(560, parent.height)
                    anchors.centerIn: parent
                    context: screenRoot.appBackend.transcriptionContext
                    running: screenRoot.appBackend.running
                    panelColor: screenRoot.panel
                    raisedColor: screenRoot.raised
                    borderColor: screenRoot.border
                    textPrimaryColor: screenRoot.textPrimary
                    textMutedColor: screenRoot.textMuted
                    accentColor: screenRoot.acid
                    onTranscriptionContextEdited: function(context) {
                        screenRoot.appBackend.setTranscriptionContext(context)
                    }
                    onWebDictionaryRefreshRequested: function(url, snippet) {
                        screenRoot.appBackend.refreshTranscriptionWebDictionary(url, snippet)
                    }
                }
            }
        }
    }

    Shortcut {
        sequence: StandardKey.Save
        enabled: screenRoot.dictionaryMode
        onActivated: dictionaryPage.saveContext()
    }
}
