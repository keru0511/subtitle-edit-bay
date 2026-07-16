pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtMultimedia

ApplicationWindow {
    id: root
    // Injected by gui.py before the QML document is loaded.
    // qmllint disable unqualified
    property var appBackend: backend
    // qmllint enable unqualified
    width: 1480
    height: 920
    minimumWidth: 1180
    minimumHeight: 720
    visible: true
    title: "Subtitle Edit Bay"
    onClosing: {
        mediaPlayer.stop()
        mediaPlayer.source = ""
    }
    color: "#0B0E0D"
    palette.window: "#121715"
    palette.windowText: "#F4F1E8"
    palette.base: "#171D1A"
    palette.alternateBase: "#202823"
    palette.text: "#F4F1E8"
    palette.button: "#202823"
    palette.buttonText: "#F4F1E8"
    palette.highlight: "#C8FF3D"
    palette.highlightedText: "#10140F"
    palette.toolTipBase: "#26312B"
    palette.toolTipText: "#F4F1E8"

    readonly property color canvas: "#0B0E0D"
    readonly property color panel: "#121715"
    readonly property color panelRaised: "#19201D"
    readonly property color border: "#29332E"
    readonly property color textPrimary: "#F4F1E8"
    readonly property color textMuted: "#8E9B94"
    readonly property color acid: "#C8FF3D"
    readonly property color amber: "#FFB547"
    readonly property color danger: "#FF6B5F"

    function currentSettings() {
        return {
            "model": modelCombo.currentText,
            "device": deviceCombo.currentText,
            "compute_type": deviceCombo.currentText === "cuda" ? "float16" : "int8",
            "language": "ja",
            "nvenc_cq": Math.round(qualitySlider.value),
            "x264_crf": Math.round(qualitySlider.value),
            "subtitle_font_size": fontSizeSpin.value,
            "subtitle_volume_scale_percent": volumeScaleSpin.value,
            "subtitle_max_gap_seconds": Number(gapField.text),
            "subtitle_end_padding_seconds": Number(paddingField.text),
            "subtitle_min_duration_seconds": Number(minDurationField.text),
            "video_codec": codecCombo.currentText,
            "audio_normalize": normalizeSwitch.checked,
            "audio_target_lufs": Number(lufsField.text),
            "cut_no_speech": silenceSwitch.checked,
            "no_speech_min_seconds": Number(silenceField.text),
            "speech_padding_seconds": Number(speechPaddingField.text),
            "postprocess_workers": workersSpin.value,
            "reference_audio": root.appBackend.speakers.length > 0 ? referenceCombo.currentValue : "",
            "reference_track": root.appBackend.audioTracks.length > 0 ? trackCombo.currentValue : "",
            "alignment_offset_adjustment": Number(manualOffsetField.text || 0)
        }
    }

    function syncSettings() {
        var value = root.appBackend.settings
        qualitySlider.value = Number(value.nvenc_cq || 18)
        fontSizeSpin.value = Number(value.subtitle_font_size || 50)
        volumeScaleSpin.value = Number(value.subtitle_volume_scale_percent === undefined ? 20 : value.subtitle_volume_scale_percent)
        gapField.text = Number(value.subtitle_max_gap_seconds || 0.1).toFixed(2)
        paddingField.text = Number(value.subtitle_end_padding_seconds || 0.08).toFixed(2)
        minDurationField.text = Number(value.subtitle_min_duration_seconds || 0.35).toFixed(2)
        silenceField.text = Number(value.no_speech_min_seconds || 1.2).toFixed(1)
        speechPaddingField.text = Number(value.speech_padding_seconds || 0.25).toFixed(2)
        lufsField.text = Number(value.audio_target_lufs || -16).toFixed(0)
        normalizeSwitch.checked = value.audio_normalize === undefined ? true : value.audio_normalize
        silenceSwitch.checked = Boolean(value.cut_no_speech)
        workersSpin.value = Number(value.postprocess_workers || 4)
        modelCombo.currentIndex = Math.max(0, modelCombo.find(value.model || "large-v3"))
        deviceCombo.currentIndex = Math.max(0, deviceCombo.find(value.device || "cuda"))
        codecCombo.currentIndex = Math.max(0, codecCombo.find(value.video_codec || "h264_nvenc"))
        manualOffsetField.text = Number(value.alignment_offset_adjustment || 0).toFixed(3)
    }

    component SectionLabel: Text {
        color: root.textMuted
        font.family: "Bahnschrift"
        font.pixelSize: 11
        font.weight: Font.DemiBold
        font.letterSpacing: 1.6
    }

    component BodyText: Text {
        color: root.textPrimary
        font.family: "Yu Gothic UI"
        font.pixelSize: 13
    }

    component HelpToolTip: ToolTip {
        id: helpTip
        property Item anchorTarget
        property bool active: false
        parent: anchorTarget
        visible: active
        delay: 450
        timeout: 12000
        width: 320
        padding: 12
        contentItem: Text {
            text: helpTip.text
            color: root.textPrimary
            font.family: "Yu Gothic UI"
            font.pixelSize: 12
            lineHeight: 1.35
            wrapMode: Text.Wrap
        }
        background: Rectangle {
            radius: 9
            color: "#26312B"
            border.color: "#526259"
        }
    }

    component EditField: TextField {
        id: fieldControl
        property string helpText: ""
        hoverEnabled: true
        HelpToolTip { anchorTarget: fieldControl; active: fieldControl.hovered; text: fieldControl.helpText }
        color: root.textPrimary
        selectionColor: root.acid
        selectedTextColor: "#111410"
        font.family: "Bahnschrift"
        font.pixelSize: 13
        horizontalAlignment: TextInput.AlignRight
        leftPadding: 12
        rightPadding: 12
        background: Rectangle {
            implicitHeight: 36
            radius: 8
            color: parent.activeFocus ? "#202A25" : "#171D1A"
            border.color: parent.activeFocus ? root.acid : root.border
        }
    }

    component SettingSwitch: Switch {
        id: control
        property string helpText: ""
        hoverEnabled: true
        HelpToolTip { anchorTarget: control; active: control.hovered; text: control.helpText }
        indicator: Rectangle {
            implicitWidth: 42
            implicitHeight: 23
            x: control.width - width
            y: (control.height - height) / 2
            radius: height / 2
            color: control.checked ? root.acid : "#2B3530"
            Behavior on color { ColorAnimation { duration: 140 } }
            Rectangle {
                width: 17
                height: 17
                radius: 9
                y: 3
                x: control.checked ? parent.width - width - 3 : 3
                color: control.checked ? "#10150F" : "#98A39D"
                Behavior on x { NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
            }
        }
        contentItem: Text {
            text: control.text
            color: root.textPrimary
            font.family: "Yu Gothic UI"
            font.pixelSize: 13
            verticalAlignment: Text.AlignVCenter
            rightPadding: 50
        }
    }

    background: Rectangle {
        color: root.canvas
        Rectangle {
            width: 620
            height: 620
            radius: 310
            x: parent.width - width * 0.62
            y: -height * 0.62
            color: "#16251B"
            opacity: 0.72
        }
        Rectangle {
            anchors.fill: parent
            color: "transparent"
            border.color: "#111613"
        }
    }

    header: Rectangle {
        height: 70
        color: "#0E1210"
        border.color: root.border

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 22
            anchors.rightMargin: 22
            spacing: 18

            Rectangle {
                Layout.preferredWidth: 36
                Layout.preferredHeight: 36
                radius: 10
                color: root.acid
                Text {
                    anchors.centerIn: parent
                    text: "S"
                    color: "#10140F"
                    font.family: "Bahnschrift"
                    font.pixelSize: 20
                    font.weight: Font.Black
                }
            }

            Column {
                Layout.preferredWidth: 210
                spacing: -1
                Text {
                    text: "SUBTITLE EDIT BAY"
                    color: root.textPrimary
                    font.family: "Bahnschrift"
                    font.pixelSize: 15
                    font.weight: Font.Bold
                    font.letterSpacing: 1.1
                }
                Text {
                    text: "CRAIG PRODUCTION DESK"
                    color: root.textMuted
                    font.family: "Bahnschrift"
                    font.pixelSize: 9
                    font.letterSpacing: 1.5
                }
            }

            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: "transparent" }

            Rectangle {
                Layout.preferredWidth: statusRow.width + 24
                Layout.preferredHeight: 34
                radius: 17
                color: root.appBackend.running ? "#24301A" : "#171D1A"
                border.color: root.appBackend.running ? "#789B2E" : root.border
                Row {
                    id: statusRow
                    anchors.centerIn: parent
                    spacing: 9
                    Rectangle {
                        width: 8
                        height: 8
                        radius: 4
                        color: root.appBackend.stage === "ERROR" ? root.danger : (root.appBackend.running ? root.acid : "#6C7871")
                        SequentialAnimation on opacity {
                            running: root.appBackend.running
                            loops: Animation.Infinite
                            NumberAnimation { to: 0.35; duration: 600 }
                            NumberAnimation { to: 1.0; duration: 600 }
                        }
                    }
                    Text {
                        text: root.appBackend.stage
                        color: root.textPrimary
                        font.family: "Bahnschrift"
                        font.pixelSize: 11
                        font.weight: Font.DemiBold
                        font.letterSpacing: 1
                    }
                }
            }

            Text {
                text: root.appBackend.elapsed
                color: root.textPrimary
                font.family: "Bahnschrift"
                font.pixelSize: 15
                font.weight: Font.Medium
            }
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 12

        Rectangle {
            Layout.preferredWidth: 242
            Layout.fillHeight: true
            radius: 14
            color: root.panel
            border.color: root.border

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 12

                RowLayout {
                    Layout.fillWidth: true
                    SectionLabel { text: "INPUT SOURCES" }
                    Item { Layout.fillWidth: true }
                    ToolButton {
                        id: resetSourcesButton
                        text: "RESET"
                        enabled: !root.appBackend.running
                        onClicked: {
                            root.appBackend.resetSources()
                            sourceSetup.open()
                        }
                        contentItem: Text {
                            text: resetSourcesButton.text
                            color: resetSourcesButton.hovered ? root.danger : root.textMuted
                            font.family: "Bahnschrift"
                            font.pixelSize: 9
                            font.weight: Font.Bold
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle {
                            radius: 7
                            color: resetSourcesButton.hovered ? "#34201E" : "transparent"
                        }
                    }
                }

                Rectangle {
                    id: sourceSummary
                    Layout.fillWidth: true
                    Layout.preferredHeight: 142
                    radius: 11
                    color: "#171D1A"
                    border.color: root.appBackend.sourceSelection.video
                        && root.appBackend.sourceSelection.audio_files.length > 0
                        && root.appBackend.sourceSelection.output_dir ? "#4B6030" : root.border

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 11
                        spacing: 7

                        RowLayout {
                            Layout.fillWidth: true
                            Text {
                                text: root.appBackend.sourceSelection.video ? "✓" : "1"
                                color: root.appBackend.sourceSelection.video ? root.acid : root.amber
                                font.family: "Bahnschrift"
                                font.weight: Font.Bold
                                Layout.preferredWidth: 16
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0
                                Text { text: "VIDEO"; color: root.textMuted; font.family: "Bahnschrift"; font.pixelSize: 9 }
                                Text {
                                    Layout.fillWidth: true
                                    text: root.appBackend.sourceSelection.video
                                        ? root.appBackend.sourceSelection.video.split(/[\\/]/).pop()
                                        : "動画を指定"
                                    color: root.textPrimary
                                    font.family: "Yu Gothic UI"
                                    font.pixelSize: 10
                                    elide: Text.ElideMiddle
                                }
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Text {
                                text: root.appBackend.sourceSelection.audio_files.length > 0 ? "✓" : "2"
                                color: root.appBackend.sourceSelection.audio_files.length > 0 ? root.acid : root.amber
                                font.family: "Bahnschrift"
                                font.weight: Font.Bold
                                Layout.preferredWidth: 16
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0
                                Text { text: "SPEAKER AUDIO"; color: root.textMuted; font.family: "Bahnschrift"; font.pixelSize: 9 }
                                Text {
                                    text: root.appBackend.sourceSelection.audio_files.length > 0
                                        ? root.appBackend.sourceSelection.audio_files.length + " files"
                                        : "話者音声を指定"
                                    color: root.textPrimary
                                    font.family: "Yu Gothic UI"
                                    font.pixelSize: 10
                                }
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Text {
                                text: root.appBackend.sourceSelection.output_dir ? "✓" : "3"
                                color: root.appBackend.sourceSelection.output_dir ? root.acid : root.amber
                                font.family: "Bahnschrift"
                                font.weight: Font.Bold
                                Layout.preferredWidth: 16
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0
                                Text { text: "OUTPUT"; color: root.textMuted; font.family: "Bahnschrift"; font.pixelSize: 9 }
                                Text {
                                    Layout.fillWidth: true
                                    text: root.appBackend.sourceSelection.output_dir
                                        ? root.appBackend.sourceSelection.output_dir
                                        : "出力先を指定"
                                    color: root.textPrimary
                                    font.family: "Yu Gothic UI"
                                    font.pixelSize: 10
                                    elide: Text.ElideMiddle
                                }
                            }
                        }
                    }

                    MouseArea {
                        anchors.fill: parent
                        enabled: !root.appBackend.running
                        cursorShape: Qt.PointingHandCursor
                        onClicked: sourceSetup.open()
                    }
                }

                Button {
                    id: sourceSetupButton
                    Layout.fillWidth: true
                    Layout.preferredHeight: 40
                    enabled: !root.appBackend.running
                    text: "DROP / SELECT SOURCES"
                    onClicked: sourceSetup.open()
                    contentItem: Text {
                        text: sourceSetupButton.text
                        color: root.acid
                        font.family: "Bahnschrift"
                        font.pixelSize: 10
                        font.weight: Font.Bold
                        font.letterSpacing: 1.0
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        radius: 9
                        color: sourceSetupButton.hovered ? "#263126" : "#18201B"
                        border.color: "#4B6030"
                    }
                }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }

                RowLayout {
                    Layout.fillWidth: true
                    SectionLabel { text: "SPEAKER TRACKS" }
                    Item { Layout.fillWidth: true }
                    Text {
                        text: root.appBackend.speakers.length
                        color: root.textMuted
                        font.family: "Bahnschrift"
                        font.pixelSize: 11
                    }
                }

                ListView {
                    id: speakerList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 8
                    model: root.appBackend.speakers
                    delegate: Rectangle {
                        id: speakerDelegate
                        required property int index
                        required property var modelData
                        width: speakerList.width
                        height: 62
                        radius: 10
                        color: "#171D1A"
                        border.color: "#242E29"
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 10
                            spacing: 10
                            Rectangle {
                                Layout.preferredWidth: 30
                                Layout.preferredHeight: 30
                                radius: 9
                                color: speakerDelegate.modelData.color
                                Text {
                                    anchors.centerIn: parent
                                    text: speakerDelegate.modelData.name.charAt(0).toUpperCase()
                                    color: "#121512"
                                    font.family: "Bahnschrift"
                                    font.weight: Font.Bold
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 1
                                Text {
                                    Layout.fillWidth: true
                                    text: speakerDelegate.modelData.name
                                    color: root.textPrimary
                                    font.family: "Yu Gothic UI"
                                    font.pixelSize: 12
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideRight
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: speakerDelegate.modelData.file_name
                                    color: root.textMuted
                                    font.family: "Bahnschrift"
                                    font.pixelSize: 9
                                    elide: Text.ElideMiddle
                                }
                            }
                            Rectangle {
                                Layout.preferredWidth: 22
                                Layout.preferredHeight: 22
                                radius: 6
                                color: speakerDelegate.modelData.color
                                border.color: "#FFFFFF"
                                border.width: 1
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        colorPopup.speakerIndex = speakerDelegate.index
                                        colorPopup.open()
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 10

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 280
                radius: 14
                color: "#080A09"
                border.color: root.border
                clip: true

                MediaPlayer {
                    id: mediaPlayer
                    objectName: "mediaPlayer"
                    property bool priming: false
                    source: root.appBackend.previewUrl
                    videoOutput: videoOutput
                    audioOutput: AudioOutput {
                        volume: 0.7
                        muted: mediaPlayer.priming
                    }
                    onPositionChanged: {
                        if (!sequenceSlider.pressed)
                            sequenceSlider.value = position
                    }
                    onDurationChanged: sequenceSlider.to = Math.max(1, duration)
                    onSourceChanged: {
                        sequenceSlider.value = 0
                        if (source.toString() !== "") {
                            priming = true
                            play()
                        }
                    }
                }
                VideoOutput {
                    id: videoOutput
                    anchors.fill: parent
                    fillMode: VideoOutput.PreserveAspectFit
                }
                Connections {
                    target: videoOutput.videoSink
                    function onVideoFrameChanged() {
                        if (mediaPlayer.priming) {
                            mediaPlayer.pause()
                            mediaPlayer.priming = false
                        }
                    }
                }

                Rectangle {
                    anchors.fill: parent
                    visible: root.appBackend.previewUrl === ""
                    color: "#0C100E"
                    Column {
                        anchors.centerIn: parent
                        spacing: 10
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: "DROP A CRAIG PROJECT"
                            color: root.textPrimary
                            font.family: "Bahnschrift"
                            font.pixelSize: 20
                            font.weight: Font.Bold
                            font.letterSpacing: 1.5
                        }
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: "動画と話者別音声を自動検出します"
                            color: root.textMuted
                            font.family: "Yu Gothic UI"
                            font.pixelSize: 12
                        }
                    }
                }

                Rectangle {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    height: 88
                    gradient: Gradient {
                        GradientStop { position: 0; color: "#0010150F" }
                        GradientStop { position: 1; color: "#F0101512" }
                    }
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 18
                        anchors.rightMargin: 18
                        anchors.topMargin: 8
                        anchors.bottomMargin: 6
                        spacing: 3
                        Slider {
                            id: sequenceSlider
                            objectName: "sequenceSlider"
                            Layout.fillWidth: true
                            Layout.preferredHeight: 18
                            from: 0
                            to: 1
                            value: 0
                            onMoved: mediaPlayer.position = value
                            HelpToolTip { anchorTarget: sequenceSlider; active: sequenceSlider.hovered || sequenceSlider.pressed; text: "ドラッグまたはクリックして再生位置を移動します" }

                            background: Rectangle {
                                x: sequenceSlider.leftPadding
                                y: sequenceSlider.topPadding + sequenceSlider.availableHeight / 2 - height / 2
                                width: sequenceSlider.availableWidth
                                height: 4
                                radius: 2
                                color: "#465149"
                                Rectangle {
                                    width: sequenceSlider.visualPosition * parent.width
                                    height: parent.height
                                    radius: parent.radius
                                    color: root.acid
                                }
                            }
                            handle: Rectangle {
                                x: sequenceSlider.leftPadding + sequenceSlider.visualPosition * (sequenceSlider.availableWidth - width)
                                y: sequenceSlider.topPadding + sequenceSlider.availableHeight / 2 - height / 2
                                width: sequenceSlider.pressed ? 14 : 11
                                height: width
                                radius: width / 2
                                color: root.textPrimary
                                border.color: root.acid
                                border.width: 2
                                Behavior on width { NumberAnimation { duration: 100 } }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            ToolButton {
                                id: playButton
                                text: mediaPlayer.playbackState === MediaPlayer.PlayingState ? "Ⅱ" : "▶"
                                onClicked: mediaPlayer.playbackState === MediaPlayer.PlayingState ? mediaPlayer.pause() : mediaPlayer.play()
                                contentItem: Text {
                                    text: playButton.text
                                    color: root.textPrimary
                                    font.pixelSize: 18
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }
                            }
                            Text {
                                text: root.appBackend.sourceSelection.video ? root.appBackend.sourceSelection.video.split(/[\\/]/).pop() : "NO MEDIA"
                                color: root.textPrimary
                                font.family: "Bahnschrift"
                                font.pixelSize: 12
                                elide: Text.ElideMiddle
                                Layout.fillWidth: true
                            }
                            Text {
                                text: {
                                    var total = Math.max(0, mediaPlayer.duration)
                                    var current = Math.max(0, mediaPlayer.position)
                                    function stamp(ms) {
                                        var seconds = Math.floor(ms / 1000)
                                        var hours = Math.floor(seconds / 3600)
                                        var minutes = Math.floor((seconds % 3600) / 60)
                                        var remaining = seconds % 60
                                        return (hours > 0 ? (hours < 10 ? "0" : "") + hours + ":" : "")
                                            + (minutes < 10 ? "0" : "") + minutes + ":"
                                            + (remaining < 10 ? "0" : "") + remaining
                                    }
                                    return stamp(current) + " / " + stamp(total)
                                }
                                color: root.textMuted
                                font.family: "Bahnschrift"
                                font.pixelSize: 11
                            }
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.max(150, 72 + root.appBackend.speakers.length * 22)
                radius: 14
                color: root.panel
                border.color: root.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 8
                    RowLayout {
                        Layout.fillWidth: true
                        SectionLabel { text: "SPEECH TIMELINE" }
                        Item { Layout.fillWidth: true }
                        Text {
                            text: "PREVIEW MAP"
                            color: root.amber
                            font.family: "Bahnschrift"
                            font.pixelSize: 9
                            font.letterSpacing: 1.3
                        }
                    }
                    Repeater {
                        model: root.appBackend.speakers
                        RowLayout {
                            id: waveformRow
                            required property int index
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.preferredHeight: 18
                            spacing: 7
                            Text {
                                Layout.preferredWidth: 72
                                text: waveformRow.modelData.name
                                color: root.textMuted
                                font.family: "Bahnschrift"
                                font.pixelSize: 9
                                elide: Text.ElideRight
                            }
                            Row {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 16
                                spacing: 2
                                Repeater {
                                    model: 42
                                    Rectangle {
                                        required property int index
                                        width: Math.max(3, (parent.width - 82) / 42)
                                        height: 3 + ((index * 13 + waveformRow.index * 17) % 12)
                                        anchors.verticalCenter: parent.verticalCenter
                                        radius: 2
                                        color: waveformRow.modelData.color
                                        opacity: ((index * 7 + waveformRow.index) % 5) === 0 ? 0.18 : 0.72
                                    }
                                }
                            }
                        }
                    }
                    Item { Layout.fillHeight: true }
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 2
                        color: "#2A342F"
                        Rectangle {
                            width: parent.width * (mediaPlayer.duration > 0 ? mediaPlayer.position / mediaPlayer.duration : 0.03)
                            height: parent.height
                            color: root.acid
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 148
                radius: 14
                color: root.panel
                border.color: root.border
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 14
                    ColumnLayout {
                        Layout.preferredWidth: 145
                        Layout.fillHeight: true
                        SectionLabel { text: "JOB MONITOR" }
                        Text {
                            text: Math.round(root.appBackend.progress * 100) + "%"
                            color: root.acid
                            font.family: "Bahnschrift"
                            font.pixelSize: 30
                            font.weight: Font.Bold
                        }
                        Text {
                            Layout.fillWidth: true
                            text: root.appBackend.status
                            color: root.textMuted
                            font.family: "Yu Gothic UI"
                            font.pixelSize: 11
                            wrapMode: Text.Wrap
                        }
                        Item { Layout.fillHeight: true }
                    }
                    Rectangle { Layout.preferredWidth: 1; Layout.fillHeight: true; color: root.border }
                    ScrollView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        TextArea {
                            id: logArea
                            readOnly: true
                            text: root.appBackend.logText || "実行ログはここに表示されます。"
                            color: root.appBackend.logText ? "#B9C6BF" : "#66736C"
                            font.family: "Cascadia Mono"
                            font.pixelSize: 10
                            wrapMode: TextEdit.WrapAnywhere
                            background: Rectangle { color: "transparent" }
                            onTextChanged: cursorPosition = length
                        }
                    }
                }
            }
        }

        Rectangle {
            Layout.preferredWidth: 320
            Layout.fillHeight: true
            radius: 14
            color: root.panel
            border.color: root.border

            ColumnLayout {
                anchors.fill: parent
                spacing: 0

                RowLayout {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 58
                    Layout.leftMargin: 16
                    Layout.rightMargin: 16
                    Text {
                        text: "OUTPUT INSPECTOR"
                        color: root.textPrimary
                        font.family: "Bahnschrift"
                        font.pixelSize: 13
                        font.weight: Font.Bold
                        font.letterSpacing: 1.2
                    }
                    Item { Layout.fillWidth: true }
                    Rectangle { Layout.preferredWidth: 8; Layout.preferredHeight: 8; radius: 4; color: root.amber }
                }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }

                ScrollView {
                    id: inspectorScroll
                    objectName: "inspectorScroll"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ColumnLayout {
                        objectName: "inspectorContent"
                        x: 16
                        width: Math.max(0, inspectorScroll.availableWidth - 32)
                        spacing: 12

                        Item { Layout.preferredHeight: 4 }
                        SectionLabel { text: "ENGINE" }
                        RowLayout {
                            Layout.fillWidth: true
                            BodyText { text: "Device"; Layout.fillWidth: true }
                            ComboBox {
                                id: deviceCombo
                                model: ["cuda", "cpu"]
                                Layout.preferredWidth: 112
                                hoverEnabled: true
                                HelpToolTip { anchorTarget: deviceCombo; active: deviceCombo.hovered; text: "cudaはNVIDIA GPUを使う高速設定です。cpuはGPUなしで動きますが、文字起こしに時間がかかります。" }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            BodyText { text: "Whisper model"; Layout.fillWidth: true }
                            ComboBox {
                                id: modelCombo
                                model: ["large-v3", "medium", "small"]
                                Layout.preferredWidth: 132
                                hoverEnabled: true
                                HelpToolTip { anchorTarget: modelCombo; active: modelCombo.hovered; text: "large-v3は最も高精度ですが重く、medium・smallの順に高速・軽量になります。ゲーム固有名詞の精度を優先するならlarge-v3を推奨します。" }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            BodyText { text: "CPU workers"; Layout.fillWidth: true }
                            SpinBox {
                                id: workersSpin
                                from: 1
                                to: 16
                                value: 4
                                editable: true
                                hoverEnabled: true
                                HelpToolTip { anchorTarget: workersSpin; active: workersSpin.hovered; text: "WhisperX後の字幕整形を並列処理するCPUスレッド数です。大きくすると速くなる場合がありますが、CPUとメモリ使用量が増えます。通常は4で十分です。" }
                            }
                        }

                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }
                        SectionLabel { text: "VIDEO QUALITY" }
                        RowLayout {
                            Layout.fillWidth: true
                            BodyText { text: "Codec"; Layout.fillWidth: true }
                            ComboBox {
                                id: codecCombo
                                model: ["h264_nvenc", "libx264"]
                                Layout.preferredWidth: 132
                                hoverEnabled: true
                                HelpToolTip { anchorTarget: codecCombo; active: codecCombo.hovered; text: "h264_nvencはNVIDIA GPUで高速に出力します。libx264はCPUで遅めですが、GPUがない環境でも利用できます。" }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2
                                BodyText { text: codecCombo.currentText === "h264_nvenc" ? "NVENC quality" : "x264 quality" }
                                Text {
                                    text: "LOWER IS CLEANER"
                                    color: root.textMuted
                                    font.family: "Bahnschrift"
                                    font.pixelSize: 8
                                    font.letterSpacing: 1
                                }
                            }
                            Text {
                                text: Math.round(qualitySlider.value)
                                color: root.acid
                                font.family: "Bahnschrift"
                                font.pixelSize: 20
                                font.weight: Font.Bold
                            }
                        }
                        Slider {
                            id: qualitySlider
                            Layout.fillWidth: true
                            from: 14
                            to: 28
                            stepSize: 1
                            value: 18
                            hoverEnabled: true
                            HelpToolTip {
                                anchorTarget: qualitySlider
                                active: qualitySlider.hovered || qualitySlider.pressed
                                text: "画質を決めるCQ/CRF値です。小さいほど高画質・大容量、大きいほど低画質・小容量になります。標準は18、より高画質なら16前後です。"
                            }
                        }

                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }
                        SectionLabel { text: "AUDIO & CUT" }
                        SettingSwitch { id: normalizeSwitch; Layout.fillWidth: true; text: "音量を正規化"; checked: true; helpText: "動画全体の聞こえる音量を指定したLUFSへ揃えます。参加者ごとの音量差を完全に揃える機能ではありません。" }
                        RowLayout {
                            Layout.fillWidth: true
                            BodyText { text: "Target loudness"; Layout.fillWidth: true }
                            EditField { id: lufsField; Layout.preferredWidth: 74; text: "-16"; helpText: "出力全体の目標音量です。-14は大きめ、-16は標準、-18は控えめです。0に近いほど音が大きくなります。" }
                            Text { text: "LUFS"; color: root.textMuted; font.pixelSize: 10 }
                        }
                        SettingSwitch { id: silenceSwitch; Layout.fillWidth: true; text: "誰も話していない部分をカット"; helpText: "全話者トラックで発話がない区間を映像ごと削除します。ゲーム音だけの場面も短くなるため、必要な動画だけ有効にしてください。" }
                        RowLayout {
                            Layout.fillWidth: true
                            enabled: silenceSwitch.checked
                            opacity: enabled ? 1 : 0.4
                            BodyText { text: "Minimum silence"; Layout.fillWidth: true }
                            EditField { id: silenceField; Layout.preferredWidth: 74; text: "1.2"; helpText: "この秒数以上、全員が話していない区間だけをカットします。小さくすると細かく詰まり、大きくすると長い沈黙だけが消えます。" }
                            Text { text: "SEC"; color: root.textMuted; font.pixelSize: 10 }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            enabled: silenceSwitch.checked
                            opacity: enabled ? 1 : 0.4
                            BodyText { text: "Speech padding"; Layout.fillWidth: true }
                            EditField { id: speechPaddingField; Layout.preferredWidth: 74; text: "0.25"; helpText: "発話の前後に残す余白です。短すぎると声の頭や語尾を切りやすく、長すぎると無音が多く残ります。" }
                            Text { text: "SEC"; color: root.textMuted; font.pixelSize: 10 }
                        }

                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }
                        SectionLabel { text: "SUBTITLE SIZE" }
                        RowLayout {
                            Layout.fillWidth: true
                            BodyText { text: "Base font size"; Layout.fillWidth: true }
                            SpinBox {
                                id: fontSizeSpin
                                from: 32
                                to: 96
                                value: 50
                                editable: true
                                Layout.preferredWidth: 100
                                hoverEnabled: true
                                HelpToolTip { anchorTarget: fontSizeSpin; active: fontSizeSpin.hovered; text: "字幕の基準文字サイズです。全話者に同じ値を使い、発話音量による倍率をこのサイズへ適用します。標準は50です。" }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            BodyText { text: "Volume scaling"; Layout.fillWidth: true }
                            SpinBox {
                                id: volumeScaleSpin
                                from: 0
                                to: 50
                                value: 20
                                editable: true
                                Layout.preferredWidth: 100
                                hoverEnabled: true
                                HelpToolTip { anchorTarget: volumeScaleSpin; active: volumeScaleSpin.hovered; text: "話者ごとの普段の音量を基準に、静かな発話を小さく、大きな発話を大きくする幅です。20ならおよそ80〜120%で変化し、0なら音量連動を無効にします。" }
                            }
                            Text { text: "%"; color: root.textMuted; font.pixelSize: 10 }
                        }

                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }
                        SectionLabel { text: "SUBTITLE TIMING" }
                        RowLayout {
                            Layout.fillWidth: true
                            BodyText { text: "Word gap"; Layout.fillWidth: true }
                            EditField { id: gapField; Layout.preferredWidth: 74; text: "0.10"; helpText: "単語間の無音がこの秒数以上なら字幕の分割候補にします。小さくすると字幕が細かく分かれ、大きくすると一つにつながりやすくなります。" }
                            Text { text: "SEC"; color: root.textMuted; font.pixelSize: 10 }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            BodyText { text: "End padding"; Layout.fillWidth: true }
                            EditField { id: paddingField; Layout.preferredWidth: 74; text: "0.08"; helpText: "最後の単語を話し終えてから字幕を残す時間です。大きくすると読みやすくなりますが、発話後も字幕が残りやすくなります。" }
                            Text { text: "SEC"; color: root.textMuted; font.pixelSize: 10 }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            BodyText { text: "Minimum duration"; Layout.fillWidth: true }
                            EditField { id: minDurationField; Layout.preferredWidth: 74; text: "0.35"; helpText: "字幕1ページを最低限表示する時間です。小さすぎると一瞬で消え、大きすぎると次の発話と重なりやすくなります。" }
                            Text { text: "SEC"; color: root.textMuted; font.pixelSize: 10 }
                        }
                        Item { Layout.preferredHeight: 8 }
                    }
                }

                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.margins: 14
                    spacing: 10
                    Button {
                        id: startRenderButton
                        objectName: "startRenderButton"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 48
                        enabled: root.appBackend.sourceSelection.video && root.appBackend.sourceSelection.output_dir && root.appBackend.speakers.length > 0 && !root.appBackend.running
                            && root.appBackend.dependencyStatus.ready
                        text: "START RENDER"
                        onClicked: root.appBackend.startProcessing(root.currentSettings())
                        contentItem: Text {
                            text: startRenderButton.text
                            color: startRenderButton.enabled ? "#10140F" : "#657067"
                            font.family: "Bahnschrift"
                            font.pixelSize: 13
                            font.weight: Font.Bold
                            font.letterSpacing: 1.4
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle {
                            radius: 10
                            color: startRenderButton.enabled ? (startRenderButton.hovered ? "#D6FF71" : root.acid) : "#232A26"
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Button {
                            Layout.fillWidth: true
                            text: root.appBackend.running ? "STOP" : "SAVE PRESET"
                            onClicked: root.appBackend.running ? root.appBackend.cancelProcessing() : root.appBackend.saveSettings(root.currentSettings())
                        }
                        Button {
                            Layout.fillWidth: true
                            text: "OPEN OUTPUT"
                            enabled: root.appBackend.sourceSelection.output_dir
                            onClicked: root.appBackend.openOutputFolder()
                        }
                    }
                }
            }
        }
    }

    Popup {
        id: sourceSetup
        objectName: "sourceSetup"
        anchors.centerIn: Overlay.overlay
        width: Math.min(root.width - 64, 900)
        height: Math.min(root.height - 64, 760)
        modal: true
        focus: true
        padding: 0
        closePolicy: Popup.CloseOnEscape
        background: Rectangle {
            radius: 18
            color: "#111714"
            border.color: "#3A4841"
            border.width: 1
        }

        ColumnLayout {
            anchors.fill: parent
            spacing: 0

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 68
                Layout.leftMargin: 22
                Layout.rightMargin: 14
                spacing: 12
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 1
                    Text {
                        text: "SOURCE SETUP"
                        color: root.textPrimary
                        font.family: "Bahnschrift"
                        font.pixelSize: 18
                        font.weight: Font.Bold
                        font.letterSpacing: 1.5
                    }
                    Text {
                        text: "このレンダーで使う素材をすべて指定"
                        color: root.textMuted
                        font.family: "Yu Gothic UI"
                        font.pixelSize: 11
                    }
                }
                ToolButton {
                    id: closeSourceButton
                    text: "×"
                    onClicked: sourceSetup.close()
                    contentItem: Text {
                        text: closeSourceButton.text
                        color: root.textMuted
                        font.pixelSize: 24
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        radius: 9
                        color: closeSourceButton.hovered ? "#26302B" : "transparent"
                    }
                }
            }

            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }

            ScrollView {
                id: sourceScroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.margins: 20
                clip: true

                ColumnLayout {
                    width: sourceScroll.availableWidth
                    spacing: 14

                    SectionLabel { text: "MEDIA INPUT" }

                    Rectangle {
                        objectName: "dependencyWarning"
                        visible: !root.appBackend.dependencyStatus.ready
                        Layout.fillWidth: true
                        Layout.preferredHeight: visible ? 92 : 0
                        radius: 12
                        color: "#2B1E1B"
                        border.color: "#704038"

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 12
                            Rectangle {
                                Layout.preferredWidth: 38
                                Layout.preferredHeight: 38
                                radius: 10
                                color: "#462822"
                                Text {
                                    anchors.centerIn: parent
                                    text: "!"
                                    color: root.danger
                                    font.pixelSize: 20
                                    font.weight: Font.Bold
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2
                                Text {
                                    text: "実行ツールが不足しています"
                                    color: root.textPrimary
                                    font.family: "Yu Gothic UI"
                                    font.pixelSize: 12
                                    font.weight: Font.DemiBold
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: "不足: " + root.appBackend.dependencyStatus.missing.join(", ")
                                        + "\nFFmpegをPATHへ追加し、WhisperXは使用中のPython環境へインストールしてください。"
                                    color: "#D9A69A"
                                    font.family: "Yu Gothic UI"
                                    font.pixelSize: 10
                                    wrapMode: Text.Wrap
                                }
                            }
                            Button {
                                text: "RECHECK"
                                onClicked: root.appBackend.refreshDependencies()
                            }
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 96
                        radius: 12
                        color: videoDropArea.containsDrag ? "#25331F" : "#171E1A"
                        border.color: videoDropArea.containsDrag ? root.acid : root.border
                        border.width: videoDropArea.containsDrag ? 2 : 1

                        DropArea {
                            id: videoDropArea
                            anchors.fill: parent
                            onDropped: function(drop) {
                                if (drop.hasUrls && drop.urls.length > 0) {
                                    root.appBackend.setVideoFile(drop.urls[0].toString())
                                    drop.acceptProposedAction()
                                }
                            }
                        }

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 14
                            Rectangle {
                                Layout.preferredWidth: 46
                                Layout.preferredHeight: 46
                                radius: 12
                                color: "#283420"
                                Text {
                                    anchors.centerIn: parent
                                    text: "▶"
                                    color: root.acid
                                    font.pixelSize: 17
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 3
                                Text {
                                    text: "VIDEO"
                                    color: root.textMuted
                                    font.family: "Bahnschrift"
                                    font.pixelSize: 10
                                    font.weight: Font.Bold
                                    font.letterSpacing: 1.3
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: root.appBackend.sourceSelection.video || "動画をここへドロップ"
                                    color: root.appBackend.sourceSelection.video ? root.textPrimary : root.textMuted
                                    font.family: "Yu Gothic UI"
                                    font.pixelSize: 12
                                    elide: Text.ElideMiddle
                                }
                            }
                            Button {
                                text: "BROWSE"
                                onClicked: root.appBackend.browseVideoFile()
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.max(142, 78 + Math.min(3, root.appBackend.speakers.length) * 42)
                        radius: 12
                        color: audioDropArea.containsDrag ? "#25331F" : "#171E1A"
                        border.color: audioDropArea.containsDrag ? root.acid : root.border
                        border.width: audioDropArea.containsDrag ? 2 : 1

                        DropArea {
                            id: audioDropArea
                            anchors.fill: parent
                            onDropped: function(drop) {
                                if (drop.hasUrls) {
                                    var paths = []
                                    for (var i = 0; i < drop.urls.length; ++i)
                                        paths.push(drop.urls[i].toString())
                                    root.appBackend.setAudioFiles(paths, true)
                                    drop.acceptProposedAction()
                                }
                            }
                        }

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 8
                            RowLayout {
                                Layout.fillWidth: true
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 1
                                    Text {
                                        text: "SPEAKER AUDIO"
                                        color: root.textMuted
                                        font.family: "Bahnschrift"
                                        font.pixelSize: 10
                                        font.weight: Font.Bold
                                        font.letterSpacing: 1.3
                                    }
                                    Text {
                                        text: root.appBackend.speakers.length > 0
                                            ? root.appBackend.speakers.length + " tracks selected"
                                            : "複数の話者音声をまとめてドロップ"
                                        color: root.appBackend.speakers.length > 0 ? root.textPrimary : root.textMuted
                                        font.family: "Yu Gothic UI"
                                        font.pixelSize: 12
                                    }
                                }
                                Button {
                                    text: "CLEAR"
                                    enabled: root.appBackend.speakers.length > 0
                                    onClicked: root.appBackend.clearAudioFiles()
                                }
                                Button {
                                    text: "ADD FILES"
                                    onClicked: root.appBackend.browseAudioFiles()
                                }
                            }
                            ListView {
                                id: sourceAudioList
                                Layout.fillWidth: true
                                Layout.preferredHeight: Math.min(contentHeight, 126)
                                visible: count > 0
                                clip: true
                                spacing: 4
                                model: root.appBackend.speakers
                                delegate: Rectangle {
                                    id: sourceAudioDelegate
                                    required property int index
                                    required property var modelData
                                    width: sourceAudioList.width
                                    height: 38
                                    radius: 8
                                    color: "#202823"
                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 9
                                        anchors.rightMargin: 5
                                        spacing: 8
                                        Rectangle {
                                            Layout.preferredWidth: 8
                                            Layout.preferredHeight: 22
                                            radius: 4
                                            color: sourceAudioDelegate.modelData.color
                                        }
                                        Text {
                                            Layout.fillWidth: true
                                            text: sourceAudioDelegate.modelData.file_name
                                            color: root.textPrimary
                                            font.family: "Yu Gothic UI"
                                            font.pixelSize: 11
                                            elide: Text.ElideMiddle
                                        }
                                        ToolButton {
                                            id: removeAudioButton
                                            text: "×"
                                            onClicked: root.appBackend.removeAudioFile(sourceAudioDelegate.index)
                                            contentItem: Text {
                                                text: removeAudioButton.text
                                                color: root.textMuted
                                                font.pixelSize: 17
                                                horizontalAlignment: Text.AlignHCenter
                                                verticalAlignment: Text.AlignVCenter
                                            }
                                            background: Rectangle {
                                                radius: 6
                                                color: removeAudioButton.hovered ? "#3A2825" : "transparent"
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 82
                        radius: 12
                        color: outputDropArea.containsDrag ? "#25331F" : "#171E1A"
                        border.color: outputDropArea.containsDrag ? root.acid : root.border

                        DropArea {
                            id: outputDropArea
                            anchors.fill: parent
                            onDropped: function(drop) {
                                if (drop.hasUrls && drop.urls.length > 0) {
                                    root.appBackend.setOutputDirectory(drop.urls[0].toString())
                                    drop.acceptProposedAction()
                                }
                            }
                        }

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 14
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 3
                                Text {
                                    text: "OUTPUT DIRECTORY"
                                    color: root.textMuted
                                    font.family: "Bahnschrift"
                                    font.pixelSize: 10
                                    font.weight: Font.Bold
                                    font.letterSpacing: 1.3
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: root.appBackend.sourceSelection.output_dir || "出力先フォルダを指定"
                                    color: root.appBackend.sourceSelection.output_dir ? root.textPrimary : root.textMuted
                                    font.family: "Yu Gothic UI"
                                    font.pixelSize: 12
                                    elide: Text.ElideMiddle
                                }
                            }
                            Button {
                                text: "BROWSE"
                                onClicked: root.appBackend.browseOutputDirectory()
                            }
                        }
                    }

                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }
                    SectionLabel { text: "ALIGNMENT REFERENCE" }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: 12
                        rowSpacing: 8

                        Text {
                            text: "基準にする話者音声"
                            color: root.textPrimary
                            font.family: "Yu Gothic UI"
                            font.pixelSize: 12
                        }
                        Text {
                            text: "照合する動画音声トラック"
                            color: root.textPrimary
                            font.family: "Yu Gothic UI"
                            font.pixelSize: 12
                        }

                        ComboBox {
                            id: referenceCombo
                            Layout.fillWidth: true
                            model: root.appBackend.speakers
                            textRole: "file_name"
                            valueRole: "path"
                            enabled: count > 0
                            ToolTip.visible: hovered
                            ToolTip.text: "動画側にも同じ声が収録されている話者音声を選びます。どれか分からない場合は、動画の音声トラックと一致するファイルを選んでください。"
                        }
                        ComboBox {
                            id: trackCombo
                            Layout.fillWidth: true
                            model: root.appBackend.audioTracks
                            textRole: "label"
                            valueRole: "selector"
                            enabled: count > 0
                            ToolTip.visible: hovered
                            ToolTip.text: "基準音声と一致する動画側トラックを指定します。不明なら自動検出のままで構いません。"
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 4
                            Text {
                                text: "手動オフセット補正"
                                color: root.textPrimary
                                font.family: "Yu Gothic UI"
                                font.pixelSize: 12
                            }
                            Text {
                                text: "自動検出値に加算。正の値で字幕を後ろ、負の値で前へ移動"
                                color: root.textMuted
                                font.family: "Yu Gothic UI"
                                font.pixelSize: 10
                            }
                        }
                        EditField {
                            id: manualOffsetField
                            Layout.preferredWidth: 112
                            text: "0.000"
                            helpText: "同期解析で求めたオフセットに加える秒数です。通常は0のまま使い、微調整が必要な場合だけ変更します。"
                            validator: DoubleValidator { bottom: -120; top: 120; decimals: 3 }
                        }
                        Text { text: "SEC"; color: root.textMuted; font.pixelSize: 10 }
                        Button {
                            id: analyzeButton
                            text: root.appBackend.alignmentBusy ? "ANALYZING..." : "ANALYZE SYNC"
                            enabled: !root.appBackend.alignmentBusy
                                && root.appBackend.sourceSelection.video
                                && root.appBackend.speakers.length > 0
                                && root.appBackend.dependencyStatus.ffmpeg
                                && root.appBackend.dependencyStatus.ffprobe
                            onClicked: root.appBackend.analyzeAlignment(
                                referenceCombo.currentValue || "",
                                trackCombo.currentValue || "",
                                Number(manualOffsetField.text || 0)
                            )
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 78
                        radius: 12
                        color: "#101511"
                        border.color: root.appBackend.alignmentResult.status === "解析完了" ? "#5D7534" : root.border

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 24
                            BusyIndicator {
                                running: root.appBackend.alignmentBusy
                                visible: running
                                Layout.preferredWidth: 30
                                Layout.preferredHeight: 30
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2
                                Text {
                                    text: root.appBackend.alignmentResult.status || "未解析"
                                    color: root.appBackend.alignmentResult.status === "解析完了" ? root.acid : root.textPrimary
                                    font.family: "Yu Gothic UI"
                                    font.pixelSize: 12
                                    font.weight: Font.DemiBold
                                }
                                Text {
                                    text: root.appBackend.alignmentResult.error || "ANALYZE SYNCで位置合わせ結果を事前確認できます"
                                    color: root.textMuted
                                    font.family: "Yu Gothic UI"
                                    font.pixelSize: 10
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                            }
                            ColumnLayout {
                                spacing: 2
                                SectionLabel { text: "TRACK" }
                                Text {
                                    text: root.appBackend.alignmentResult.track || "AUTO"
                                    color: root.textPrimary
                                    font.family: "Bahnschrift"
                                    font.pixelSize: 12
                                }
                            }
                            ColumnLayout {
                                spacing: 2
                                SectionLabel { text: "DETECTED" }
                                Text {
                                    text: Number(root.appBackend.alignmentResult.detected_offset || 0).toFixed(3) + "s"
                                    color: root.textPrimary
                                    font.family: "Bahnschrift"
                                    font.pixelSize: 12
                                }
                            }
                            ColumnLayout {
                                spacing: 2
                                SectionLabel { text: "FINAL OFFSET" }
                                Text {
                                    text: Number(root.appBackend.alignmentResult.offset || 0).toFixed(3) + "s"
                                    color: root.amber
                                    font.family: "Bahnschrift"
                                    font.pixelSize: 13
                                    font.weight: Font.Bold
                                }
                            }
                            ColumnLayout {
                                spacing: 2
                                SectionLabel { text: "SCORE" }
                                Text {
                                    text: Number(root.appBackend.alignmentResult.score || 0).toFixed(3)
                                    color: root.textPrimary
                                    font.family: "Bahnschrift"
                                    font.pixelSize: 12
                                }
                            }
                        }
                    }

                    Item { Layout.preferredHeight: 4 }
                }
            }

            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }
            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 64
                Layout.leftMargin: 20
                Layout.rightMargin: 20
                Text {
                    Layout.fillWidth: true
                    text: "素材パスは保存されません。次回起動時は空の状態に戻ります。"
                    color: root.textMuted
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 10
                }
                Button {
                    id: applySourceButton
                    Layout.preferredWidth: 150
                    text: "APPLY & CLOSE"
                    enabled: root.appBackend.sourceSelection.video
                        && root.appBackend.sourceSelection.output_dir
                        && root.appBackend.speakers.length > 0
                    onClicked: sourceSetup.close()
                    contentItem: Text {
                        text: applySourceButton.text
                        color: applySourceButton.enabled ? "#10140F" : "#657067"
                        font.family: "Bahnschrift"
                        font.pixelSize: 11
                        font.weight: Font.Bold
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        radius: 9
                        color: applySourceButton.enabled ? root.acid : "#232A26"
                    }
                }
            }
        }
    }
    Popup {
        id: colorPopup
        property int speakerIndex: -1
        anchors.centerIn: Overlay.overlay
        width: 276
        height: 158
        modal: true
        focus: true
        padding: 18
        background: Rectangle {
            radius: 14
            color: root.panelRaised
            border.color: root.border
        }
        ColumnLayout {
            anchors.fill: parent
            spacing: 14
            SectionLabel { text: "TRACK OUTLINE COLOR" }
            GridLayout {
                Layout.fillWidth: true
                columns: 6
                columnSpacing: 10
                rowSpacing: 10
                Repeater {
                    model: ["#FFD966", "#F6B26B", "#93C47D", "#6FA8DC", "#E78284", "#81C8BE", "#E5C07B", "#56B6C2", "#98C379", "#61AFEF", "#C678DD", "#ABB2BF"]
                    Rectangle {
                        id: paletteSwatch
                        required property string modelData
                        width: 30
                        height: 30
                        radius: 8
                        color: paletteSwatch.modelData
                        border.color: "#FFFFFF"
                        border.width: colorMouse.containsMouse ? 2 : 0
                        MouseArea {
                            id: colorMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                root.appBackend.updateSpeakerColor(colorPopup.speakerIndex, paletteSwatch.modelData)
                                colorPopup.close()
                            }
                        }
                    }
                }
            }
        }
    }

    Component.onCompleted: {
        syncSettings()
        Qt.callLater(function() { sourceSetup.open() })
    }
}
