pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: actionBar
    objectName: "contextActionBar"
    property bool projectLoaded: false
    property bool running: false
    property string activeJob: ""
    property bool canStartTranscription: false
    property bool canRenderNormal: false
    property bool canCreateProject: false
    property bool audioMixerAvailable: true
    property bool subtitleAvailable: false
    property string blockReason: ""
    property string renderBlockReason: ""
    property string mixerBlockReason: ""
    property bool settingsExpanded: false
    property bool outputFolderAvailable: false

    signal settingsRequested()
    signal dictionaryRequested()
    signal createProjectRequested()
    signal startTranscriptionRequested()
    signal editorRequested()
    signal mixerRequested()
    signal shortModeRequested()
    signal renderRequested()
    signal saveOrStopRequested()
    signal outputFolderRequested()

    implicitHeight: 148
    Layout.minimumHeight: 148
    radius: 12
    color: "#121715"
    border.color: "#2A3530"

    component ActionButton: Button {
        id: control
        property bool primary: false
        property string reason: ""
        Layout.fillWidth: true
        Layout.preferredHeight: 28
        ToolTip.visible: hovered && reason.length > 0
        ToolTip.text: reason
        contentItem: Text {
            text: control.text
            color: control.enabled ? (control.primary ? "#10140F" : "#F4F1E8") : "#68716B"
            font.family: "Yu Gothic UI"
            font.pixelSize: 11
            font.weight: Font.Bold
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 8
            color: control.enabled ? (control.primary ? "#C8FF3D" : "#19201D") : "#252C28"
            border.color: "#2A3530"
        }
    }
    component CategoryLabel: Text {
        Layout.preferredWidth: 38
        color: "#8E9B94"
        font.family: "Yu Gothic UI"
        font.pixelSize: 10
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 6
        spacing: 3
        RowLayout {
            Layout.fillWidth: true
            Text {
                objectName: "contextActionBarTitle"
                text: actionBar.projectLoaded ? "ツールと出力" : "素材の準備"
                color: "#F4F1E8"
                font.family: "Yu Gothic UI"
                font.pixelSize: 14
                font.weight: Font.Bold
            }
            Text {
                objectName: "contextActionStatus"
                Layout.fillWidth: true
                text: actionBar.running ? (actionBar.activeJob.indexOf("render") === 0 ? "書き出し中" : "処理中") : (actionBar.projectLoaded ? "プロジェクト準備済み" : "文字起こしなしでも編集できます")
                color: actionBar.running ? "#FFB547" : "#8E9B94"
                font.family: "Yu Gothic UI"
                font.pixelSize: 10
                horizontalAlignment: Text.AlignRight
                elide: Text.ElideRight
            }
            SmallButton {
                objectName: "settingsToggleButton"
                text: actionBar.settingsExpanded ? "設定を閉じる" : "文字起こし・出力設定"
                onClicked: actionBar.settingsRequested()
            }
        }

        RowLayout {
            objectName: "transcriptionToolActions"
            Layout.fillWidth: true
            spacing: 6
            CategoryLabel { text: "ツール" }
            ActionButton {
                objectName: "transcribeButton"
                primary: true
                enabled: actionBar.canStartTranscription
                text: actionBar.activeJob === "transcribe" ? "文字起こし中..." : (actionBar.projectLoaded ? "文字起こしを追加 / 更新" : "文字起こしを開始")
                reason: actionBar.blockReason
                onClicked: actionBar.startTranscriptionRequested()
            }
            ActionButton {
                objectName: "transcriptionDictionaryOpenButton"
                enabled: !actionBar.running
                text: "文字起こし辞書を設定"
                onClicked: actionBar.dictionaryRequested()
            }
            ActionButton {
                objectName: "createEmptyProjectButton"
                visible: !actionBar.projectLoaded
                enabled: actionBar.canCreateProject && !actionBar.running
                text: "空の編集プロジェクトを作成"
                onClicked: actionBar.createProjectRequested()
            }
        }
        RowLayout {
            objectName: "outputActions"
            Layout.fillWidth: true
            visible: actionBar.projectLoaded
            spacing: 6
            CategoryLabel { text: "出力" }
            ActionButton {
                objectName: "renderVideoButton"
                primary: true
                enabled: actionBar.canRenderNormal
                text: actionBar.activeJob === "render" ? "動画を書き出し中..." : (actionBar.subtitleAvailable ? "通常動画を書き出す（字幕焼き付け）" : "通常動画を書き出す")
                reason: actionBar.renderBlockReason
                onClicked: actionBar.renderRequested()
            }
            ActionButton {
                objectName: "shortModeOpenButton"
                enabled: !actionBar.running
                text: "ショート動画を作成"
                onClicked: actionBar.shortModeRequested()
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            CategoryLabel { text: "表示"; visible: actionBar.projectLoaded }
            ActionButton {
                objectName: "editSubtitlesButton"
                visible: actionBar.projectLoaded
                enabled: !actionBar.running
                text: "字幕を拡大編集"
                onClicked: actionBar.editorRequested()
            }
            ActionButton {
                objectName: "audioMixerOpenButton"
                visible: actionBar.projectLoaded
                enabled: !actionBar.running && actionBar.audioMixerAvailable
                text: actionBar.audioMixerAvailable ? "音量を拡大編集" : "音声トラックなし"
                reason: actionBar.mixerBlockReason
                onClicked: actionBar.mixerRequested()
            }
            SmallButton {
                objectName: "saveSettingsButton"
                text: actionBar.running ? (actionBar.activeJob === "update" ? "更新中..." : "停止") : "設定を保存"
                enabled: !(actionBar.running && actionBar.activeJob === "update")
                onClicked: actionBar.saveOrStopRequested()
            }
            SmallButton {
                objectName: "outputFolderButton"
                text: "出力先を開く"
                enabled: actionBar.outputFolderAvailable
                onClicked: actionBar.outputFolderRequested()
            }
        }
        Text {
            objectName: "workflowBlockReason"
            Layout.fillWidth: true
            text: actionBar.projectLoaded && actionBar.renderBlockReason.length > 0
                ? actionBar.renderBlockReason : actionBar.blockReason
            visible: text.length > 0
            color: "#FFB547"
            font.family: "Yu Gothic UI"
            font.pixelSize: 9
            elide: Text.ElideRight
        }
    }
}
