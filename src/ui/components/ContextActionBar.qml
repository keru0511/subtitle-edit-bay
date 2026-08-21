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
    property bool canCreateProject: false
    property bool audioMixerAvailable: true
    property string blockReason: ""
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

    // The project state shows two action rows; keep the bar compact enough for
    // the supported 1220x760 window while leaving the initial state shorter.
    implicitHeight: actionBar.projectLoaded || actionBar.activeJob === "render" ? 220 : 148
    Layout.minimumHeight: actionBar.projectLoaded || actionBar.activeJob === "render" ? 220 : 148
    radius: 12
    color: "#121715"
    border.color: "#2A3530"

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 6
        spacing: 3

        RowLayout {
            Layout.fillWidth: true
            Text {
                objectName: "contextActionBarTitle"
                text: "コンテキスト操作"
                color: "#F4F1E8"
                font.family: "Yu Gothic UI"
                font.pixelSize: 14
                font.weight: Font.Bold
            }
            Text {
                objectName: "contextActionStatus"
                Layout.fillWidth: true
                text: actionBar.running ? (actionBar.activeJob === "render" ? "書き出し中" : "処理中") : (actionBar.projectLoaded ? "プロジェクト準備済み" : "素材の準備")
                color: actionBar.running ? "#FFB547" : "#8E9B94"
                font.family: "Yu Gothic UI"
                font.pixelSize: 10
                horizontalAlignment: Text.AlignRight
            }
            SmallButton {
                objectName: "settingsToggleButton"
                text: actionBar.settingsExpanded ? "設定を閉じる" : "詳細設定"
                onClicked: actionBar.settingsRequested()
            }
        }

        GridLayout {
            objectName: "workflowActions"
            Layout.fillWidth: true
            columns: 2
            columnSpacing: 6
            rowSpacing: 4

            Button {
                id: dictionaryButton
                objectName: "transcriptionDictionaryOpenButton"
                Layout.fillWidth: true
                Layout.preferredHeight: 28
                visible: !actionBar.projectLoaded
                enabled: !actionBar.running
                text: "文字起こし辞書を設定"
                onClicked: actionBar.dictionaryRequested()
                contentItem: Text { text: dictionaryButton.text; color: dictionaryButton.enabled ? "#F4F1E8" : "#68716B"; font.family: "Yu Gothic UI"; font.pixelSize: 11; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { radius: 8; color: dictionaryButton.enabled ? "#19201D" : "#252C28"; border.color: dictionaryButton.enabled ? "#2A3530" : "#252C28" }
            }

            Button {
                id: createProjectButton
                objectName: "createEmptyProjectButton"
                Layout.fillWidth: true
                Layout.preferredHeight: 28
                visible: !actionBar.projectLoaded
                enabled: actionBar.canCreateProject && !actionBar.running
                text: "空の編集プロジェクトを作成"
                onClicked: actionBar.createProjectRequested()
                contentItem: Text { text: createProjectButton.text; color: createProjectButton.enabled ? "#10140F" : "#68716B"; font.family: "Yu Gothic UI"; font.pixelSize: 11; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { radius: 8; color: createProjectButton.enabled ? "#C8FF3D" : "#252C28" }
            }

            Button {
                id: transcribeButton
                objectName: "transcribeButton"
                Layout.fillWidth: true
                Layout.preferredHeight: 28
                visible: !actionBar.running || actionBar.activeJob === "transcribe"
                enabled: actionBar.canStartTranscription
                text: actionBar.activeJob === "transcribe" ? "文字起こし中..." : (actionBar.projectLoaded ? "文字起こしを追加 / 更新" : "文字起こしを開始")
                onClicked: actionBar.startTranscriptionRequested()
                contentItem: Text { text: transcribeButton.text; color: transcribeButton.enabled ? "#10140F" : "#68716B"; font.family: "Yu Gothic UI"; font.pixelSize: 11; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { radius: 8; color: transcribeButton.enabled ? "#C8FF3D" : "#252C28" }
            }

            Button {
                id: editButton
                objectName: "editSubtitlesButton"
                Layout.fillWidth: true
                Layout.preferredHeight: 28
                visible: actionBar.projectLoaded
                enabled: actionBar.projectLoaded && !actionBar.running
                text: "字幕を編集する"
                onClicked: { actionBar.editorRequested() }
                contentItem: Text { text: editButton.text; color: editButton.enabled ? "#10140F" : "#68716B"; font.family: "Yu Gothic UI"; font.pixelSize: 11; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { radius: 8; color: editButton.enabled ? "#C8FF3D" : "#252C28" }
            }

            Button {
                id: mixerButton
                objectName: "audioMixerOpenButton"
                Layout.fillWidth: true
                Layout.preferredHeight: 28
                visible: actionBar.projectLoaded
                enabled: actionBar.projectLoaded && !actionBar.running && actionBar.audioMixerAvailable
                text: actionBar.audioMixerAvailable ? "音量を調整する" : "音声トラックなし"
                onClicked: actionBar.mixerRequested()
                contentItem: Text { text: mixerButton.text; color: mixerButton.enabled ? "#10140F" : "#68716B"; font.family: "Yu Gothic UI"; font.pixelSize: 11; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { radius: 8; color: mixerButton.enabled ? "#C8FF3D" : "#252C28" }
            }

            Button {
                id: shortButton
                objectName: "shortModeOpenButton"
                Layout.fillWidth: true
                Layout.preferredHeight: 28
                visible: actionBar.projectLoaded
                enabled: actionBar.projectLoaded && !actionBar.running
                text: "ショート動画を作成"
                onClicked: actionBar.shortModeRequested()
                contentItem: Text { text: shortButton.text; color: shortButton.enabled ? "#10140F" : "#68716B"; font.family: "Yu Gothic UI"; font.pixelSize: 11; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { radius: 8; color: shortButton.enabled ? "#C8FF3D" : "#252C28" }
            }

            Button {
                id: renderButton
                objectName: "renderVideoButton"
                Layout.fillWidth: true
                Layout.preferredHeight: 28
                visible: actionBar.projectLoaded || actionBar.activeJob === "render"
                enabled: actionBar.projectLoaded && !actionBar.running
                text: actionBar.activeJob === "render" ? (actionBar.subtitleAvailable ? "字幕を焼き付け中..." : "動画を書き出し中...") : (actionBar.subtitleAvailable ? "字幕を焼き付けて動画を書き出す" : "動画を書き出す")
                onClicked: actionBar.renderRequested()
                contentItem: Text { text: renderButton.text; color: renderButton.enabled ? "#F4F1E8" : "#68716B"; font.family: "Yu Gothic UI"; font.pixelSize: 11; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { radius: 8; color: renderButton.enabled ? "#19201D" : "#252C28"; border.color: renderButton.enabled ? "#2A3530" : "#252C28" }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Text {
                objectName: "workflowBlockReason"
                Layout.fillWidth: true
                text: actionBar.blockReason.length > 0 ? actionBar.blockReason : actionBar.mixerBlockReason
                visible: text.length > 0
                color: "#FFB547"
                font.family: "Yu Gothic UI"
                font.pixelSize: 9
                wrapMode: Text.Wrap
                maximumLineCount: 2
                elide: Text.ElideRight
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
    }

    property bool subtitleAvailable: false
}
