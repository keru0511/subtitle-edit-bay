pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: header

    // This component is deliberately a presentational boundary.  The
    // workflow screen supplies backend snapshots and owns the actions; the
    // header does not keep a second workspace or project state.
    property bool projectLoaded: false
    property string projectName: ""
    property bool projectDirty: false
    property string sourcePath: ""
    // Snapshot supplied by the backend-owned navigation controller.  This is
    // display data, never a second workspace source of truth.
    property string workspaceKind: "normal-video"
    property string currentEditMode: "cut"
    property string activityText: ""
    property string applicationVersion: ""
    property bool running: false
    property bool updateBusy: false
    property bool outputFolderAvailable: false
    property bool canRender: false
    property bool renderNeedsOutput: false
    property string renderBlockReason: ""
    property bool aiAuthenticated: false
    property bool aiLoginAvailable: false
    property string aiAuthState: ""
    property string aiConnectionState: ""
    property int rightInset: 0

    property color panelColor: "#101512"
    property color raisedColor: "#21262D"
    property color borderColor: "#30363D"
    property color textColor: "#F0F6FC"
    property color mutedColor: "#8B949E"
    property color accentColor: "#6366F1"
    property color warningColor: "#F59E0B"

    signal updateCheckRequested()
    signal projectOpenRequested()
    signal sourceSettingsRequested()
    signal saveRequested()
    signal outputFolderRequested()
    signal aiAssistantRequested()
    signal shortWorkspaceRequested()
    signal renderRequested()

    objectName: "workspaceHeader"
    implicitHeight: 62
    height: visible ? implicitHeight : 0
    color: header.panelColor
    border.color: header.borderColor
    clip: true

    function sourceFileName(path) {
        var value = String(path || "")
        if (!value)
            return "素材未選択"
        var parts = value.split(/[\\/]/)
        return parts[parts.length - 1] || value
    }

    function editModeLabel(mode) {
        return {
            "subtitle": "字幕編集",
            "cut": "カット編集",
            "audio": "音量編集"
        }[mode] || "編集"
    }

    readonly property string workspaceLabel: header.workspaceKind === "short-artifact"
        ? "ショート"
        : "通常動画"
    readonly property string sourceLabel: header.sourceFileName(header.sourcePath)
    readonly property string saveStatusLabel: header.projectDirty
        ? "● 保存待ち"
        : (header.projectLoaded ? "✓ 保存済み" : "プロジェクト未作成")
    readonly property string aiActionLabel: header.aiAuthenticated
        ? "AI"
        : (header.aiAuthState === "login_pending"
            ? "ブラウザを開く"
            : (["error", "disconnected"].indexOf(header.aiConnectionState) >= 0
                ? "再接続" : "AIログイン"))

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 14
        anchors.rightMargin: 14 + header.rightInset
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 300
            Layout.maximumWidth: 620
            spacing: 10

            Rectangle {
                Layout.preferredWidth: 30
                Layout.preferredHeight: 30
                radius: 8
                color: header.accentColor

                Text {
                    anchors.centerIn: parent
                    text: "S"
                    color: "#FFFFFF"
                    font.family: "Bahnschrift"
                    font.pixelSize: 18
                    font.weight: Font.Bold
                }
            }

            ColumnLayout {
                Layout.minimumWidth: 140
                Layout.preferredWidth: 190
                spacing: 0

                Text {
                    objectName: "workspaceHeaderBrand"
                    text: "SUBTITLE EDIT BAY"
                    color: header.textColor
                    font.family: "Bahnschrift"
                    font.pixelSize: 15
                    font.weight: Font.Bold
                    font.letterSpacing: 1.2
                    elide: Text.ElideRight
                }
                Text {
                    objectName: "workspaceHeaderProjectStatus"
                    Layout.fillWidth: true
                    text: header.projectLoaded
                        ? header.saveStatusLabel
                        : "新規作成または既存プロジェクトを選択"
                    color: header.projectDirty ? header.warningColor : header.mutedColor
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 9
                    elide: Text.ElideRight
                }
                Text {
                    objectName: "workspaceHeaderVersion"
                    text: header.applicationVersion ? "v" + header.applicationVersion : ""
                    color: header.accentColor
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 8
                    elide: Text.ElideRight
                }
            }

            Rectangle {
                Layout.preferredWidth: 1
                Layout.preferredHeight: 30
                color: header.borderColor
            }

            Button {
                id: sourceButton
                objectName: "sourceSetupButton"
                Layout.fillWidth: true
                Layout.minimumWidth: 130
                Layout.preferredHeight: 38
                enabled: !header.running
                ToolTip.visible: hovered
                ToolTip.text: header.sourcePath || "動画・音声・話者の素材設定を開く"
                onClicked: header.sourceSettingsRequested()

                contentItem: ColumnLayout {
                    spacing: 0
                    Text {
                        objectName: "workspaceHeaderProjectName"
                        Layout.fillWidth: true
                        text: header.projectLoaded
                            ? (header.projectName || "無題のプロジェクト")
                            : "素材設定"
                        color: sourceButton.enabled ? header.textColor : header.mutedColor
                        font.family: "Yu Gothic UI"
                        font.pixelSize: 10
                        font.weight: Font.DemiBold
                        elide: Text.ElideMiddle
                    }
                    Text {
                        Layout.fillWidth: true
                        text: header.sourceLabel
                        color: header.mutedColor
                        font.family: "Yu Gothic UI"
                        font.pixelSize: 8
                        elide: Text.ElideMiddle
                    }
                }
                background: Rectangle {
                    radius: 7
                    color: sourceButton.hovered ? header.raisedColor : "transparent"
                    border.color: sourceButton.activeFocus ? header.accentColor : header.borderColor
                }
            }
        }

        Rectangle {
            objectName: "workspaceHeaderModeIndicator"
            Layout.preferredWidth: 170
            Layout.minimumWidth: 150
            Layout.preferredHeight: 38
            radius: 8
            color: header.raisedColor
            border.color: header.borderColor

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 7
                spacing: 0
                Text {
                    objectName: "workspaceHeaderWorkspaceLabel"
                    Layout.fillWidth: true
                    text: header.workspaceLabel + "ワークスペース"
                    color: header.mutedColor
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 8
                    horizontalAlignment: Text.AlignHCenter
                }
                Text {
                    objectName: "workspaceHeaderModeLabel"
                    Layout.fillWidth: true
                    text: header.editModeLabel(header.currentEditMode)
                    color: header.textColor
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter
                    elide: Text.ElideRight
                }
            }
        }

        RowLayout {
            Layout.minimumWidth: 430
            Layout.preferredWidth: 500
            Layout.maximumWidth: 560
            spacing: 5

            Text {
                objectName: "workspaceHeaderActivity"
                Layout.fillWidth: true
                Layout.minimumWidth: 60
                text: header.activityText
                color: header.running ? header.warningColor : header.mutedColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 8
                horizontalAlignment: Text.AlignRight
                elide: Text.ElideRight
            }

            SmallButton {
                objectName: "checkForUpdatesButton"
                text: "更新"
                enabled: !header.running && !header.updateBusy
                onClicked: header.updateCheckRequested()
            }
            SmallButton {
                objectName: "projectOpenButton"
                text: "開く"
                enabled: !header.running
                onClicked: header.projectOpenRequested()
            }
            SmallButton {
                objectName: "workspaceHeaderSaveButton"
                focusPolicy: Qt.TabFocus
                text: "保存"
                enabled: header.projectLoaded && !header.running
                onClicked: header.saveRequested()
            }
            SmallButton {
                objectName: "workspaceHeaderOutputButton"
                text: "出力先"
                enabled: header.outputFolderAvailable
                onClicked: header.outputFolderRequested()
            }
            SmallButton {
                objectName: "workspaceHeaderAiButton"
                text: header.aiActionLabel
                enabled: !header.running && (header.aiAuthenticated || header.aiLoginAvailable)
                onClicked: header.aiAssistantRequested()
            }
            SmallButton {
                objectName: "workspaceHeaderShortButton"
                text: "ショート作成"
                enabled: header.projectLoaded && !header.running
                onClicked: header.shortWorkspaceRequested()
            }
            Button {
                id: renderButton
                objectName: "workspaceHeaderRenderButton"
                focusPolicy: Qt.TabFocus
                Layout.preferredWidth: 106
                Layout.preferredHeight: 32
                enabled: header.projectLoaded && !header.running && header.canRender
                ToolTip.visible: hovered && header.renderBlockReason.length > 0
                ToolTip.text: header.renderBlockReason
                text: header.renderNeedsOutput ? "出力先を選択" : "動画を書き出す"
                onClicked: header.renderRequested()
                contentItem: Text {
                    text: renderButton.text
                    color: renderButton.enabled ? "#FFFFFF" : "#6E7681"
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 9
                    font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                }
                background: Rectangle {
                    radius: 7
                    color: renderButton.enabled ? header.accentColor : header.raisedColor
                    border.color: renderButton.enabled ? header.accentColor : header.borderColor
                }
            }
        }
    }
}
