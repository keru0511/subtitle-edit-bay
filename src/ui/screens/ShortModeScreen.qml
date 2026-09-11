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

    function clampCurrentClipIndex() {
        if (!shortRoot.appBackend) return
        var count = shortRoot.appBackend.shortVideoClipCount
        var nextIndex = count > 0
            ? Math.min(Math.max(0, shortRoot.currentClipIndex), count - 1)
            : 0
        if (nextIndex !== shortRoot.currentClipIndex)
            shortRoot.currentClipIndex = nextIndex
    }

    function currentClip() {
        if (!shortRoot.appBackend) return null
        var count = shortRoot.appBackend.shortVideoClipCount
        if (currentClipIndex < 0 || currentClipIndex >= count) return null
        return shortRoot.appBackend.shortVideoClipAt(currentClipIndex)
    }

    function initializeIfNeeded() {
        if (shortRoot.appBackend) shortRoot.appBackend.initializeShortVideoClips()
    }

    Component.onCompleted: {
        shortRoot.initializeIfNeeded()
        shortRoot.clampCurrentClipIndex()
    }

    Connections {
        target: shortRoot.appBackend
        function onShortVideoClipDataChanged() { shortRoot.clampCurrentClipIndex() }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 12

        EditorModeRail {
            id: shortModeRail
            objectName: "shortModeEditorModeRail"
            Layout.preferredWidth: 86
            Layout.minimumWidth: 86
            Layout.fillHeight: true
            Layout.alignment: Qt.AlignTop
            currentMode: "short"
            capabilities: shortRoot.appBackend ? shortRoot.appBackend.editorModeCapabilities : ({})
            panelColor: shortRoot.mainRoot ? shortRoot.mainRoot.panel : "#161B22"
            raisedColor: shortRoot.mainRoot ? shortRoot.mainRoot.raised : "#21262D"
            borderColor: shortRoot.mainRoot ? shortRoot.mainRoot.border : "#30363D"
            textColor: shortRoot.mainRoot ? shortRoot.mainRoot.textPrimary : "#F0F6FC"
            mutedColor: shortRoot.mainRoot ? shortRoot.mainRoot.textMuted : "#8B949E"
            accentColor: shortRoot.mainRoot ? shortRoot.mainRoot.acid : "#6366F1"
            onModeRequested: function(mode) {
                if (mode !== "short" && shortRoot.mainRoot) {
                    shortRoot.mainRoot.closeShortModeScreen()
                    shortRoot.mainRoot.selectWorkspaceMode(mode)
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12

            RowLayout {
                Layout.fillWidth: true
                Layout.rightMargin: shortRoot.mainRoot ? shortRoot.mainRoot.codexDrawerHeaderInset : 0
                spacing: 12
                Text {
                    text: "ショート動画作成"
                    color: "#F0F6FC"
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 18
                    font.weight: Font.Bold
                }
                Item { Layout.fillWidth: true }
                Button {
                    id: exportButton
                    objectName: "shortModeExportButton"
                    implicitHeight: 32
                    enabled: shortRoot.appBackend && (shortRoot.appBackend.actionCapabilities.canRenderShort || shortRoot.appBackend.actionCapabilities.shortRenderNeedsOutput)
                    ToolTip.visible: hovered && !enabled
                    ToolTip.text: shortRoot.appBackend ? shortRoot.appBackend.actionCapabilities.shortRenderReason : ""
                    text: shortRoot.appBackend && shortRoot.appBackend.actionCapabilities.shortRenderNeedsOutput ? "出力先を選んで書き出す" : "書き出す"
                    onClicked: {
                        shortRoot.appBackend.renderShortVideo()
                    }
                    contentItem: Text {
                        text: exportButton.text
                        color: exportButton.enabled ? "#FFFFFF" : "#8B949E"
                        font.family: "Yu Gothic UI"
                        font.pixelSize: 10
                        font.weight: Font.DemiBold
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        radius: 8
                        color: exportButton.enabled ? (exportButton.down ? "#4F46E5" : (exportButton.hovered ? "#818CF8" : "#6366F1")) : "#21262D"
                        border.color: exportButton.enabled ? "#6366F1" : "#30363D"
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
                        color: shortModeBackButton.enabled ? "#F0F6FC" : "#6E7681"
                        font.family: "Yu Gothic UI"
                        font.pixelSize: 10
                        font.weight: Font.DemiBold
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        radius: 8
                        color: shortModeBackButton.down ? "#30363D" : (shortModeBackButton.hovered ? "#282E33" : "#21262D")
                        border.color: shortModeBackButton.activeFocus ? "#6366F1" : "#30363D"
                    }
                }
            }

            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: "#30363D" }

            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 16

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
}
