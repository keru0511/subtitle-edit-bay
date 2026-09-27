import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

Item {
    id: shortRoot
    objectName: "shortModeScreen"
    anchors.fill: parent

    property var mainRoot: null
    // qmllint disable unqualified
    property var appBackend: backend
    // qmllint enable unqualified
    property int currentClipIndex: 0
    property string inputValidationMessage: ""
    readonly property bool hasIncompleteInput: settingsPanel.hasIncompleteInput()
        || clipList.activeTimeInputIncomplete
    onHasIncompleteInputChanged: {
        if (!hasIncompleteInput)
            inputValidationMessage = ""
    }

    function clampCurrentClipIndex() {
        if (!shortRoot.appBackend) return
        var count = shortRoot.appBackend.shortVideo.shortVideoClipCount
        var nextIndex = count > 0
            ? Math.min(Math.max(0, shortRoot.currentClipIndex), count - 1)
            : 0
        if (nextIndex !== shortRoot.currentClipIndex)
            shortRoot.currentClipIndex = nextIndex
    }

    function currentClip() {
        if (!shortRoot.appBackend) return null
        var count = shortRoot.appBackend.shortVideo.shortVideoClipCount
        if (currentClipIndex < 0 || currentClipIndex >= count) return null
        return shortRoot.appBackend.shortVideo.shortVideoClipAt(currentClipIndex)
    }

    function initializeIfNeeded() {
        if (shortRoot.appBackend) shortRoot.appBackend.shortVideo.initializeShortVideoClips()
    }

    function commitPendingEdits() {
        if (shortRoot.mainRoot && !shortRoot.mainRoot.commitInputMethod())
            return false
        // qmllint disable missing-property
        var focusedInput = shortRoot.mainRoot ? shortRoot.mainRoot.activeFocusItem : null
        // 入力途中の値を失って古い設定で書き出さない。
        if (settingsPanel.hasIncompleteInput()
                || (focusedInput && focusedInput.acceptableInput === false)) {
            shortRoot.inputValidationMessage = "入力途中の値を完了してください"
            return false
        }
        // qmllint enable missing-property
        if (!clipList.commitPendingEdits()) {
            shortRoot.inputValidationMessage = "クリップの時刻を保存できませんでした"
            return false
        }
        if (!settingsPanel.commitPendingEdits()) {
            shortRoot.inputValidationMessage = "ショート設定を保存できませんでした"
            return false
        }
        shortRoot.inputValidationMessage = ""
        // 範囲指定などの入力欄の onEditingFinished を、画面遷移より先に実行する。
        shortRoot.forceActiveFocus()
        return true
    }

    Component.onCompleted: {
        shortRoot.initializeIfNeeded()
        shortRoot.clampCurrentClipIndex()
    }

    Connections {
        target: shortRoot.appBackend.shortVideo
        function onShortVideoClipDataChanged() { shortRoot.clampCurrentClipIndex() }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 14

        RowLayout {
            Layout.fillWidth: true
            Layout.rightMargin: shortRoot.mainRoot ? shortRoot.mainRoot.codexDrawerHeaderInset : 0
            spacing: 12
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text {
                    text: "ショート用ワークスペース"
                    color: "#E8EFEA"
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 18
                    font.weight: Font.Bold
                }
                Text {
                    text: "通常動画から派生する別成果物を編集します"
                    color: "#8E9B94"
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 9
                }
            }
            Button {
                id: exportButton
                objectName: "shortModeExportButton"
                implicitHeight: 32
                enabled: shortRoot.appBackend && !shortRoot.appBackend.running
                    && (shortRoot.appBackend.workflow.actionCapabilities.canRenderShort || shortRoot.appBackend.workflow.actionCapabilities.shortRenderNeedsOutput)
                    && !shortRoot.hasIncompleteInput
                ToolTip.visible: hovered && !enabled
                ToolTip.text: shortRoot.hasIncompleteInput
                    ? "入力途中の値を完了してください"
                    : (shortRoot.appBackend ? shortRoot.appBackend.workflow.actionCapabilities.shortRenderReason : "")
                text: shortRoot.appBackend && shortRoot.appBackend.workflow.actionCapabilities.shortRenderNeedsOutput
                    ? "出力先を選んでショート動画を書き出す"
                    : "ショート動画を書き出す"
                onClicked: {
                    if (!shortRoot.commitPendingEdits())
                        return
                    shortRoot.appBackend.workflow.renderShortVideo()
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
                    && !shortRoot.hasIncompleteInput
                text: "通常動画編集へ戻る"
                onClicked: shortRoot.mainRoot.closeShortWorkspace()
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
            objectName: "shortModeInputValidationMessage"
            Layout.fillWidth: true
            visible: text.length > 0
            text: shortRoot.hasIncompleteInput
                ? "入力途中の値を完了してください"
                : shortRoot.inputValidationMessage
            color: "#F59E0B"
            font.family: "Yu Gothic UI"
            font.pixelSize: 10
            wrapMode: Text.Wrap
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 20

            ColumnLayout {
                Layout.fillHeight: true
                Layout.preferredWidth: 300
                Layout.minimumWidth: 260
                Layout.maximumWidth: 340
                spacing: 8

                Text {
                    text: "ショート設定"
                    color: "#E8EFEA"
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 13
                    font.weight: Font.Bold
                }
                Rectangle {
                    objectName: "shortWorkspaceTimeBasis"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 46
                    radius: 7
                    color: "#171E1A"
                    border.color: "#2A3530"
                    Text {
                        anchors.fill: parent
                        anchors.margins: 9
                        text: "素材時間: 元ソース動画を基準にします\n通常動画のカット後時間とは混在しません"
                        color: "#8E9B94"
                        font.family: "Yu Gothic UI"
                        font.pixelSize: 9
                        wrapMode: Text.Wrap
                    }
                }
                ShortModeSettingsPanel {
                    id: settingsPanel
                    objectName: "shortModeSettingsPanel"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    appBackend: shortRoot.appBackend
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 8

                Text {
                    text: "ショートプレビュー"
                    color: "#E8EFEA"
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 13
                    font.weight: Font.Bold
                }

                ShortModePreview {
                    id: shortPreview
                    objectName: "shortModePreview"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumWidth: 220
                    appBackend: shortRoot.appBackend
                    clipData: shortRoot.currentClip()
                }
            }

            ColumnLayout {
                Layout.fillHeight: true
                Layout.preferredWidth: 340
                Layout.minimumWidth: 280
                Layout.maximumWidth: 380
                spacing: 12

                Text {
                    text: "クリップ"
                    color: "#E8EFEA"
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 13
                    font.weight: Font.Bold
                }

                HighlightCandidateList {
                    id: highlightCandidates
                    objectName: "highlightCandidateList"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 280
                    appBackend: shortRoot.appBackend
                    onPreviewRequested: function (seconds, endSeconds) { shortPreview.previewAt(seconds, endSeconds) }
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
            }

        }
    }
}
