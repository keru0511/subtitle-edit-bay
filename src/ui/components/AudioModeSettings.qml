pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    required property var backend
    property color panelColor: "#121715"
    property color raisedColor: "#19201D"
    property color borderColor: "#2A3530"
    property color textColor: "#F4F1E8"
    property color mutedColor: "#8E9B94"
    property color accentColor: "#C8FF3D"
    property color warningColor: "#FFB547"
    property real savedContentY: 0
    property bool restoringContentY: true
    property real pendingContentY: -1
    signal contentYChangedByUser(real value)

    function preserveChannelScroll() {
        if (!root.restoringContentY || root.pendingContentY < 0)
            root.pendingContentY = channelList.contentY
        root.restoringContentY = true
    }

    function restoreChannelScroll() {
        var targetY = root.pendingContentY >= 0
            ? root.pendingContentY
            : root.savedContentY
        var maximumY = Math.max(0, channelList.contentHeight - channelList.height)
        channelList.contentY = Math.max(0, Math.min(targetY, maximumY))
        root.pendingContentY = -1
        root.restoringContentY = false
    }

    function updateChannel(index, changes) {
        root.preserveChannelScroll()
        root.backend.updateAudioMixChannel(index, changes)
        channelScrollRestoreTimer.restart()
    }

    function resetMixer() {
        root.preserveChannelScroll()
        root.backend.resetAudioMixer()
        channelScrollRestoreTimer.restart()
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            Text { text: "音量設定"; color: root.textColor; font.family: "Yu Gothic UI"; font.pixelSize: 13; font.weight: Font.Bold }
            Item { Layout.fillWidth: true }
            Text { text: root.backend.audioMixerChannels.length + "トラック"; color: root.accentColor; font.family: "Yu Gothic UI"; font.pixelSize: 9 }
        }
        Text {
            Layout.fillWidth: true
            text: "共通プレビューで完成音を確認できます"
            color: root.mutedColor
            font.family: "Yu Gothic UI"
            font.pixelSize: 9
            wrapMode: Text.Wrap
        }
        RowLayout {
            Layout.fillWidth: true
            SmallButton {
                objectName: "workspaceAudioResetButton"
                Layout.fillWidth: true
                text: "リセット"
                enabled: !root.backend.running
                onClicked: root.resetMixer()
            }
            SmallButton {
                objectName: "workspaceAudioSaveButton"
                Layout.fillWidth: true
                text: "保存"
                enabled: !root.backend.running
                onClicked: root.backend.saveProject()
            }
        }
        SmallButton {
            objectName: "workspaceAudioRebuildPreviewButton"
            Layout.fillWidth: true
            text: root.backend.audioPreviewPreparing ? "準備中…" : "音声プレビューを作り直す"
            enabled: !root.backend.running && !root.backend.audioPreviewPreparing
            onClicked: root.backend.clearAudioPreviewCache()
        }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.borderColor }

        ListView {
            id: channelList
            objectName: "workspaceAudioChannelList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 8
            boundsBehavior: Flickable.StopAtBounds
            model: root.backend.audioMixerChannels
            onContentYChanged: {
                if (!root.restoringContentY)
                    root.contentYChangedByUser(contentY)
            }
            onContentHeightChanged: {
                if (root.restoringContentY && root.pendingContentY >= 0)
                    channelScrollRestoreTimer.restart()
            }

            Timer {
                id: channelScrollRestoreTimer
                interval: 0
                running: true
                repeat: false
                onTriggered: {
                    if (root.pendingContentY < 0)
                        root.pendingContentY = root.savedContentY
                    root.restoreChannelScroll()
                }
            }

            delegate: Rectangle {
                id: channelCard
                required property int index
                required property var modelData
                width: channelList.width
                height: 144
                radius: 8
                color: channelCard.modelData.enabled ? root.raisedColor : root.panelColor
                border.color: channelCard.modelData.solo
                    ? root.accentColor
                    : (channelCard.modelData.muted ? root.warningColor : root.borderColor)
                opacity: channelCard.modelData.enabled ? 1 : 0.62

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 4
                    RowLayout {
                        Layout.fillWidth: true
                        Text { Layout.fillWidth: true; text: channelCard.modelData.label; color: root.textColor; font.family: "Yu Gothic UI"; font.pixelSize: 10; font.weight: Font.Bold; elide: Text.ElideRight }
                        CheckBox {
                            objectName: "workspaceAudioEnabledCheck"
                            text: "使用"
                            checked: Boolean(channelCard.modelData.enabled)
                            enabled: !root.backend.running
                            onToggled: root.updateChannel(channelCard.index, {"enabled": checked})
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: "音量"; color: root.mutedColor; font.family: "Yu Gothic UI"; font.pixelSize: 9 }
                        Slider {
                            id: volumeSlider
                            objectName: "workspaceAudioVolumeSlider"
                            Layout.fillWidth: true
                            from: 0
                            to: 200
                            stepSize: 1
                            value: Number(channelCard.modelData.volume_percent || 0)
                            enabled: !root.backend.running && channelCard.modelData.enabled
                            onMoved: root.updateChannel(channelCard.index, {"volume_percent": value})
                        }
                        Text { text: Math.round(volumeSlider.value) + "%"; color: root.textColor; font.family: "Cascadia Mono"; font.pixelSize: 9 }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        SmallButton {
                            objectName: "workspaceAudioMuteButton"
                            Layout.fillWidth: true
                            text: channelCard.modelData.muted ? "ミュート解除" : "ミュート"
                            enabled: !root.backend.running
                            onClicked: root.updateChannel(channelCard.index, {"muted": !channelCard.modelData.muted})
                        }
                        SmallButton {
                            objectName: "workspaceAudioSoloButton"
                            Layout.fillWidth: true
                            text: channelCard.modelData.solo ? "ソロ解除" : "ソロ"
                            enabled: !root.backend.running
                            onClicked: root.updateChannel(channelCard.index, {"solo": !channelCard.modelData.solo})
                        }
                    }
                    Text { Layout.fillWidth: true; text: channelCard.modelData.kind === "external" ? "外部音声" : "動画音声"; color: root.mutedColor; font.family: "Yu Gothic UI"; font.pixelSize: 8; horizontalAlignment: Text.AlignRight }
                }
            }
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
        }

        Rectangle {
            objectName: "workspaceAudioMasterMeter"
            Layout.fillWidth: true
            Layout.preferredHeight: 9
            radius: 4
            color: "#070908"
            border.color: root.borderColor
            Rectangle {
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.margins: 2
                width: Math.max(0, (parent.width - 4) * Number(root.backend.audioMasterLevel || 0))
                radius: 2
                color: root.backend.audioLimiterReductionDb > 0.01 ? root.warningColor : root.accentColor
                Behavior on width { NumberAnimation { duration: 45 } }
            }
        }
        Text {
            Layout.fillWidth: true
            text: "自動調整 " + Number(root.backend.audioLimiterReductionDb || 0).toFixed(1) + " dB"
            color: root.backend.audioLimiterReductionDb > 0.01 ? root.warningColor : root.mutedColor
            font.family: "Yu Gothic UI"
            font.pixelSize: 8
            horizontalAlignment: Text.AlignRight
        }
    }
}
