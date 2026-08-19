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

        var bgm = s.bgm || {}
        bgmFileLabel.text = bgm.path ? bgm.path.toString() : "BGM ファイルを選択"
        bgmIn.text = bgm["in"] ? bgm["in"].toString() : "0"
        bgmOut.text = bgm.out ? bgm.out.toString() : "0"
        bgmStart.text = bgm.start ? bgm.start.toString() : "0"
        bgmVolumeSlider.value = (bgm.volume !== undefined) ? bgm.volume : 0.3
    }

    function _sendBgmUpdate(changes) {
        if (settingsRoot.appBackend) {
            settingsRoot.appBackend.setShortVideoBgm(changes)
        }
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

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 1
        color: "#2A3530"
    }

    Text {
        text: "BGM"
        color: "#E8EFEA"
        font.family: "Yu Gothic UI"
        font.pixelSize: 14
        font.weight: Font.Bold
    }

    Button {
        id: bgmBrowseButton
        objectName: "shortModeBgmBrowseButton"
        Layout.fillWidth: true
        contentItem: Text {
            id: bgmFileLabel
            objectName: "shortModeBgmFileLabel"
            text: "BGM ファイルを選択"
            color: "#F4F1E8"
            elide: Text.ElideMiddle
            verticalAlignment: Text.AlignVCenter
            horizontalAlignment: Text.AlignLeft
        }
        onClicked: {
            if (settingsRoot.appBackend) {
                settingsRoot.appBackend.browseShortModeBgm()
            }
        }
    }

    GridLayout {
        columns: 4
        columnSpacing: 10
        rowSpacing: 6

        Text { text: "IN"; color: "#F4F1E8" }
        Text { text: "OUT"; color: "#F4F1E8" }
        Text { text: "START"; color: "#F4F1E8" }
        Text { text: ""; color: "#F4F1E8" }

        TimeField {
            id: bgmIn
            objectName: "shortModeBgmInField"
            Layout.preferredWidth: 70
            text: "0"
            onEditingFinished: _sendBgmUpdate({"in": parseFloat(text) || 0})
        }
        TimeField {
            id: bgmOut
            objectName: "shortModeBgmOutField"
            Layout.preferredWidth: 70
            text: "0"
            onEditingFinished: _sendBgmUpdate({"out": parseFloat(text) || 0})
        }
        TimeField {
            id: bgmStart
            objectName: "shortModeBgmStartField"
            Layout.preferredWidth: 70
            text: "0"
            onEditingFinished: _sendBgmUpdate({"start": parseFloat(text) || 0})
        }
        ColumnLayout {
            spacing: 2
            Text { text: "VOLUME"; color: "#F4F1E8"; font.pixelSize: 9 }
            Slider {
                id: bgmVolumeSlider
                objectName: "shortModeBgmVolumeSlider"
                from: 0.0; to: 1.0; stepSize: 0.05
                onValueChanged: _sendBgmUpdate({"volume": value})
            }
        }
    }

    Component.onCompleted: refresh()
}
