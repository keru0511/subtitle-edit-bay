pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: actionBar
    objectName: "contextActionBar"
    property bool compact: false
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

    implicitHeight: compact ? 36 : 177
    Layout.minimumHeight: implicitHeight
    radius: compact ? 8 : 12
    color: "#131A26"
    border.color: "#243044"

    component ActionButton: Button {
        id: control
        property bool primary: false
        property string reason: ""
        Layout.fillWidth: true
        Layout.preferredHeight: actionBar.compact ? 26 : 28
        ToolTip.visible: hovered && reason.length > 0
        ToolTip.text: reason
        contentItem: Text {
            text: control.text
            color: control.enabled ? (control.primary ? "#FFFFFF" : "#F8FAFC") : "#64748B"
            font.family: "Yu Gothic UI"
            font.pixelSize: actionBar.compact ? 10 : 11
            font.weight: Font.Bold
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: actionBar.compact ? 6 : 8
            color: control.enabled ? (control.primary ? (control.down ? "#4338CA" : (control.hovered ? "#4F46E5" : "#6366F1")) : (control.down ? "#222E42" : (control.hovered ? "#1F2937" : "#1A2332"))) : "#131A26"
            border.color: control.enabled ? (control.primary ? "#6366F1" : "#243044") : "#1F2937"
        }
    }
    component CategoryLabel: Text {
        Layout.preferredWidth: 38
        color: "#94A3B8"
        font.family: "Yu Gothic UI"
        font.pixelSize: 10
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: actionBar.compact ? 4 : 6
        spacing: actionBar.compact ? 0 : 3
        RowLayout {
            visible: !actionBar.compact
            Layout.fillWidth: true
            Text {
                objectName: "contextActionBarTitle"
                text: actionBar.projectLoaded ? "ツールと出力" : "素材の準備"
                color: "#F8FAFC"
                font.family: "Yu Gothic UI"
                font.pixelSize: 14
                font.weight: Font.Bold
            }
            Text {
                objectName: "contextActionStatus"
                Layout.fillWidth: true
                text: actionBar.running ? (actionBar.activeJob.indexOf("render") === 0 ? "書き出し中" : "処理中") : (actionBar.projectLoaded ? "プロジェクト準備済み" : "文字起こしなしでも編集できます")
                color: actionBar.running ? "#F59E0B" : "#94A3B8"
                font.family: "Yu Gothic UI"
                font.pixelSize: 10
                horizontalAlignment: Text.AlignRight
                elide: Text.ElideRight
            }
            SmallButton {
                objectName: "legacySettingsToggleButton"
                text: actionBar.settingsExpanded ? "設定を閉じる" : "文字起こし・出力設定"
                onClicked: actionBar.settingsRequested()
            }
        }

        RowLayout {
            objectName: "transcriptionToolActions"
            Layout.fillWidth: true
            spacing: actionBar.compact ? 4 : 6
            CategoryLabel { text: "ツール"; visible: !actionBar.compact }
            SmallButton {
                objectName: "settingsToggleButton"
                visible: actionBar.compact
                text: "設定"
                implicitHeight: 26
                onClicked: actionBar.settingsRequested()
            }
            SmallButton {
                objectName: "saveSettingsButton"
                visible: actionBar.compact && actionBar.running
                text: actionBar.running ? "停止" : "設定を保存"
                implicitHeight: 26
                enabled: actionBar.activeJob !== "update"
                onClicked: actionBar.saveOrStopRequested()
            }
            ActionButton {
                objectName: "transcribeButton"
                primary: true
                enabled: actionBar.canStartTranscription
                text: actionBar.activeJob === "transcribe" ? "文字起こし中..." : "文字起こし"
                reason: actionBar.blockReason
                onClicked: actionBar.startTranscriptionRequested()
            }
            ActionButton {
                objectName: "transcriptionDictionaryOpenButton"
                enabled: !actionBar.running
                text: "文字起こし辞書"
                onClicked: actionBar.dictionaryRequested()
            }
            ActionButton {
                objectName: "createEmptyProjectButton"
                visible: !actionBar.projectLoaded
                enabled: actionBar.canCreateProject && !actionBar.running
                text: "空の編集プロジェクトを作成"
                onClicked: actionBar.createProjectRequested()
            }
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
        }
        RowLayout {
            objectName: "derivedArtifactActions"
            Layout.fillWidth: true
            visible: actionBar.projectLoaded && !actionBar.compact
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
            visible: actionBar.projectLoaded && !actionBar.compact
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
            visible: !actionBar.compact
            spacing: 6
            CategoryLabel { text: "表示" }
            SmallButton {
                objectName: "legacySaveSettingsButton"
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
            visible: text.length > 0 && !actionBar.compact
            color: "#F59E0B"
            font.family: "Yu Gothic UI"
            font.pixelSize: 9
            elide: Text.ElideRight
        }
    }
}
