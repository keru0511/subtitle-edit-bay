pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup {
    id: root

    required property var appBackend
    required property var colors
    required property int defaultSubtitleFontSize
    property alias fontSizePercent: fontSizeSpin.value
    property alias outlineColor: outlineColorButton.colorValue
    property alias outlineThickness: outlineThicknessSpin.value
    readonly property string selectedDevice: deviceCombo.currentText
    readonly property int selectedFontSize: Math.max(3, Math.round(root.defaultSubtitleFontSize * root.fontSizePercent / 100))

    signal saveRequested
    signal outlineColorRequested(string currentColor)

    component SettingsPanelTitle: PanelTitle {
        titleColor: root.colors.textMuted
    }

    component SettingsButton: SmallButton {
        textPrimary: root.colors.textPrimary
        disabledText: "#59635D"
        borderColor: root.colors.border
        focusColor: root.colors.acid
        defaultBackground: "#1A211E"
        hoverBackground: "#27312C"
        pressedBackground: "#303B35"
    }

    component SettingsTimeField: TimeField {
        textPrimary: root.colors.textPrimary
        borderColor: root.colors.border
        focusColor: root.colors.acid
        inputBackground: "#101512"
    }

    function coalesceSetting(value, fallback) {
        return value === undefined || value === null ? fallback : value;
    }

    function settingsValues() {
        return {
            "model": modelCombo.currentText,
            "device": deviceCombo.currentText,
            "compute_type": deviceCombo.currentText === "cuda" ? "float16" : "int8",
            "language": "ja",
            "nvenc_cq": qualitySpin.value,
            "x264_crf": qualitySpin.value,
            "subtitle_font_size": root.selectedFontSize,
            "subtitle_outline_color": root.outlineColor,
            "subtitle_outline_thickness": root.outlineThickness,
            "subtitle_volume_scale_percent": volumeScaleSpin.value,
            "subtitle_max_gap_seconds": Number(gapField.text),
            "subtitle_end_padding_seconds": Number(paddingField.text),
            "subtitle_min_duration_seconds": Number(minDurationField.text),
            "audio_normalize": normalizeSwitch.checked,
            "audio_target_lufs": Number(lufsField.text),
            "cut_no_speech": silenceSwitch.checked,
            "no_speech_min_seconds": Number(silenceField.text),
            "speech_padding_seconds": Number(speechPaddingField.text),
            "speech_threshold_db": String(Number(speechThresholdField.text)) + "dB",
            "postprocess_workers": workersSpin.value
        };
    }

    function applySettings(value) {
        qualitySpin.value = Number(root.coalesceSetting(value.nvenc_cq, 18));
        fontSizeSpin.value = Math.round(Number(root.coalesceSetting(value.subtitle_font_size, root.defaultSubtitleFontSize)) / root.defaultSubtitleFontSize * 100);
        outlineColorButton.colorValue = String(root.coalesceSetting(value.subtitle_outline_color, "#000000"));
        outlineThicknessSpin.value = Number(value.subtitle_outline_thickness === undefined ? 3 : value.subtitle_outline_thickness);
        volumeScaleSpin.value = Number(value.subtitle_volume_scale_percent === undefined ? 20 : value.subtitle_volume_scale_percent);
        gapField.text = Number(root.coalesceSetting(value.subtitle_max_gap_seconds, 0.1)).toFixed(2);
        paddingField.text = Number(root.coalesceSetting(value.subtitle_end_padding_seconds, 0.08)).toFixed(2);
        minDurationField.text = Number(root.coalesceSetting(value.subtitle_min_duration_seconds, 0.35)).toFixed(2);
        silenceField.text = Number(root.coalesceSetting(value.no_speech_min_seconds, 1.2)).toFixed(1);
        speechPaddingField.text = Number(root.coalesceSetting(value.speech_padding_seconds, 0.25)).toFixed(2);
        speechThresholdField.text = parseFloat(String(root.coalesceSetting(value.speech_threshold_db, "-40dB"))).toFixed(0);
        lufsField.text = Number(root.coalesceSetting(value.audio_target_lufs, -16)).toFixed(0);
        normalizeSwitch.checked = value.audio_normalize === undefined ? true : value.audio_normalize;
        silenceSwitch.checked = Boolean(value.cut_no_speech);
        workersSpin.value = Number(root.coalesceSetting(value.postprocess_workers, 4));
        modelCombo.currentIndex = Math.max(0, modelCombo.find(root.coalesceSetting(value.model, "large-v3")));
        deviceCombo.currentIndex = Math.max(0, deviceCombo.find(root.coalesceSetting(value.device, "cuda")));
    }

    objectName: "advancedSettingsPopup"
    padding: 12
    modal: false
    focus: true
    // The toggle button is outside this non-modal popup. Let the toggle
    // handler own the close action so an outside press cannot close the
    // popup before the same press reopens it through onSettingsRequested.
    closePolicy: Popup.CloseOnEscape
    contentItem: ColumnLayout {
        objectName: "advancedSettingsPanel"
        spacing: 10

        SettingsButton {
            objectName: "settingsPopupSaveButton"
            Layout.fillWidth: true
            text: "設定を保存"
            enabled: !root.appBackend.running
            onClicked: root.saveRequested()
        }
        SettingsButton {
            objectName: "settingsPopupCloseButton"
            Layout.fillWidth: true
            text: "閉じる"
            onClicked: root.close()
        }

        ScrollView {
            id: advancedSettingsScrollView
            objectName: "advancedSettingsScrollView"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            contentHeight: advancedSettingsContent.implicitHeight
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ScrollBar.vertical: ScrollBar {
                objectName: "advancedSettingsVerticalScrollBar"
                policy: ScrollBar.AlwaysOn
            }

            ColumnLayout {
                id: advancedSettingsContent
                objectName: "advancedSettingsContent"
                width: advancedSettingsScrollView.availableWidth
                spacing: 10
                SettingsPanelTitle {
                    text: "文字起こしエンジン"
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: "処理デバイス"
                        color: root.colors.textPrimary
                        Layout.fillWidth: true
                    }
                    ComboBox {
                        id: deviceCombo
                        objectName: "deviceCombo"
                        model: ["cuda", "cpu"]
                        Layout.preferredWidth: 110
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: "Whisperモデル"
                        color: root.colors.textPrimary
                        Layout.fillWidth: true
                    }
                    ComboBox {
                        id: modelCombo
                        objectName: "modelCombo"
                        model: ["large-v3", "medium", "small"]
                        Layout.preferredWidth: 130
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: "CPU並列数"
                        color: root.colors.textPrimary
                        Layout.fillWidth: true
                    }
                    SpinBox {
                        id: workersSpin
                        from: 1
                        to: 16
                        value: 4
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 1
                    color: root.colors.border
                }
                SettingsPanelTitle {
                    text: "字幕"
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: "基準文字サイズ"
                        color: root.colors.textPrimary
                        Layout.fillWidth: true
                    }
                    SpinBox {
                        id: fontSizeSpin
                        objectName: "fontSizeSpin"
                        from: 10
                        to: 900
                        value: 100
                    }
                    Text {
                        text: "%"
                        color: root.colors.textMuted
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: "縁取り色"
                        color: root.colors.textPrimary
                        Layout.fillWidth: true
                    }
                    Button {
                        id: outlineColorButton
                        objectName: "outlineColorButton"
                        property string colorValue: "#000000"

                        Layout.preferredWidth: 112
                        Layout.preferredHeight: 32
                        onClicked: root.outlineColorRequested(outlineColorButton.colorValue)
                        contentItem: Row {
                            spacing: 7
                            Rectangle {
                                width: 20
                                height: 20
                                radius: 4
                                color: outlineColorButton.colorValue
                                border.color: root.colors.border
                            }
                            Text {
                                text: outlineColorButton.colorValue
                                color: root.colors.textPrimary
                                font.pixelSize: 10
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }
                        background: Rectangle {
                            radius: 6
                            color: root.colors.raised
                            border.color: root.colors.border
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: "縁取り太さ"
                        color: root.colors.textPrimary
                        Layout.fillWidth: true
                    }
                    SpinBox {
                        id: outlineThicknessSpin
                        objectName: "outlineThicknessSpin"
                        from: 0
                        to: 20
                        value: 3
                    }
                    Text {
                        text: "px"
                        color: root.colors.textMuted
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: "字幕の音量バランス"
                        color: root.colors.textPrimary
                        Layout.fillWidth: true
                    }
                    SpinBox {
                        id: volumeScaleSpin
                        objectName: "volumeScaleSpin"
                        from: 0
                        to: 50
                        value: 20
                    }
                    Text {
                        text: "%"
                        color: root.colors.textMuted
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: "単語間隔"
                        color: root.colors.textPrimary
                        Layout.fillWidth: true
                    }
                    SettingsTimeField {
                        id: gapField
                        Layout.preferredWidth: 76
                        text: "0.10"
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: "終了余白"
                        color: root.colors.textPrimary
                        Layout.fillWidth: true
                    }
                    SettingsTimeField {
                        id: paddingField
                        Layout.preferredWidth: 76
                        text: "0.08"
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: "最短表示時間"
                        color: root.colors.textPrimary
                        Layout.fillWidth: true
                    }
                    SettingsTimeField {
                        id: minDurationField
                        Layout.preferredWidth: 76
                        text: "0.35"
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 1
                    color: root.colors.border
                }
                SettingsPanelTitle {
                    text: "動画・音声"
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: "動画書き出し"
                        color: root.colors.textPrimary
                        Layout.fillWidth: true
                    }
                    Text {
                        objectName: "automaticVideoCodecText"
                        text: root.appBackend.dependencyStatus.nvenc ? "GPU（自動）" : "CPU（自動）"
                        color: root.colors.acid
                        font.family: "Yu Gothic UI"
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: "画質"
                        color: root.colors.textPrimary
                        Layout.fillWidth: true
                    }
                    SpinBox {
                        id: qualitySpin
                        objectName: "qualitySpin"
                        from: 14
                        to: 28
                        value: 18
                    }
                }
                Switch {
                    id: normalizeSwitch
                    objectName: "normalizeSwitch"
                    text: "音量を正規化"
                    checked: true
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: "目標LUFS"
                        color: root.colors.textPrimary
                        Layout.fillWidth: true
                    }
                    SettingsTimeField {
                        id: lufsField
                        objectName: "lufsField"
                        Layout.preferredWidth: 76
                        text: "-16"
                        validator: DoubleValidator {
                            bottom: -30
                            top: -5
                        }
                    }
                }
                Switch {
                    id: silenceSwitch
                    objectName: "silenceSwitch"
                    text: "無音部分をカット"
                }
                RowLayout {
                    Layout.fillWidth: true
                    enabled: silenceSwitch.checked
                    opacity: enabled ? 1 : 0.4
                    Text {
                        text: "最短無音時間"
                        color: root.colors.textPrimary
                        Layout.fillWidth: true
                    }
                    SettingsTimeField {
                        id: silenceField
                        objectName: "silenceField"
                        Layout.preferredWidth: 76
                        text: "1.2"
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    enabled: silenceSwitch.checked
                    opacity: enabled ? 1 : 0.4
                    Text {
                        text: "発話余白"
                        color: root.colors.textPrimary
                        Layout.fillWidth: true
                    }
                    SettingsTimeField {
                        id: speechPaddingField
                        objectName: "speechPaddingField"
                        Layout.preferredWidth: 76
                        text: "0.25"
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    enabled: silenceSwitch.checked
                    opacity: enabled ? 1 : 0.4
                    Text {
                        text: "無音判定閾値"
                        color: root.colors.textPrimary
                        Layout.fillWidth: true
                    }
                    SettingsTimeField {
                        id: speechThresholdField
                        objectName: "speechThresholdField"
                        Layout.preferredWidth: 76
                        text: "-40"
                        validator: DoubleValidator {
                            bottom: -100
                            top: 0
                            decimals: 1
                        }
                    }
                }
                Item {
                    Layout.preferredHeight: 6
                }
            }
        }
    }
}
