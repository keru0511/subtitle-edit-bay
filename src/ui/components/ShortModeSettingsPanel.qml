import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

ColumnLayout {
    id: settingsRoot
    objectName: "shortModeSettingsPanel"
    spacing: 10

    property var appBackend: null

    function refresh() {
        if (!settingsRoot.appBackend) return
        var s = settingsRoot.appBackend.shortVideoSettings
        fitCombo.currentIndex = fitCombo.model.indexOf(s.global_fit)
        bgColorField.text = s.global_background_color
        transitionCombo.currentIndex = transitionCombo.model.indexOf(s.transition.type)
        transitionDuration.value = s.transition.duration
        scaleSpin.value = s.subtitle_scale_percent
    }

    Connections {
        target: settingsRoot.appBackend
        function onShortVideoChanged() { settingsRoot.refresh() }
    }

    Text {
        text: "グローバル設定"
        color: "#E8EFEA"
        font.family: "Yu Gothic UI"
        font.pixelSize: 14
        font.weight: Font.Bold
    }

    RowLayout {
        Layout.fillWidth: true
        Text { text: "画面構成"; color: "#F4F1E8"; Layout.fillWidth: true }
        ComboBox {
            id: fitCombo
            objectName: "shortModeGlobalFitCombo"
            model: ["cover", "contain", "blur"]
            onActivated: {
                if (settingsRoot.appBackend) {
                    settingsRoot.appBackend.setShortVideoGlobalFit(fitCombo.currentText)
                }
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        Text { text: "背景色"; color: "#F4F1E8"; Layout.fillWidth: true }
        TextField {
            id: bgColorField
            objectName: "shortModeBackgroundColorField"
            Layout.preferredWidth: 80
            text: "000000"
            onEditingFinished: {
                if (settingsRoot.appBackend) {
                    var raw = text.replace("#", "")
                    if (raw.length === 6) {
                        settingsRoot.appBackend.setShortVideoGlobalBackgroundColor(raw)
                    }
                }
            }
        }
        Rectangle {
            Layout.preferredWidth: 30
            Layout.preferredHeight: 24
            radius: 4
            color: bgColorField.text.startsWith("#") ? bgColorField.text : "#" + bgColorField.text
            border.color: "#2A3530"
        }
        Button {
            text: "..."
            onClicked: bgColorDialog.open()
        }
    }

    ColorDialog {
        id: bgColorDialog
        title: "背景色を選択"
        onAccepted: {
            if (settingsRoot.appBackend) {
                var hex = selectedColor.toString().replace("#", "")
                settingsRoot.appBackend.setShortVideoGlobalBackgroundColor(hex)
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        Text { text: "トランジション"; color: "#F4F1E8"; Layout.fillWidth: true }
        ComboBox {
            id: transitionCombo
            objectName: "shortModeTransitionCombo"
            model: ["crossfade", "fade", "cut"]
            onActivated: {
                if (settingsRoot.appBackend) {
                    settingsRoot.appBackend.setShortVideoTransition(transitionCombo.currentText, transitionDuration.value)
                }
            }
        }
        Slider {
            id: transitionDuration
            from: 0; to: 2.0; stepSize: 0.1
            onValueChanged: {
                if (settingsRoot.appBackend) {
                    settingsRoot.appBackend.setShortVideoTransition(transitionCombo.currentText, value)
                }
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        Text { text: "字幕スケール"; color: "#F4F1E8"; Layout.fillWidth: true }
        SpinBox {
            id: scaleSpin
            objectName: "shortModeSubtitleScaleSpin"
            from: 50; to: 300
            textFromValue: function(value) { return value + "%" }
            valueFromText: function(text) { return parseInt(text) || 150 }
            onValueModified: {
                if (settingsRoot.appBackend) {
                    settingsRoot.appBackend.setShortVideoSubtitleScale(value)
                }
            }
        }
    }

    Component.onCompleted: refresh()
}
