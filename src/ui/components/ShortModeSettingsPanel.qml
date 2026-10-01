import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

ColumnLayout {
    id: settingsRoot
    objectName: "shortModeSettingsPanel"
    spacing: 10

    property var appBackend: null
    property bool refreshingSettings: false
    property bool committingDrafts: false
    readonly property bool editingEnabled: settingsRoot.appBackend && !settingsRoot.appBackend.running
    property var fitOptions: [
        { "label": "画面いっぱい", "value": "cover" },
        { "label": "全体を表示", "value": "contain" },
        { "label": "ぼかし背景", "value": "blur" }
    ]
    property var transitionOptions: [
        { "label": "クロスフェード", "value": "crossfade" },
        { "label": "フェード", "value": "fade" },
        { "label": "カット", "value": "cut" }
    ]

    function indexForValue(options, value) {
        for (var index = 0; index < options.length; index += 1) {
            if (options[index].value === value)
                return index
        }
        return 0
    }

    function hasIncompleteInput() {
        return !bgColorField.acceptableInput || !bgmIn.acceptableInput
            || !bgmOut.acceptableInput || !bgmStart.acceptableInput
    }

    function commitPendingEdits() {
        if (settingsRoot.committingDrafts)
            return true
        if (!settingsRoot.appBackend || settingsRoot.appBackend.running || settingsRoot.hasIncompleteInput())
            return false
        settingsRoot.committingDrafts = true
        try {
            if (bgColorField.draftEdited) {
                bgColorField.draftEdited = false
                if (!settingsRoot.appBackend.shortVideo.setShortVideoGlobalBackgroundColor(bgColorField.text)) {
                    bgColorField.draftEdited = true
                    return false
                }
            }
            var inEdited = bgmIn.draftEdited
            var outEdited = bgmOut.draftEdited
            var startEdited = bgmStart.draftEdited
            var bgmChanges = {}
            if (inEdited) bgmChanges["in"] = Number(bgmIn.text)
            if (outEdited) bgmChanges["out"] = Number(bgmOut.text)
            if (startEdited) bgmChanges["start"] = Number(bgmStart.text)
            if (inEdited || outEdited || startEdited) {
                bgmIn.draftEdited = false
                bgmOut.draftEdited = false
                bgmStart.draftEdited = false
                if (!settingsRoot.appBackend.shortVideo.setShortVideoBgm(bgmChanges)) {
                    bgmIn.draftEdited = inEdited
                    bgmOut.draftEdited = outEdited
                    bgmStart.draftEdited = startEdited
                    return false
                }
            }
            return true
        } finally {
            settingsRoot.committingDrafts = false
        }
    }

    function refresh() {
        if (!settingsRoot.appBackend || settingsRoot.refreshingSettings) return
        // 表示値の反映で発火する変更通知をユーザーの編集として扱わない。
        settingsRoot.refreshingSettings = true
        try {
            var s = settingsRoot.appBackend.shortVideo.shortVideoSettings
            fitCombo.currentIndex = settingsRoot.indexForValue(settingsRoot.fitOptions, s.global_fit)
            if (!bgColorField.draftEdited) bgColorField.text = s.global_background_color
            transitionCombo.currentIndex = settingsRoot.indexForValue(settingsRoot.transitionOptions, s.transition.type)
            if (!transitionDuration.pressed) transitionDuration.value = s.transition.duration
            scaleSpin.value = s.subtitle_scale_percent

            var bgm = s.bgm || {}
            bgmFileLabel.text = bgm.path ? bgm.path.toString() : "BGM ファイルを選択"
            if (!bgmIn.draftEdited) bgmIn.text = bgm["in"] ? bgm["in"].toString() : "0"
            if (!bgmOut.draftEdited) bgmOut.text = bgm.out ? bgm.out.toString() : "0"
            if (!bgmStart.draftEdited) bgmStart.text = bgm.start ? bgm.start.toString() : "0"
            if (!bgmVolumeSlider.pressed)
                bgmVolumeSlider.value = (bgm.volume !== undefined) ? bgm.volume : 0.3
        } finally {
            settingsRoot.refreshingSettings = false
        }
    }

    function _sendBgmUpdate(changes) {
        if (settingsRoot.appBackend && !settingsRoot.appBackend.running && !settingsRoot.refreshingSettings) {
            settingsRoot.appBackend.shortVideo.setShortVideoBgm(changes)
        }
    }

    Connections {
        target: settingsRoot.appBackend ? settingsRoot.appBackend.shortVideo : null
        function onShortVideoChanged() { settingsRoot.refresh() }
    }

    Text {
        text: "ショート全体の設定"
        color: "#F0F6FC"
        font.family: "Yu Gothic UI"
        font.pixelSize: 14
        font.weight: Font.Bold
    }

    RowLayout {
        Layout.fillWidth: true
        Text { text: "画面構成"; color: "#F0F6FC"; Layout.fillWidth: true }
        ComboBox {
            id: fitCombo
            objectName: "shortModeGlobalFitCombo"
            model: settingsRoot.fitOptions
            textRole: "label"
            valueRole: "value"
            enabled: settingsRoot.editingEnabled
            onActivated: {
                if (settingsRoot.appBackend) {
                    settingsRoot.appBackend.shortVideo.setShortVideoGlobalFit(fitCombo.currentValue)
                }
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        Text { text: "背景色"; color: "#F0F6FC"; Layout.fillWidth: true }
        TextField {
            id: bgColorField
            objectName: "shortModeBackgroundColorField"
            Layout.preferredWidth: 80
            text: "000000"
            enabled: settingsRoot.editingEnabled
            validator: RegularExpressionValidator { regularExpression: /^#?[0-9A-Fa-f]{6}$/ }
            property bool draftEdited: false
            onTextEdited: draftEdited = true
            onEditingFinished: {
                if (settingsRoot.appBackend) {
                    var savedColor = String(settingsRoot.appBackend.shortVideo.shortVideoSettings.global_background_color || "")
                    if (text.replace("#", "").toUpperCase() !== savedColor.replace("#", "").toUpperCase())
                        draftEdited = true
                }
                settingsRoot.commitPendingEdits()
            }
        }
        Rectangle {
            Layout.preferredWidth: 30
            Layout.preferredHeight: 24
            radius: 4
            color: bgColorField.text.startsWith("#") ? bgColorField.text : "#" + bgColorField.text
            border.color: "#30363D"
        }
        Button {
            objectName: "shortModeBackgroundColorButton"
            Layout.preferredWidth: 32
            text: "..."
            enabled: settingsRoot.editingEnabled
            onClicked: bgColorDialog.open()
        }
    }

    ColorDialog {
        id: bgColorDialog
        objectName: "shortModeBackgroundColorDialog"
        title: "背景色を選択"
        onAccepted: {
            if (settingsRoot.appBackend && !settingsRoot.appBackend.running && !settingsRoot.refreshingSettings) {
                var hex = selectedColor.toString().replace("#", "")
                var hadDraft = bgColorField.draftEdited
                bgColorField.draftEdited = false
                if (!settingsRoot.appBackend.shortVideo.setShortVideoGlobalBackgroundColor(hex))
                    bgColorField.draftEdited = hadDraft
            }
        }
    }

    GridLayout {
        columns: 2
        Layout.fillWidth: true
        Text { text: "トランジション"; color: "#F0F6FC"; Layout.fillWidth: true }
        ComboBox {
            id: transitionCombo
            objectName: "shortModeTransitionCombo"
            model: settingsRoot.transitionOptions
            textRole: "label"
            valueRole: "value"
            enabled: settingsRoot.editingEnabled
            onActivated: {
                if (settingsRoot.appBackend && !settingsRoot.appBackend.running && !settingsRoot.refreshingSettings) {
                    settingsRoot.appBackend.shortVideo.setShortVideoTransition(transitionCombo.currentValue, transitionDuration.value)
                }
            }
        }
        Slider {
            id: transitionDuration
            Layout.columnSpan: 2
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            objectName: "shortModeTransitionDurationSlider"
            from: 0; to: 2.0; stepSize: 0.1
            enabled: settingsRoot.editingEnabled
            function commitDuration() {
                if (settingsRoot.appBackend && !settingsRoot.appBackend.running && !settingsRoot.refreshingSettings) {
                    settingsRoot.appBackend.shortVideo.setShortVideoTransition(transitionCombo.currentValue, value)
                }
            }
            onMoved: if (!pressed) commitDuration()
            onPressedChanged: if (!pressed) commitDuration()
        }
    }

    RowLayout {
        Layout.fillWidth: true
        Text { text: "字幕スケール"; color: "#F0F6FC"; Layout.fillWidth: true }
        SpinBox {
            id: scaleSpin
            Layout.preferredWidth: 130
            objectName: "shortModeSubtitleScaleSpin"
            from: 50; to: 300
            textFromValue: function(value) { return value + "%" }
            valueFromText: function(text) { return parseInt(text) || 150 }
            enabled: settingsRoot.editingEnabled
            onValueModified: {
                if (settingsRoot.appBackend) {
                    settingsRoot.appBackend.shortVideo.setShortVideoSubtitleScale(value)
                }
            }
        }
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 1
        color: "#30363D"
    }

    Text {
        text: "BGM"
        color: "#F0F6FC"
        font.family: "Yu Gothic UI"
        font.pixelSize: 14
        font.weight: Font.Bold
    }

    Button {
        id: bgmBrowseButton
        objectName: "shortModeBgmBrowseButton"
        Layout.fillWidth: true
        enabled: settingsRoot.editingEnabled
        contentItem: Text {
            id: bgmFileLabel
            objectName: "shortModeBgmFileLabel"
            text: "BGMを選択"
            color: "#F0F6FC"
            elide: Text.ElideMiddle
            verticalAlignment: Text.AlignVCenter
            horizontalAlignment: Text.AlignLeft
        }
        onClicked: {
            if (settingsRoot.appBackend) {
                settingsRoot.appBackend.shortVideo.browseShortModeBgm()
            }
        }
    }

    GridLayout {
        columns: 2
        Layout.fillWidth: true
        columnSpacing: 10
        rowSpacing: 6

        Text { Layout.row: 0; Layout.column: 0; text: "開始位置"; color: "#F0F6FC" }
        Text { Layout.row: 0; Layout.column: 1; text: "終了位置"; color: "#F0F6FC" }
        Text { Layout.row: 2; Layout.column: 0; text: "動画内の開始"; color: "#F0F6FC" }
        Text { Layout.row: 2; Layout.column: 1; text: "音量"; color: "#F0F6FC" }

        TimeField {
            id: bgmIn
            Layout.row: 1
            Layout.column: 0
            Layout.fillWidth: true
            objectName: "shortModeBgmInField"
            Layout.preferredWidth: 70
            enabled: settingsRoot.editingEnabled
            text: "0"
            property bool draftEdited: false
            onTextEdited: draftEdited = true
            onEditingFinished: {
                draftEdited = true
                settingsRoot.commitPendingEdits()
            }
        }
        TimeField {
            id: bgmOut
            Layout.row: 1
            Layout.column: 1
            Layout.fillWidth: true
            objectName: "shortModeBgmOutField"
            Layout.preferredWidth: 70
            enabled: settingsRoot.editingEnabled
            text: "0"
            property bool draftEdited: false
            onTextEdited: draftEdited = true
            onEditingFinished: {
                draftEdited = true
                settingsRoot.commitPendingEdits()
            }
        }
        TimeField {
            id: bgmStart
            Layout.row: 3
            Layout.column: 0
            Layout.fillWidth: true
            objectName: "shortModeBgmStartField"
            Layout.preferredWidth: 70
            enabled: settingsRoot.editingEnabled
            text: "0"
            property bool draftEdited: false
            onTextEdited: draftEdited = true
            onEditingFinished: {
                draftEdited = true
                settingsRoot.commitPendingEdits()
            }
        }
        ColumnLayout {
            Layout.row: 3
            Layout.column: 1
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            spacing: 2
            Slider {
                id: bgmVolumeSlider
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                objectName: "shortModeBgmVolumeSlider"
                from: 0.0; to: 1.0; stepSize: 0.05
                enabled: settingsRoot.editingEnabled
                onMoved: if (!pressed) settingsRoot._sendBgmUpdate({"volume": value})
                onPressedChanged: if (!pressed) settingsRoot._sendBgmUpdate({"volume": value})
            }
        }
    }

    Component.onCompleted: refresh()
}
