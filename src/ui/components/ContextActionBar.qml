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
    property bool renderNeedsOutput: false
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

    implicitHeight: 177
    Layout.minimumHeight: 177
    radius: 12
    color: "#161B22"
    border.color: "#30363D"

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
            color: control.enabled ? (control.primary ? "#FFFFFF" : "#F0F6FC") : "#6E7681"
            font.family: "Yu Gothic UI"
            font.pixelSize: 11
            font.weight: Font.Bold
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 8
            color: control.enabled ? (control.primary ? (control.down ? "#4F46E5" : (control.hovered ? "#818CF8" : "#6366F1")) : (control.down ? "#30363D" : (control.hovered ? "#282E33" : "#21262D"))) : "#161B22"
            border.color: control.enabled ? (control.primary ? "#6366F1" : "#30363D") : "#21262D"
        }
    }
    component CategoryLabel: Text {
        Layout.preferredWidth: 38
        color: "#8B949E"
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
                color: "#F0F6FC"
                font.family: "Yu Gothic UI"
                font.pixelSize: 14
                font.weight: Font.Bold
            }
            Text {
                objectName: "contextActionStatus"
                Layout.fillWidth: true
                text: actionBar.running ? (actionBar.activeJob.indexOf("render") === 0 ? "書き出し中" : "処理中") : (actionBar.projectLoaded ? "プロジェクト準備済み" : "文字起こしなしでも編集できます")
                color: actionBar.running ? "#F59E0B" : "#8B949E"
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
            objectName: "derivedArtifactActions"
            Layout.fillWidth: true
            visible: actionBar.projectLoaded
            spacing: 6
            CategoryLabel { text: "別成果物" }
            ActionButton {
                objectName: "shortModeOpenButton"
                enabled: !actionBar.running
                text: "ショートを作成"
                onClicked: actionBar.shortModeRequested()
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
                text: actionBar.activeJob === "render" ? "動画を書き出し中..." : (actionBar.renderNeedsOutput ? "出力先を選んで通常動画を書き出す" : (actionBar.subtitleAvailable ? "通常動画を書き出す（字幕焼き付け）" : "通常動画を書き出す"))
                reason: actionBar.renderBlockReason
                onClicked: actionBar.renderRequested()
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
            color: "#F59E0B"
            font.family: "Yu Gothic UI"
            font.pixelSize: 9
            elide: Text.ElideRight
        }
    }
}
