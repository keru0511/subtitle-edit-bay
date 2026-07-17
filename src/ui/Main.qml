pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtMultimedia

ApplicationWindow {
    id: root
    // qmllint disable unqualified
    property var appBackend: backend
    // qmllint enable unqualified
    property real timelinePixelsPerSecond: 34
    property real editorPixelsPerSecond: 64
    property int snapMilliseconds: 100
    property var subtitleSegmentCache: root.appBackend.subtitleSegments
    property var projectSpeakerCache: root.appBackend.projectSpeakers

    width: 1520
    height: 940
    minimumWidth: 1220
    minimumHeight: 760
    visible: true
    title: "Subtitle Edit Bay"
    color: "#0B0E0D"
    palette.window: "#121715"
    palette.windowText: "#F4F1E8"
    palette.base: "#171D1A"
    palette.text: "#F4F1E8"
    palette.button: "#202823"
    palette.buttonText: "#F4F1E8"
    palette.highlight: "#C8FF3D"
    palette.highlightedText: "#10140F"

    readonly property color panel: "#121715"
    readonly property color raised: "#19201D"
    readonly property color border: "#2A3530"
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
            "nvenc_cq": qualitySpin.value,
            "x264_crf": qualitySpin.value,
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
        qualitySpin.value = Number(value.nvenc_cq || 18)
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
    function transcriptionBlockReason() {
        if (root.appBackend.running)
            return "処理中です。完了または停止するまで入力と編集は変更できません"
        if (!root.appBackend.dependencyStatus.ready)
            return "実行ツールが不足しています: " + root.appBackend.dependencyStatus.missing.join(", ")
        if (!root.appBackend.sourceSelection.video)
            return "SOURCESで動画を指定してください"
        if (root.appBackend.speakers.length === 0)
            return "SOURCESで1つ以上の話者音声を指定してください"
        if (!root.appBackend.sourceSelection.output_dir)
            return "SOURCESで出力先フォルダを指定してください"
        if (root.appBackend.projectLoaded)
            return "文字起こし済みです。やり直す場合はSOURCESで入力を変更してください"
        return ""
    }

    function canSplitSelectedSegment(positionMs) {
        var index = root.appBackend.selectedSegmentIndex
        if (index < 0 || index >= root.appBackend.subtitleSegments.length)
            return false
        var segment = root.appBackend.subtitleSegments[index]
        var seconds = Number(positionMs) / 1000
        return seconds > Number(segment.start) + 0.05 && seconds < Number(segment.end) - 0.05
    }

    function stamp(seconds) {
        var safe = Math.max(0, Number(seconds) || 0)
        var hours = Math.floor(safe / 3600)
        var minutes = Math.floor((safe % 3600) / 60)
        var remainder = (safe % 60).toFixed(2)
        return (hours > 0 ? String(hours).padStart(2, "0") + ":" : "")
            + String(minutes).padStart(2, "0") + ":" + String(remainder).padStart(5, "0")
    }

    function speakerColor(style) {
        var speakers = root.projectSpeakerCache
        for (var i = 0; i < speakers.length; ++i) {
            if (speakers[i].style === style)
                return speakers[i].color
        }
        return root.amber
    }

    function activeCaptions(positionMs) {
        var seconds = positionMs / 1000
        var active = []
        var segments = root.subtitleSegmentCache
        for (var i = 0; i < segments.length; ++i) {
            if (segments[i].start <= seconds && segments[i].end >= seconds)
                active.push(segments[i])
        }
        return active
    }

    function laneForStyle(style) {
        var speakers = root.projectSpeakerCache
        for (var i = 0; i < speakers.length; ++i) {
            if (speakers[i].style === style)
                return i
        }
        return 0
    }

    component PanelTitle: Text {
        color: root.textMuted
        font.family: "Bahnschrift"
        font.pixelSize: 10
        font.weight: Font.Bold
        font.letterSpacing: 1.5
    }

    component SmallButton: Button {
        id: smallControl
        implicitHeight: 32
        contentItem: Text {
            text: smallControl.text
            color: smallControl.enabled ? root.textPrimary : "#59635D"
            font.family: "Bahnschrift"
            font.pixelSize: 10
            font.weight: Font.DemiBold
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 7
            color: smallControl.down ? "#303B35" : (smallControl.hovered ? "#27312C" : "#1A211E")
            border.color: smallControl.activeFocus ? root.acid : root.border
        }
    }

    component TimeField: TextField {
        id: timeControl
        horizontalAlignment: TextInput.AlignRight
        color: root.textPrimary
        selectionColor: root.acid
        font.family: "Cascadia Mono"
        font.pixelSize: 11
        validator: DoubleValidator { bottom: 0; decimals: 3 }
        background: Rectangle {
            radius: 6
            color: "#101512"
            border.color: timeControl.activeFocus ? root.acid : root.border
        }
    }

    component SubtitleOverlay: Item {
        property MediaPlayer player
        Repeater {
            model: root.activeCaptions(parent.player ? parent.player.position : 0)
            delegate: Text {
                required property var modelData
                width: Math.min(implicitWidth + 30, parent.width - 30)
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 26 + Number(modelData.layout_row || 0) * 54
                text: modelData.text
                color: root.speakerColor(modelData.speaker)
                font.family: "Yu Gothic UI"
                font.pixelSize: Math.max(14, Math.round(22 * Number(modelData.subtitle_font_scale || 1)))
                font.weight: Font.Bold
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
                style: Text.Outline
                styleColor: "#D0000000"
            }
        }
    }

    component SubtitleTimeline: Rectangle {
        id: timelineRoot
        property MediaPlayer player
        property real pixelsPerSecond: 40
        property real snapSeconds: 0.1
        property int laneHeight: 42
        property bool editable: true
        signal segmentActivated(int index)

        color: "#0E1311"
        border.color: root.border
        radius: 10
        clip: true

        Flickable {
            id: timelineFlick
            anchors.fill: parent
            anchors.leftMargin: 86
            clip: true
            interactive: true
            boundsBehavior: Flickable.StopAtBounds
            contentWidth: Math.max(width, root.appBackend.projectDuration * timelineRoot.pixelsPerSecond + 120)
            contentHeight: 28 + Math.max(1, root.appBackend.projectSpeakers.length) * timelineRoot.laneHeight

            Item {
                id: timelineCanvas
                width: timelineFlick.contentWidth
                height: timelineFlick.contentHeight

                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton
                    onClicked: function(mouse) {
                        if (timelineRoot.player)
                            timelineRoot.player.position = Math.max(0, mouse.x / timelineRoot.pixelsPerSecond * 1000)
                    }
                }

                Repeater {
                    model: Math.ceil(root.appBackend.projectDuration / 10) + 1
                    delegate: Item {
                        id: rulerTick
                        required property int index
                        x: rulerTick.index * 10 * timelineRoot.pixelsPerSecond
                        width: 1
                        height: timelineCanvas.height
                        Rectangle { anchors.fill: parent; color: "#26302B" }
                        Text {
                            x: 4
                            y: 3
                            text: root.stamp(rulerTick.index * 10)
                            color: root.textMuted
                            font.family: "Cascadia Mono"
                            font.pixelSize: 9
                        }
                    }
                }

                Repeater {
                    model: root.appBackend.projectSpeakers
                    delegate: Rectangle {
                        required property int index
                        y: 28 + index * timelineRoot.laneHeight
                        width: timelineCanvas.width
                        height: timelineRoot.laneHeight
                        color: index % 2 === 0 ? "#111714" : "#0D1210"
                        border.color: "#202923"
                    }
                }

                Repeater {
                    model: root.appBackend.subtitleWaveforms
                    delegate: Item {
                        id: waveDelegate
                        required property var modelData
                        property int laneIndex: root.laneForStyle(modelData.style)
                        x: Number(modelData.offset_seconds || 0) * timelineRoot.pixelsPerSecond
                        y: 31 + laneIndex * timelineRoot.laneHeight
                        width: Number(modelData.duration_seconds || 0) * timelineRoot.pixelsPerSecond
                        height: timelineRoot.laneHeight - 6
                        opacity: 0.3
                        Repeater {
                            model: waveDelegate.modelData.peaks || []
                            delegate: Rectangle {
                                required property int index
                                required property var modelData
                                x: index * waveDelegate.width / Math.max(1, waveDelegate.modelData.peaks.length)
                                anchors.verticalCenter: parent.verticalCenter
                                width: Math.max(1, waveDelegate.width / Math.max(1, waveDelegate.modelData.peaks.length) - 0.5)
                                height: Math.max(1, Number(modelData) * parent.height)
                                color: waveDelegate.modelData.color || root.amber
                            }
                        }
                    }
                }

                Repeater {
                    model: root.appBackend.subtitleSegments
                    delegate: Rectangle {
                        id: captionClip
                        required property int index
                        required property var modelData
                        property real originalX: 0
                        property real originalWidth: 0
                        property real pointerStart: 0
                        x: Number(modelData.start) * timelineRoot.pixelsPerSecond
                        y: 31 + root.laneForStyle(modelData.speaker) * timelineRoot.laneHeight
                        width: Math.max(10, (Number(modelData.end) - Number(modelData.start)) * timelineRoot.pixelsPerSecond)
                        height: timelineRoot.laneHeight - 7
                        radius: 6
                        color: root.speakerColor(modelData.speaker)
                        opacity: root.appBackend.selectedSegmentIndex === index ? 1 : 0.78
                        border.color: root.appBackend.selectedSegmentIndex === index ? root.textPrimary : "#66101010"
                        border.width: root.appBackend.selectedSegmentIndex === index ? 2 : 1

                        Text {
                            anchors.fill: parent
                            anchors.leftMargin: 8
                            anchors.rightMargin: 8
                            text: captionClip.modelData.text
                            color: "#10140F"
                            font.family: "Yu Gothic UI"
                            font.pixelSize: 10
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                            verticalAlignment: Text.AlignVCenter
                        }

                        MouseArea {
                            id: moveArea
                            anchors.fill: parent
                            anchors.leftMargin: 7
                            anchors.rightMargin: 7
                            enabled: timelineRoot.editable
                            cursorShape: Qt.SizeHorCursor
                            drag.target: captionClip
                            drag.axis: Drag.XAxis
                            drag.minimumX: 0
                            drag.maximumX: Math.max(0, timelineCanvas.width - captionClip.width)
                            onPressed: {
                                root.appBackend.selectSegment(captionClip.index)
                                timelineRoot.segmentActivated(captionClip.index)
                            }
                            onReleased: root.appBackend.moveSegment(
                                captionClip.index,
                                captionClip.x / timelineRoot.pixelsPerSecond,
                                (captionClip.x + captionClip.width) / timelineRoot.pixelsPerSecond,
                                timelineRoot.snapSeconds
                            )
                        }

                        Rectangle {
                            id: leftHandle
                            z: 3
                            anchors.left: parent.left
                            anchors.verticalCenter: parent.verticalCenter
                            width: 7
                            height: parent.height
                            radius: 3
                            color: "#EEFFFFFF"
                            visible: timelineRoot.editable && root.appBackend.selectedSegmentIndex === captionClip.index
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.SizeHorCursor
                                onPressed: function(mouse) {
                                    captionClip.originalX = captionClip.x
                                    captionClip.originalWidth = captionClip.width
                                    captionClip.pointerStart = mapToItem(timelineCanvas, mouse.x, mouse.y).x
                                }
                                onPositionChanged: function(mouse) {
                                    if (!pressed) return
                                    var pointer = mapToItem(timelineCanvas, mouse.x, mouse.y).x
                                    var delta = Math.min(captionClip.originalWidth - 4, pointer - captionClip.pointerStart)
                                    captionClip.x = Math.max(0, captionClip.originalX + delta)
                                    captionClip.width = captionClip.originalWidth - (captionClip.x - captionClip.originalX)
                                }
                                onReleased: root.appBackend.resizeSegmentStart(
                                    captionClip.index,
                                    captionClip.x / timelineRoot.pixelsPerSecond,
                                    timelineRoot.snapSeconds
                                )
                            }
                        }

                        Rectangle {
                            id: rightHandle
                            z: 3
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            width: 7
                            height: parent.height
                            radius: 3
                            color: "#EEFFFFFF"
                            visible: timelineRoot.editable && root.appBackend.selectedSegmentIndex === captionClip.index
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.SizeHorCursor
                                onPressed: function(mouse) {
                                    captionClip.originalWidth = captionClip.width
                                    captionClip.pointerStart = mapToItem(timelineCanvas, mouse.x, mouse.y).x
                                }
                                onPositionChanged: function(mouse) {
                                    if (!pressed) return
                                    var pointer = mapToItem(timelineCanvas, mouse.x, mouse.y).x
                                    captionClip.width = Math.max(4, captionClip.originalWidth + pointer - captionClip.pointerStart)
                                }
                                onReleased: root.appBackend.resizeSegmentEnd(
                                    captionClip.index,
                                    (captionClip.x + captionClip.width) / timelineRoot.pixelsPerSecond,
                                    timelineRoot.snapSeconds
                                )
                            }
                        }
                    }
                }

                Rectangle {
                    z: 10
                    x: Math.max(0, (timelineRoot.player ? timelineRoot.player.position : 0) / 1000 * timelineRoot.pixelsPerSecond)
                    y: 0
                    width: 2
                    height: timelineCanvas.height
                    color: root.acid
                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter
                        width: 9
                        height: 9
                        radius: 5
                        color: root.acid
                    }
                }
            }
            ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AlwaysOn }
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
        }

        Column {
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: 86
            topPadding: 28
            Repeater {
                model: root.appBackend.projectSpeakers
                delegate: Rectangle {
                    id: laneLabel
                    required property var modelData
                    width: 86
                    height: timelineRoot.laneHeight
                    color: "#171E1A"
                    border.color: root.border
                    Row {
                        anchors.centerIn: parent
                        spacing: 6
                        Rectangle { width: 7; height: 22; radius: 3; color: laneLabel.modelData.color }
                        Text {
                            width: 62
                            text: laneLabel.modelData.name
                            color: root.textPrimary
                            font.family: "Yu Gothic UI"
                            font.pixelSize: 10
                            elide: Text.ElideRight
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                }
            }
        }
    }

    header: Rectangle {
        height: 62
        color: "#101512"
        border.color: root.border
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 20
            anchors.rightMargin: 20
            spacing: 14
            ColumnLayout {
                spacing: 0
                Text { text: "SUBTITLE EDIT BAY"; color: root.textPrimary; font.family: "Bahnschrift"; font.pixelSize: 18; font.weight: Font.Bold; font.letterSpacing: 1.5 }
                Text { text: "TRANSCRIBE  /  EDIT  /  RENDER"; color: root.acid; font.family: "Bahnschrift"; font.pixelSize: 9; font.letterSpacing: 1.2 }
            }
            Rectangle { Layout.preferredWidth: 1; Layout.preferredHeight: 30; color: root.border }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1
                Text {
                    Layout.fillWidth: true
                    text: root.appBackend.projectLoaded ? root.appBackend.projectName : "編集プロジェクト未作成"
                    color: root.appBackend.projectLoaded ? root.textPrimary : root.textMuted
                    font.family: "Yu Gothic UI"; font.pixelSize: 12; elide: Text.ElideMiddle
                }
                Text {
                    text: root.appBackend.projectDirty ? "● AUTOSAVE PENDING" : (root.appBackend.projectLoaded ? "✓ SAVED" : "文字起こし後に自動作成")
                    color: root.appBackend.projectDirty ? root.amber : root.textMuted
                    font.family: "Bahnschrift"; font.pixelSize: 9
                }
            }
            SmallButton { text: "OPEN PROJECT"; enabled: !root.appBackend.running; onClicked: root.appBackend.browseProjectFile() }
            SmallButton { text: "SOURCES"; enabled: !root.appBackend.running; onClicked: sourcePopup.open() }
            Rectangle { Layout.preferredWidth: 9; Layout.preferredHeight: 9; radius: 5; color: root.appBackend.running ? root.amber : root.acid }
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 10

        Rectangle {
            Layout.preferredWidth: 270
            Layout.fillHeight: true
            radius: 12
            color: root.panel
            border.color: root.border
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 10
                PanelTitle { text: "SOURCE & SPEAKERS" }
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: 70; radius: 9; color: root.raised; border.color: root.border
                    Column { anchors.fill: parent; anchors.margins: 10; spacing: 3
                        Text { text: "VIDEO"; color: root.textMuted; font.pixelSize: 9; font.family: "Bahnschrift" }
                        Text { width: parent.width; text: root.appBackend.sourceSelection.video || "未選択"; color: root.textPrimary; font.pixelSize: 11; font.family: "Yu Gothic UI"; elide: Text.ElideMiddle }
                        Text { width: parent.width; text: root.appBackend.sourceSelection.output_dir || "出力先未選択"; color: root.textMuted; font.pixelSize: 10; font.family: "Yu Gothic UI"; elide: Text.ElideMiddle }
                    }
                }
                ListView {
                    id: speakerSourceList
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 6
                    model: root.appBackend.speakers
                    delegate: Rectangle {
                        id: speakerSourceDelegate
                        required property int index
                        required property var modelData
                        width: speakerSourceList.width; height: 48; radius: 8; color: root.raised
                        RowLayout { anchors.fill: parent; anchors.margins: 8; spacing: 8
                            Rectangle { Layout.preferredWidth: 8; Layout.preferredHeight: 28; radius: 4; color: speakerSourceDelegate.modelData.color }
                            ColumnLayout { Layout.fillWidth: true; spacing: 0
                                Text { Layout.fillWidth: true; text: speakerSourceDelegate.modelData.name; color: root.textPrimary; font.pixelSize: 11; font.family: "Yu Gothic UI"; elide: Text.ElideRight }
                                Text { Layout.fillWidth: true; text: speakerSourceDelegate.modelData.file_name; color: root.textMuted; font.pixelSize: 9; font.family: "Bahnschrift"; elide: Text.ElideMiddle }
                            }
                            ToolButton { text: "×"; enabled: !root.appBackend.running; onClicked: root.appBackend.removeAudioFile(speakerSourceDelegate.index) }
                        }
                    }
                }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }
                PanelTitle { text: "ALIGNMENT" }
                ComboBox { id: referenceCombo; Layout.fillWidth: true; model: root.appBackend.speakers; textRole: "file_name"; valueRole: "path" }
                ComboBox { id: trackCombo; Layout.fillWidth: true; model: root.appBackend.audioTracks; textRole: "label"; valueRole: "selector" }
                RowLayout { Layout.fillWidth: true
                    TimeField { id: manualOffsetField; Layout.fillWidth: true; text: "0.000"; validator: DoubleValidator { bottom: -120; top: 120; decimals: 3 } }
                    SmallButton {
                        text: root.appBackend.alignmentBusy ? "ANALYZING" : "SYNC"
                        enabled: !root.appBackend.running && !root.appBackend.alignmentBusy && root.appBackend.speakers.length > 0 && root.appBackend.sourceSelection.video
                        onClicked: root.appBackend.analyzeAlignment(referenceCombo.currentValue || "", trackCombo.currentValue || "", Number(manualOffsetField.text || 0))
                    }
                }
                Text { Layout.fillWidth: true; text: root.appBackend.alignmentResult.status + (root.appBackend.alignmentResult.offset !== undefined ? "  " + Number(root.appBackend.alignmentResult.offset).toFixed(3) + "s" : ""); color: root.textMuted; font.pixelSize: 10; font.family: "Yu Gothic UI" }
                SmallButton { Layout.fillWidth: true; text: "CHANGE SOURCES"; enabled: !root.appBackend.running; onClicked: sourcePopup.open() }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 10

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 300
                radius: 12
                color: "#080A09"
                border.color: root.border
                clip: true
                MediaPlayer {
                    id: mainPlayer
                    source: root.appBackend.previewUrl
                    videoOutput: mainVideo
                    audioOutput: AudioOutput { volume: 0.7 }
                    onPositionChanged: if (!mainSeek.pressed) mainSeek.value = position
                    onDurationChanged: mainSeek.to = Math.max(1, duration)
                }
                VideoOutput { id: mainVideo; anchors.fill: parent; anchors.bottomMargin: 58; fillMode: VideoOutput.PreserveAspectFit }
                SubtitleOverlay { anchors.fill: mainVideo; player: mainPlayer }
                ColumnLayout {
                    anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                    anchors.margins: 12; spacing: 2
                    Slider { id: mainSeek; Layout.fillWidth: true; from: 0; to: 1; onMoved: mainPlayer.position = value }
                    RowLayout { Layout.fillWidth: true
                        ToolButton { text: mainPlayer.playbackState === MediaPlayer.PlayingState ? "Ⅱ" : "▶"; onClicked: mainPlayer.playbackState === MediaPlayer.PlayingState ? mainPlayer.pause() : mainPlayer.play() }
                        Text { Layout.fillWidth: true; text: root.appBackend.sourceSelection.video ? root.appBackend.sourceSelection.video.split(/[\\/]/).pop() : "NO VIDEO"; color: root.textPrimary; font.pixelSize: 11; font.family: "Bahnschrift"; elide: Text.ElideMiddle }
                        Text { text: root.stamp(mainPlayer.position / 1000) + " / " + root.stamp(mainPlayer.duration / 1000); color: root.textMuted; font.pixelSize: 10; font.family: "Cascadia Mono" }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(270, 86 + Math.max(1, root.appBackend.projectSpeakers.length) * 42)
                radius: 12; color: root.panel; border.color: root.border
                ColumnLayout { anchors.fill: parent; anchors.margins: 9; spacing: 7
                    RowLayout { Layout.fillWidth: true
                        PanelTitle { text: "EDITABLE TIMELINE" }
                        Item { Layout.fillWidth: true }
                        Text { text: root.appBackend.subtitleSegments.length + " CAPTIONS"; color: root.textMuted; font.pixelSize: 9; font.family: "Bahnschrift" }
                        Text { text: "ZOOM"; color: root.textMuted; font.pixelSize: 9 }
                        Slider { Layout.preferredWidth: 110; from: 12; to: 120; value: root.timelinePixelsPerSecond; onMoved: root.timelinePixelsPerSecond = value }
                        SmallButton { text: "OPEN EDITOR"; enabled: root.appBackend.projectLoaded && !root.appBackend.running; onClicked: editorPopup.open() }
                    }
                    SubtitleTimeline {
                        Layout.fillWidth: true; Layout.fillHeight: true
                        player: mainPlayer
                        pixelsPerSecond: root.timelinePixelsPerSecond
                        snapSeconds: root.snapMilliseconds / 1000
                        editable: root.appBackend.projectLoaded && !root.appBackend.running
                        onSegmentActivated: function(index) { mainPlayer.position = root.appBackend.subtitleSegments[index].start * 1000 }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true; Layout.preferredHeight: 118; radius: 12; color: root.panel; border.color: root.border
                RowLayout { anchors.fill: parent; anchors.margins: 12; spacing: 12
                    ColumnLayout { Layout.preferredWidth: 180
                        PanelTitle { text: root.appBackend.stage }
                        Text { text: Math.round(root.appBackend.progress * 100) + "%"; color: root.acid; font.family: "Bahnschrift"; font.pixelSize: 25; font.weight: Font.Bold }
                        Text { Layout.fillWidth: true; text: root.appBackend.status; color: root.textMuted; font.pixelSize: 10; font.family: "Yu Gothic UI"; wrapMode: Text.Wrap }
                    }
                    ProgressBar { Layout.preferredWidth: 160; from: 0; to: 1; value: root.appBackend.progress }
                    ScrollView { Layout.fillWidth: true; Layout.fillHeight: true
                        TextArea { readOnly: true; text: root.appBackend.logText || "処理ログ"; color: root.textMuted; font.family: "Cascadia Mono"; font.pixelSize: 9; wrapMode: TextEdit.WrapAnywhere; background: Rectangle { color: "transparent" } }
                    }
                }
            }
        }

        Rectangle {
            Layout.preferredWidth: 320
            Layout.fillHeight: true
            radius: 12; color: root.panel; border.color: root.border
            ColumnLayout { anchors.fill: parent; spacing: 0
                Text { Layout.margins: 14; text: "WORKFLOW & SETTINGS"; color: root.textPrimary; font.family: "Bahnschrift"; font.pixelSize: 13; font.weight: Font.Bold; font.letterSpacing: 1.1 }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }
                ScrollView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                    ColumnLayout { width: 286; x: 16; spacing: 10
                        Item { Layout.preferredHeight: 2 }
                        PanelTitle { text: "ENGINE" }
                        RowLayout { Layout.fillWidth: true; Text { text: "Device"; color: root.textPrimary; Layout.fillWidth: true } ComboBox { id: deviceCombo; model: ["cuda", "cpu"]; Layout.preferredWidth: 110 } }
                        RowLayout { Layout.fillWidth: true; Text { text: "Whisper"; color: root.textPrimary; Layout.fillWidth: true } ComboBox { id: modelCombo; model: ["large-v3", "medium", "small"]; Layout.preferredWidth: 130 } }
                        RowLayout { Layout.fillWidth: true; Text { text: "CPU workers"; color: root.textPrimary; Layout.fillWidth: true } SpinBox { id: workersSpin; from: 1; to: 16; value: 4 } }
                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }
                        PanelTitle { text: "SUBTITLE" }
                        RowLayout { Layout.fillWidth: true; Text { text: "Base font"; color: root.textPrimary; Layout.fillWidth: true } SpinBox { id: fontSizeSpin; from: 32; to: 96; value: 50 } }
                        RowLayout { Layout.fillWidth: true; Text { text: "Volume ratio"; color: root.textPrimary; Layout.fillWidth: true } SpinBox { id: volumeScaleSpin; from: 0; to: 50; value: 20 } Text { text: "%"; color: root.textMuted } }
                        RowLayout { Layout.fillWidth: true; Text { text: "Word gap"; color: root.textPrimary; Layout.fillWidth: true } TimeField { id: gapField; Layout.preferredWidth: 76; text: "0.10" } }
                        RowLayout { Layout.fillWidth: true; Text { text: "End padding"; color: root.textPrimary; Layout.fillWidth: true } TimeField { id: paddingField; Layout.preferredWidth: 76; text: "0.08" } }
                        RowLayout { Layout.fillWidth: true; Text { text: "Min duration"; color: root.textPrimary; Layout.fillWidth: true } TimeField { id: minDurationField; Layout.preferredWidth: 76; text: "0.35" } }
                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }
                        PanelTitle { text: "VIDEO & AUDIO" }
                        RowLayout { Layout.fillWidth: true; Text { text: "Codec"; color: root.textPrimary; Layout.fillWidth: true } ComboBox { id: codecCombo; model: ["h264_nvenc", "libx264"]; Layout.preferredWidth: 132 } }
                        RowLayout { Layout.fillWidth: true; Text { text: "Quality"; color: root.textPrimary; Layout.fillWidth: true } SpinBox { id: qualitySpin; from: 14; to: 28; value: 18 } }
                        Switch { id: normalizeSwitch; text: "音量を正規化"; checked: true }
                        RowLayout { Layout.fillWidth: true; Text { text: "Target LUFS"; color: root.textPrimary; Layout.fillWidth: true } TimeField { id: lufsField; Layout.preferredWidth: 76; text: "-16"; validator: DoubleValidator { bottom: -30; top: -5 } } }
                        Switch { id: silenceSwitch; text: "無音部分をカット" }
                        RowLayout { Layout.fillWidth: true; enabled: silenceSwitch.checked; opacity: enabled ? 1 : 0.4; Text { text: "Minimum silence"; color: root.textPrimary; Layout.fillWidth: true } TimeField { id: silenceField; Layout.preferredWidth: 76; text: "1.2" } }
                        RowLayout { Layout.fillWidth: true; enabled: silenceSwitch.checked; opacity: enabled ? 1 : 0.4; Text { text: "Speech padding"; color: root.textPrimary; Layout.fillWidth: true } TimeField { id: speechPaddingField; Layout.preferredWidth: 76; text: "0.25" } }
                        Item { Layout.preferredHeight: 6 }
                    }
                }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }
                ColumnLayout { Layout.fillWidth: true; Layout.margins: 12; spacing: 7
                    Button {
                        id: transcribeButton
                        objectName: "transcribeButton"
                        Layout.fillWidth: true; Layout.preferredHeight: 42
                        enabled: !root.appBackend.running && root.appBackend.sourceSelection.video && root.appBackend.sourceSelection.output_dir && root.appBackend.speakers.length > 0 && root.appBackend.dependencyStatus.ready && !root.appBackend.projectLoaded
                        text: root.appBackend.activeJob === "transcribe" ? "TRANSCRIBING..." : "1  TRANSCRIBE"
                        onClicked: root.appBackend.startTranscription(root.currentSettings())
                    }
                    Button {
                        id: editButton
                        objectName: "editSubtitlesButton"
                        Layout.fillWidth: true; Layout.preferredHeight: 42
                        enabled: root.appBackend.projectLoaded && !root.appBackend.running
                        text: "2  EDIT SUBTITLES"
                        onClicked: editorPopup.open()
                    }
                    Button {
                        id: renderButton
                        objectName: "renderVideoButton"
                        Layout.fillWidth: true; Layout.preferredHeight: 42
                        enabled: root.appBackend.projectLoaded && !root.appBackend.running
                        text: root.appBackend.activeJob === "render" ? "RENDERING..." : "3  RENDER VIDEO"
                        onClicked: root.appBackend.renderVideo(root.currentSettings())
                        contentItem: Text { text: renderButton.text; color: renderButton.enabled ? "#10140F" : "#68716B"; font.family: "Bahnschrift"; font.pixelSize: 12; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                        background: Rectangle { radius: 8; color: renderButton.enabled ? root.acid : "#252C28" }
                    }
                    Text { objectName: "workflowBlockReason"; Layout.fillWidth: true; text: root.transcriptionBlockReason(); visible: text.length > 0; color: root.amber; font.family: "Yu Gothic UI"; font.pixelSize: 9; wrapMode: Text.Wrap }
                    RowLayout { Layout.fillWidth: true
                        SmallButton { Layout.fillWidth: true; text: root.appBackend.running ? "STOP" : "SAVE SETTINGS"; onClicked: root.appBackend.running ? root.appBackend.cancelProcessing() : root.appBackend.saveSettings(root.currentSettings()) }
                        SmallButton { objectName: "outputFolderButton"; Layout.fillWidth: true; text: "OUTPUT"; enabled: Boolean(root.appBackend.sourceSelection.output_dir); onClicked: root.appBackend.openOutputFolder() }
                    }
                }
            }
        }
    }

    Popup {
        id: sourcePopup
        anchors.centerIn: Overlay.overlay
        width: 620; height: 520; modal: true; focus: true; closePolicy: Popup.CloseOnEscape
        background: Rectangle { radius: 14; color: root.panel; border.color: root.border }
        ColumnLayout { anchors.fill: parent; anchors.margins: 18; spacing: 12
            RowLayout { Layout.fillWidth: true; Text { text: "SOURCE SETUP"; color: root.textPrimary; font.family: "Bahnschrift"; font.pixelSize: 17; font.weight: Font.Bold; Layout.fillWidth: true } ToolButton { text: "×"; onClicked: sourcePopup.close() } }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: visible ? 62 : 0
                visible: !root.appBackend.dependencyStatus.ready
                radius: 8
                color: "#30201C"
                border.color: root.danger
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 9
                    Text {
                        Layout.fillWidth: true
                        text: "不足ツール: " + root.appBackend.dependencyStatus.missing.join(", ")
                        color: root.textPrimary
                        font.family: "Yu Gothic UI"
                        font.pixelSize: 10
                        wrapMode: Text.Wrap
                    }
                    SmallButton { text: "RECHECK"; enabled: !root.appBackend.running; onClicked: root.appBackend.refreshDependencies() }
                }
            }
            PanelTitle { text: "VIDEO" }
            RowLayout { Layout.fillWidth: true; Text { Layout.fillWidth: true; text: root.appBackend.sourceSelection.video || "未選択"; color: root.textMuted; elide: Text.ElideMiddle } SmallButton { text: "BROWSE"; enabled: !root.appBackend.running; onClicked: root.appBackend.browseVideoFile() } }
            PanelTitle { text: "SPEAKER AUDIO" }
            ListView {
                id: sourceAudioList
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 5; model: root.appBackend.speakers
                delegate: Rectangle { id: sourceAudioDelegate; required property int index; required property var modelData; width: sourceAudioList.width; height: 38; radius: 7; color: root.raised
                    RowLayout { anchors.fill: parent; anchors.margins: 7; Rectangle { Layout.preferredWidth: 7; Layout.preferredHeight: 22; radius: 3; color: sourceAudioDelegate.modelData.color } Text { Layout.fillWidth: true; text: sourceAudioDelegate.modelData.file_name; color: root.textPrimary; elide: Text.ElideMiddle } ToolButton { text: "×"; enabled: !root.appBackend.running; onClicked: root.appBackend.removeAudioFile(sourceAudioDelegate.index) } }
                }
            }
            RowLayout { Layout.fillWidth: true; SmallButton { text: "ADD AUDIO"; enabled: !root.appBackend.running; onClicked: root.appBackend.browseAudioFiles() } SmallButton { text: "CLEAR"; enabled: !root.appBackend.running; onClicked: root.appBackend.clearAudioFiles() } Item { Layout.fillWidth: true } }
            PanelTitle { text: "OUTPUT DIRECTORY" }
            RowLayout { Layout.fillWidth: true; Text { Layout.fillWidth: true; text: root.appBackend.sourceSelection.output_dir || "未選択"; color: root.textMuted; elide: Text.ElideMiddle } SmallButton { text: "BROWSE"; enabled: !root.appBackend.running; onClicked: root.appBackend.browseOutputDirectory() } }
            RowLayout { Layout.fillWidth: true; Item { Layout.fillWidth: true } Button { text: "DONE"; onClicked: sourcePopup.close() } }
        }
    }

    Popup {
        id: editorPopup
        anchors.centerIn: Overlay.overlay
        width: root.width - 36
        height: root.height - 36
        modal: true
        focus: true
        padding: 0
        closePolicy: Popup.CloseOnEscape
        onOpened: {
            editorPlayer.source = root.appBackend.previewUrl
            editorPlayer.position = mainPlayer.position
        }
        onClosed: {
            mainPlayer.position = editorPlayer.position
            editorPlayer.pause()
        }
        background: Rectangle { radius: 14; color: "#0D1210"; border.color: "#46564E" }

        MediaPlayer {
            id: editorPlayer
            videoOutput: editorVideo
            audioOutput: AudioOutput { volume: 0.75 }
            onPositionChanged: if (!editorSeek.pressed) editorSeek.value = position
            onDurationChanged: editorSeek.to = Math.max(1, duration)
        }

        ColumnLayout {
            anchors.fill: parent
            spacing: 0
            RowLayout {
                Layout.fillWidth: true; Layout.preferredHeight: 58; Layout.leftMargin: 14; Layout.rightMargin: 10; spacing: 8
                Text { text: "SUBTITLE EDITOR"; color: root.textPrimary; font.family: "Bahnschrift"; font.pixelSize: 17; font.weight: Font.Bold; font.letterSpacing: 1.3 }
                Text { text: root.appBackend.projectDirty ? "● EDITED" : "✓ SAVED"; color: root.appBackend.projectDirty ? root.amber : root.acid; font.family: "Bahnschrift"; font.pixelSize: 9 }
                Text { objectName: "editorStatusText"; Layout.fillWidth: true; Layout.minimumWidth: 80; text: root.appBackend.stage + " · " + root.appBackend.status; color: root.appBackend.stage === "ERROR" ? root.danger : ((root.appBackend.stage === "CHECK" || root.appBackend.stage === "BUSY") ? root.amber : root.textMuted); font.family: "Yu Gothic UI"; font.pixelSize: 9; horizontalAlignment: Text.AlignRight; elide: Text.ElideRight }
                SmallButton { text: "UNDO"; enabled: root.appBackend.canUndo; onClicked: root.appBackend.undoSubtitleEdit() }
                SmallButton { text: "REDO"; enabled: root.appBackend.canRedo; onClicked: root.appBackend.redoSubtitleEdit() }
                SmallButton { text: "+ CAPTION"; onClicked: root.appBackend.addSegment(editorPlayer.position / 1000) }
                SmallButton { objectName: "splitCaptionButton"; text: "SPLIT"; enabled: root.canSplitSelectedSegment(editorPlayer.position); onClicked: root.appBackend.splitSelectedSegment(editorPlayer.position / 1000) }
                SmallButton { text: "DELETE"; enabled: root.appBackend.selectedSegmentIndex >= 0; onClicked: root.appBackend.deleteSelectedSegment() }
                SmallButton { text: "SAVE"; onClicked: root.appBackend.saveProject() }
                SmallButton { text: "BUILD ASS"; onClicked: root.appBackend.buildSubtitlePreview(root.currentSettings()) }
                ToolButton { text: "×"; onClicked: editorPopup.close() }
            }
            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }

            RowLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.margins: 10; spacing: 10
                ColumnLayout {
                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 760; spacing: 8
                    Rectangle {
                        Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 220; radius: 10; color: "#060806"; border.color: root.border; clip: true
                        VideoOutput { id: editorVideo; anchors.fill: parent; anchors.bottomMargin: 54; fillMode: VideoOutput.PreserveAspectFit }
                        SubtitleOverlay { anchors.fill: editorVideo; player: editorPlayer }
                        ColumnLayout { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 8; spacing: 1
                            Slider { id: editorSeek; Layout.fillWidth: true; from: 0; to: 1; onMoved: editorPlayer.position = value }
                            RowLayout { Layout.fillWidth: true
                                ToolButton { text: editorPlayer.playbackState === MediaPlayer.PlayingState ? "Ⅱ" : "▶"; onClicked: editorPlayer.playbackState === MediaPlayer.PlayingState ? editorPlayer.pause() : editorPlayer.play() }
                                Text { Layout.fillWidth: true; text: root.stamp(editorPlayer.position / 1000); color: root.textPrimary; font.family: "Cascadia Mono"; font.pixelSize: 11 }
                                Text { text: root.activeCaptions(editorPlayer.position).length + " active"; color: root.textMuted; font.pixelSize: 10 }
                            }
                        }
                    }
                    RowLayout { Layout.fillWidth: true
                        PanelTitle { text: "TIMELINE" }
                        Item { Layout.fillWidth: true }
                        Text { text: "SNAP"; color: root.textMuted; font.pixelSize: 9 }
                        SpinBox { id: snapSpin; from: 0; to: 1000; stepSize: 10; value: root.snapMilliseconds; editable: true; onValueModified: root.snapMilliseconds = value }
                        Text { text: "ms"; color: root.textMuted; font.pixelSize: 9 }
                        Text { text: "ZOOM"; color: root.textMuted; font.pixelSize: 9 }
                        Slider { Layout.preferredWidth: 140; from: 16; to: 180; value: root.editorPixelsPerSecond; onMoved: root.editorPixelsPerSecond = value }
                    }
                    SubtitleTimeline {
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.min(320, 90 + Math.max(1, root.appBackend.projectSpeakers.length) * 42)
                        player: editorPlayer
                        pixelsPerSecond: root.editorPixelsPerSecond
                        snapSeconds: root.snapMilliseconds / 1000
                        editable: true
                        onSegmentActivated: function(index) {
                            var segment = root.appBackend.subtitleSegments[index]
                            if (segment) editorPlayer.position = Number(segment.start) * 1000
                        }
                    }
                }

                Rectangle {
                    Layout.preferredWidth: 540; Layout.fillHeight: true; radius: 10; color: root.panel; border.color: root.border
                    ColumnLayout { anchors.fill: parent; anchors.margins: 8; spacing: 7
                        RowLayout { Layout.fillWidth: true
                            PanelTitle { text: "CAPTION TABLE" }
                            Item { Layout.fillWidth: true }
                            Text { text: "TEXT / START / END / SPEAKER / SIZE"; color: root.textMuted; font.family: "Bahnschrift"; font.pixelSize: 8 }
                        }
                        ListView {
                            id: captionTable
                            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 5
                            model: root.appBackend.subtitleSegments
                            currentIndex: root.appBackend.selectedSegmentIndex
                            onCurrentIndexChanged: if (currentIndex >= 0) root.appBackend.selectSegment(currentIndex)
                            delegate: Rectangle {
                                id: captionRow
                                required property int index
                                required property var modelData
                                width: captionTable.width; height: 92; radius: 8
                                color: root.appBackend.selectedSegmentIndex === index ? "#263326" : root.raised
                                border.color: root.appBackend.selectedSegmentIndex === index ? root.acid : root.border
                                MouseArea { anchors.fill: parent; z: -1; onClicked: { root.appBackend.selectSegment(captionRow.index); editorPlayer.position = Number(captionRow.modelData.start) * 1000 } }
                                ColumnLayout { anchors.fill: parent; anchors.margins: 7; spacing: 5
                                    RowLayout { Layout.fillWidth: true; spacing: 5
                                        Text { text: String(captionRow.index + 1).padStart(4, "0"); color: root.textMuted; font.family: "Cascadia Mono"; font.pixelSize: 9 }
                                        TimeField { Layout.preferredWidth: 72; text: Number(captionRow.modelData.start).toFixed(3); onEditingFinished: root.appBackend.updateSegment(captionRow.index, {"start": Number(text)}) }
                                        TimeField { Layout.preferredWidth: 72; text: Number(captionRow.modelData.end).toFixed(3); onEditingFinished: root.appBackend.updateSegment(captionRow.index, {"end": Number(text)}) }
                                        ComboBox {
                                            Layout.preferredWidth: 105
                                            model: root.appBackend.projectSpeakers
                                            textRole: "name"
                                            valueRole: "style"
                                            Component.onCompleted: {
                                                for (var i = 0; i < count; ++i) if (valueAt(i) === captionRow.modelData.speaker) currentIndex = i
                                            }
                                            onActivated: root.appBackend.updateSegment(captionRow.index, {"speaker": currentValue})
                                        }
                                        SpinBox {
                                            Layout.preferredWidth: 82; from: 50; to: 200; stepSize: 5
                                            value: Math.round(Number(captionRow.modelData.subtitle_font_scale || 1) * 100)
                                            onValueModified: root.appBackend.updateSegment(captionRow.index, {"subtitle_font_scale": value / 100})
                                        }
                                        Text { text: "%"; color: root.textMuted; font.pixelSize: 9 }
                                    }
                                    TextField {
                                        Layout.fillWidth: true
                                        text: captionRow.modelData.text
                                        color: root.textPrimary; selectionColor: root.acid; font.family: "Yu Gothic UI"; font.pixelSize: 12
                                        onEditingFinished: root.appBackend.updateSegment(captionRow.index, {"text": text})
                                        background: Rectangle { radius: 6; color: "#101512"; border.color: parent.activeFocus ? root.acid : root.border }
                                    }
                                }
                            }
                            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AlwaysOn }
                        }
                    }
                }
            }
        }
    }

    Shortcut { sequence: StandardKey.Undo; enabled: root.appBackend.projectLoaded; onActivated: root.appBackend.undoSubtitleEdit() }
    Shortcut { sequence: StandardKey.Redo; enabled: root.appBackend.projectLoaded; onActivated: root.appBackend.redoSubtitleEdit() }
    Shortcut { sequence: StandardKey.Save; enabled: root.appBackend.projectLoaded; onActivated: root.appBackend.saveProject() }
    Shortcut { sequence: "Delete"; enabled: editorPopup.opened && root.appBackend.selectedSegmentIndex >= 0; onActivated: root.appBackend.deleteSelectedSegment() }

    Connections {
        target: root.appBackend
        function onSettingsChanged() { root.syncSettings() }
        function onSegmentsChanged() {
            if (captionTable && root.appBackend.selectedSegmentIndex >= 0)
                captionTable.positionViewAtIndex(root.appBackend.selectedSegmentIndex, ListView.Contain)
        }
    }

    Component.onCompleted: root.syncSettings()
    onClosing: {
        root.appBackend.saveProject()
        mainPlayer.stop()
        editorPlayer.stop()
    }
}
