pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup {
    id: root

    required property var appBackend
    required property var colors
    readonly property var referenceAudioValue: referenceCombo.currentValue
    readonly property var referenceTrackValue: trackCombo.currentValue
    property alias manualOffsetText: manualOffsetField.text
    signal sourceDropped(var drop)
    signal speakerColorRequested(int index, string color)
    signal saveAsRequested

    component SourcePanelTitle: PanelTitle {
        titleColor: root.colors.textMuted
    }

    component SourceButton: SmallButton {
        textPrimary: root.colors.textPrimary
        disabledText: "#59635D"
        borderColor: root.colors.border
        focusColor: root.colors.acid
        defaultBackground: "#1A211E"
        hoverBackground: "#27312C"
        pressedBackground: "#303B35"
    }

    component SourceTimeField: TimeField {
        textPrimary: root.colors.textPrimary
        borderColor: root.colors.border
        focusColor: root.colors.acid
        inputBackground: "#101512"
    }

    function alignmentStatusLabel(value) {
        var labels = {
            "未解析": "未解析",
            "running": "調整中",
            "success": "調整完了",
            "completed": "調整完了",
            "error": "調整に失敗しました"
        };
        var text = String(value || "");
        var key = text.toLowerCase();
        if (labels[key] !== undefined)
            return labels[key];
        return /^[\u3040-\u30ff\u3400-\u9fff]/.test(text) ? text : "結果を確認中";
    }

    objectName: "sourcePopup"
    anchors.centerIn: Overlay.overlay
    width: Math.min(620, Overlay.overlay.width - 32)
    height: Math.min(680, Overlay.overlay.height - 32)
    modal: true
    focus: true
    closePolicy: root.appBackend.running ? Popup.NoAutoClose : Popup.CloseOnEscape
    onOpened: root.appBackend.beginSourceRelink()
    onClosed: root.appBackend.finishSourceRelink()
    background: Rectangle {
        radius: 14
        color: root.colors.panel
        border.color: root.colors.border
    }
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 18
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            Text {
                text: "素材設定"
                color: root.colors.textPrimary
                font.family: "Yu Gothic UI"
                font.pixelSize: 17
                font.weight: Font.Bold
                Layout.fillWidth: true
            }
            ToolButton {
                text: "×"
                enabled: !root.appBackend.running
                onClicked: root.close()
            }
        }
        ScrollView {
            id: sourceSettingsScrollView
            objectName: "sourceSettingsScrollView"
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentHeight: Math.max(sourceSettingsContent.implicitHeight, sourceSettingsContent.childrenRect.y + sourceSettingsContent.childrenRect.height)
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ScrollBar.vertical: ScrollBar {
                objectName: "sourceSettingsVerticalScrollBar"
                policy: ScrollBar.AsNeeded
            }
            ColumnLayout {
                id: sourceSettingsContent
                objectName: "sourceSettingsContent"
                width: sourceSettingsScrollView.availableWidth
                spacing: 12
                Rectangle {
                    objectName: "sourceDependencyWarning"
                    Layout.fillWidth: true
                    Layout.preferredHeight: visible ? 62 : 0
                    visible: !root.appBackend.dependencyStatus.ready
                    radius: 8
                    color: "#30201C"
                    border.color: root.colors.danger
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 9
                        Text {
                            Layout.fillWidth: true
                            text: "不足ツール: " + root.appBackend.dependencyStatus.missing.join(", ")
                            color: root.colors.textPrimary
                            font.family: "Yu Gothic UI"
                            font.pixelSize: 10
                            wrapMode: Text.Wrap
                        }
                        SourceButton {
                            text: "再確認"
                            enabled: !root.appBackend.running
                            onClicked: root.appBackend.refreshDependencies()
                        }
                    }
                }
                Rectangle {
                    objectName: "sourcePopupDropTarget"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 68
                    radius: 9
                    color: sourcePopupDropArea.containsDrag ? "#263326" : root.colors.raised
                    border.color: sourcePopupDropArea.containsDrag ? root.colors.acid : root.colors.border
                    border.width: sourcePopupDropArea.containsDrag ? 2 : 1
                    Column {
                        anchors.centerIn: parent
                        spacing: 3
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: "動画・話者音声をここにドロップ"
                            color: root.colors.textPrimary
                            font.family: "Yu Gothic UI"
                            font.pixelSize: 11
                            font.weight: Font.DemiBold
                        }
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: "動画1件と複数の音声を自動判別します"
                            color: root.colors.textMuted
                            font.family: "Yu Gothic UI"
                            font.pixelSize: 9
                        }
                    }
                    DropArea {
                        id: sourcePopupDropArea
                        objectName: "sourcePopupDropArea"
                        anchors.fill: parent
                        enabled: !root.appBackend.running
                        onEntered: function (drag) {
                            drag.accepted = drag.hasUrls;
                        }
                        onDropped: function (drop) {
                            root.sourceDropped(drop);
                        }
                    }
                }
                SourcePanelTitle {
                    text: "動画"
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        objectName: "sourceVideoPathText"
                        Layout.fillWidth: true
                        text: root.appBackend.sourceSelection.video || "未選択"
                        color: root.colors.textMuted
                        elide: Text.ElideMiddle
                    }
                    SourceButton {
                        text: "選択"
                        enabled: !root.appBackend.running
                        onClicked: root.appBackend.browseVideoFile()
                    }
                }
                SourcePanelTitle {
                    text: "話者音声"
                }
                ListView {
                    id: sourceAudioList
                    objectName: "sourceAudioList"
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.max(48, Math.min(124, contentHeight))
                    clip: true
                    spacing: 5
                    model: root.appBackend.speakers
                    delegate: Rectangle {
                        id: sourceAudioDelegate
                        required property int index
                        required property var modelData
                        width: sourceAudioList.width
                        height: 38
                        radius: 7
                        color: root.colors.raised
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 7
                            Button {
                                objectName: "sourceSpeakerColorButton"
                                Layout.preferredWidth: 24
                                Layout.preferredHeight: 24
                                enabled: !root.appBackend.running
                                onClicked: root.speakerColorRequested(sourceAudioDelegate.index, sourceAudioDelegate.modelData.color)
                                contentItem: Rectangle {
                                    radius: 4
                                    color: sourceAudioDelegate.modelData.color
                                    border.color: root.colors.textPrimary
                                }
                                background: Rectangle {
                                    radius: 5
                                    color: "transparent"
                                    border.color: root.colors.border
                                }
                                ToolTip.visible: hovered
                                ToolTip.text: "字幕色を変更"
                            }
                            Text {
                                Layout.fillWidth: true
                                text: sourceAudioDelegate.modelData.file_name
                                color: root.colors.textPrimary
                                elide: Text.ElideMiddle
                            }
                            ToolButton {
                                objectName: "sourceAudioRemoveButton-" + sourceAudioDelegate.index
                                text: "×"
                                Layout.preferredWidth: 24
                                Layout.preferredHeight: 24
                                enabled: !root.appBackend.running
                                onClicked: root.appBackend.removeAudioFile(sourceAudioDelegate.index)
                            }
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    SourceButton {
                        objectName: "sourceAudioAddButton"
                        text: "音声を追加"
                        enabled: !root.appBackend.running
                        onClicked: root.appBackend.browseAudioFiles()
                    }
                    SourceButton {
                        objectName: "sourceAudioClearButton"
                        text: "クリア"
                        enabled: !root.appBackend.running
                        onClicked: root.appBackend.clearAudioFiles()
                    }
                    Item {
                        Layout.fillWidth: true
                    }
                }
                SourcePanelTitle {
                    text: "文字起こし対象と音声同期"
                }
                RowLayout {
                    Layout.fillWidth: true
                    ColumnLayout {
                        Layout.fillWidth: true
                        Text {
                            text: "動画音声トラック"
                            color: root.colors.textMuted
                            font.pixelSize: 9
                        }
                        ComboBox {
                            id: trackCombo
                            objectName: "videoAudioTrackCombo"
                            Layout.fillWidth: true
                            model: root.appBackend.audioTracks
                            textRole: "label"
                            valueRole: "selector"
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        Text {
                            text: "同期の基準音声"
                            color: root.colors.textMuted
                            font.pixelSize: 9
                        }
                        ComboBox {
                            id: referenceCombo
                            objectName: "referenceAudioCombo"
                            Layout.fillWidth: true
                            model: root.appBackend.speakers
                            textRole: "file_name"
                            valueRole: "path"
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: "手動補正（秒）"
                        color: root.colors.textMuted
                        font.pixelSize: 9
                    }
                    SourceTimeField {
                        id: manualOffsetField
                        objectName: "manualAlignmentOffsetField"
                        Layout.fillWidth: true
                        text: "0.000"
                        validator: DoubleValidator {
                            bottom: -120
                            top: 120
                            decimals: 3
                        }
                    }
                    SourceButton {
                        objectName: "analyzeAlignmentButton"
                        text: root.appBackend.alignmentBusy ? "調整中" : "音声のずれを自動調整"
                        enabled: !root.appBackend.running && !root.appBackend.alignmentBusy && root.appBackend.speakers.length > 0 && root.appBackend.sourceSelection.video
                        onClicked: root.appBackend.analyzeAlignment(referenceCombo.currentValue || "", trackCombo.currentValue || "", Number(manualOffsetField.text || 0))
                    }
                }
                Text {
                    Layout.fillWidth: true
                    text: root.alignmentStatusLabel(root.appBackend.alignmentResult.status) + (root.appBackend.alignmentResult.offset !== undefined ? "  " + Number(root.appBackend.alignmentResult.offset).toFixed(3) + "秒" : "")
                    color: root.colors.textMuted
                    font.pixelSize: 9
                    font.family: "Yu Gothic UI"
                }
                SourcePanelTitle {
                    text: "プロジェクト保存先"
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        objectName: "projectSavePathText"
                        Layout.fillWidth: true
                        text: root.appBackend.projectSavePath || "動画の選択後に決まります"
                        color: root.colors.textMuted
                        elide: Text.ElideMiddle
                    }
                    SourceButton {
                        objectName: "projectSaveAsButton"
                        text: root.appBackend.projectLoaded ? "別名保存" : "保存先を選んで作成"
                        enabled: !root.appBackend.running && Boolean(root.appBackend.projectSavePath)
                        onClicked: root.saveAsRequested()
                    }
                }
                SourcePanelTitle {
                    text: "完成動画の出力先"
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        objectName: "videoOutputDirectoryText"
                        Layout.fillWidth: true
                        text: root.appBackend.videoOutputDirectory || "書き出すときに選択できます"
                        color: root.colors.textMuted
                        elide: Text.ElideMiddle
                    }
                    SourceButton {
                        objectName: "videoOutputDirectoryButton"
                        text: "選択"
                        enabled: !root.appBackend.running
                        onClicked: root.appBackend.browseOutputDirectory()
                    }
                }
                Item {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 8
                }
            }
        }
        RowLayout {
            objectName: "sourcePopupFooter"
            Layout.fillWidth: true
            Item {
                Layout.fillWidth: true
            }
            Button {
                objectName: "sourceRelinkButton"
                text: "素材を再指定"
                enabled: root.appBackend.projectLoaded && !root.appBackend.running
                onClicked: root.appBackend.relinkProjectSources()
            }
            Button {
                objectName: "sourceDoneButton"
                text: "完了"
                enabled: !root.appBackend.running
                onClicked: root.close()
            }
        }
    }
}
