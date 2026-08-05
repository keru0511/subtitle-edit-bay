pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
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
    property var projectSpeakerCache: root.appBackend.projectSpeakers
    property var subtitleWaveformCache: root.appBackend.subtitleWaveforms
    property real editorPositionCache: 0
    property real editorTimelineScrollX: 0
    property real editorCaptionScrollY: 0
    property bool editorMode: false
    property bool settingsExpanded: false
    property string colorTarget: ""
    property int colorTargetIndex: -1
    property bool acceptingSourceDrop: false

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

    function openSpeakerColorPicker(target, index, currentColor) {
        root.colorTarget = target
        root.colorTargetIndex = index
        speakerColorDialog.selectedColor = currentColor || "#FFFFFF"
        speakerColorDialog.open()
    }

    ColorDialog {
        id: speakerColorDialog
        objectName: "speakerColorDialog"
        title: "話者の字幕色を選択"
        onAccepted: {
            var colorValue = selectedColor.toString()
            if (root.colorTarget === "source")
                root.appBackend.updateSpeakerColor(root.colorTargetIndex, colorValue)
            else if (root.colorTarget === "project")
                root.appBackend.updateProjectSpeakerColor(root.colorTargetIndex, colorValue)
            root.colorTarget = ""
            root.colorTargetIndex = -1
        }
        onRejected: {
            root.colorTarget = ""
            root.colorTargetIndex = -1
        }
    }

    function importSourceDrop(drop) {
        root.acceptingSourceDrop = false
        if (!drop.hasUrls)
            return
        root.appBackend.importDroppedSourceFiles(drop.urls)
        drop.acceptProposedAction()
    }

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
        if (deviceCombo.currentText === "cuda" && !root.appBackend.dependencyStatus.cuda)
            return "CUDA版PyTorchが利用できません。setup.batを再実行するか、処理デバイスをCPUへ変更してください"
        if (!root.appBackend.sourceSelection.video)
            return "素材設定で動画を指定してください"
        if (root.appBackend.speakers.length === 0)
            return "素材設定で1つ以上の話者音声を指定してください"
        if (!root.appBackend.sourceSelection.output_dir)
            return "素材設定で出力先フォルダを指定してください"
        if (root.appBackend.projectLoaded)
            return ""
        return ""
    }

    function canSplitSelectedSegment(positionMs) {
        var index = root.appBackend.selectedSegmentIndex
        var segmentCount = root.appBackend.segmentCount
        if (index < 0 || index >= segmentCount)
            return false
        var segment = root.appBackend.segmentAt(index)
        var seconds = Number(positionMs) / 1000
        return seconds > Number(segment.start) + 0.05 && seconds < Number(segment.end) - 0.05
    }

    function workflowStepNumber() {
        if (root.appBackend.running && root.appBackend.activeJob === "render")
            return 4
        if (root.appBackend.projectLoaded)
            return 3
        if (root.appBackend.sourceSelection.video && root.appBackend.sourceSelection.output_dir && root.appBackend.speakers.length > 0 && root.appBackend.dependencyStatus.ready)
            return 2
        return 1
    }

    function openEditorScreen() {
        root.editorPositionCache = mainPlayer.position
        root.editorMode = true
    }

    function closeEditorScreen() {
        mainPlayer.position = root.editorPositionCache
        root.editorMode = false
    }

    function renderFromEditor() {
        root.closeEditorScreen()
        root.appBackend.renderVideo(root.currentSettings())
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
        font.family: "Yu Gothic UI"
        font.pixelSize: 10
        font.weight: Font.Bold
        font.letterSpacing: 1.0
    }

    component SmallButton: Button {
        id: smallControl
        implicitHeight: 32
        contentItem: Text {
            text: smallControl.text
            color: smallControl.enabled ? root.textPrimary : "#59635D"
            font.family: "Yu Gothic UI"
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

    component CompactSpinBox: SpinBox {
        id: compactSpin
        implicitWidth: 106
        implicitHeight: 34
        editable: true
        font.family: "Cascadia Mono"
        font.pixelSize: 11
        contentItem: TextInput {
            z: 1
            text: compactSpin.textFromValue(compactSpin.value, compactSpin.locale)
            color: root.textPrimary
            selectionColor: root.acid
            selectedTextColor: "#10140F"
            horizontalAlignment: Qt.AlignHCenter
            verticalAlignment: Qt.AlignVCenter
            readOnly: !compactSpin.editable
            validator: compactSpin.validator
            inputMethodHints: Qt.ImhFormattedNumbersOnly
            leftPadding: 31
            rightPadding: 31
        }
        up.indicator: Rectangle {
            x: compactSpin.width - width
            width: 30
            height: compactSpin.height
            color: compactSpin.up.pressed ? "#303B35" : "transparent"
            border.color: root.border
            Text { anchors.centerIn: parent; text: "+"; color: root.textPrimary; font.pixelSize: 18 }
        }
        down.indicator: Rectangle {
            width: 30
            height: compactSpin.height
            color: compactSpin.down.pressed ? "#303B35" : "transparent"
            border.color: root.border
            Text { anchors.centerIn: parent; text: "−"; color: root.textPrimary; font.pixelSize: 18 }
        }
        background: Rectangle {
            radius: 6
            color: "#101512"
            border.color: compactSpin.activeFocus ? root.acid : root.border
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
        id: overlayRoot
        property MediaPlayer player
        property var activeSegments: []
        property string activeSignature: ""

        function refreshActiveSegments() {
            var candidates = root.appBackend.activeSubtitleSegments(
                overlayRoot.player ? overlayRoot.player.position / 1000 : 0
            )
            var signature = JSON.stringify(candidates)
            if (signature !== overlayRoot.activeSignature) {
                overlayRoot.activeSignature = signature
                overlayRoot.activeSegments = candidates
            }
        }

        onPlayerChanged: overlayRoot.refreshActiveSegments()
        Connections {
            target: overlayRoot.player
            function onPositionChanged() { overlayRoot.refreshActiveSegments() }
        }
        Connections {
            target: root.appBackend
            function onSegmentsChanged() { overlayRoot.refreshActiveSegments() }
        }
        Repeater {
            model: overlayRoot.activeSegments
            delegate: Text {
                id: overlayCaption
                required property var modelData
                property var segmentData: modelData || ({})
                width: Math.min(implicitWidth + 30, parent.width - 30)
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 26 + Number(segmentData.layout_row || 0) * 54
                text: segmentData.text || ""
                color: root.speakerColor(segmentData.speaker || "")
                font.family: segmentData.subtitle_font_family || "Yu Gothic UI"
                font.pixelSize: Math.max(14, Math.round(22 * Number(segmentData.subtitle_font_scale || 1)))
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
        property alias viewportX: timelineFlick.contentX
        property var visibleSegments: []
        property var visibleRulerTicks: []

        function refreshViewport() {
            if (timelineRoot.pixelsPerSecond <= 0)
                return
            var pixels = timelineRoot.pixelsPerSecond
            var padding = Math.max(2, timelineFlick.width / pixels * 0.25)
            var viewportStart = Math.max(0, timelineFlick.contentX / pixels - padding)
            var viewportEnd = (timelineFlick.contentX + timelineFlick.width) / pixels + padding
            timelineRoot.visibleSegments = root.appBackend.visibleSubtitleSegments(viewportStart, viewportEnd)

            var ticks = []
            var firstTick = Math.max(0, Math.floor(viewportStart / 10) * 10)
            var lastTick = Math.ceil(viewportEnd / 10) * 10
            for (var tick = firstTick; tick <= lastTick; tick += 10)
                ticks.push(tick)
            timelineRoot.visibleRulerTicks = ticks
        }

        function followPlaybackPosition(positionMs) {
            if (timelineRoot.pixelsPerSecond <= 0 || timelineFlick.width <= 0)
                return
            var targetX = Math.max(0, Number(positionMs) / 1000 * timelineRoot.pixelsPerSecond)
            var viewportWidth = timelineFlick.width
            var anchorX = viewportWidth * 0.35
            var currentX = timelineFlick.contentX
            var desiredX = currentX
            if (targetX < currentX || targetX > currentX + viewportWidth)
                desiredX = targetX - anchorX
            else if (timelineRoot.player
                     && timelineRoot.player.playbackState === MediaPlayer.PlayingState
                     && targetX > currentX + anchorX)
                desiredX = targetX - anchorX
            var maximumX = Math.max(0, timelineFlick.contentWidth - viewportWidth)
            desiredX = Math.max(0, Math.min(maximumX, desiredX))
            if (Math.abs(desiredX - currentX) > 0.5)
                timelineFlick.contentX = desiredX
        }

        onPixelsPerSecondChanged: timelineRoot.refreshViewport()
        signal segmentActivated(int index)
        Connections {
            target: root.appBackend
            function onSegmentsChanged() { Qt.callLater(timelineRoot.refreshViewport) }
        }
        Connections {
            target: timelineRoot.player
            function onPositionChanged() {
                if (timelineRoot.player)
                    timelineRoot.followPlaybackPosition(timelineRoot.player.position)
            }
        }

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
            contentHeight: 28 + Math.max(1, root.projectSpeakerCache.length) * timelineRoot.laneHeight
            onContentXChanged: timelineRoot.refreshViewport()
            onWidthChanged: timelineRoot.refreshViewport()
            Component.onCompleted: timelineRoot.refreshViewport()

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
                    model: timelineRoot.visibleRulerTicks
                    delegate: Item {
                        id: rulerTick
                        required property var modelData
                        x: Number(modelData) * timelineRoot.pixelsPerSecond
                        width: 1
                        height: timelineCanvas.height
                        Rectangle { anchors.fill: parent; color: "#26302B" }
                        Text {
                            x: 4
                            y: 3
                            text: root.stamp(rulerTick.modelData)
                            color: root.textMuted
                            font.family: "Cascadia Mono"
                            font.pixelSize: 9
                        }
                    }
                }

                Repeater {
                    model: root.projectSpeakerCache
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
                    model: root.subtitleWaveformCache
                    delegate: Item {
                        id: waveDelegate
                        required property var modelData
                        property int laneIndex: root.laneForStyle(modelData.style)
                        x: Number(modelData.offset_seconds || 0) * timelineRoot.pixelsPerSecond
                        y: 31 + laneIndex * timelineRoot.laneHeight
                        width: Number(modelData.duration_seconds || 0) * timelineRoot.pixelsPerSecond
                        height: timelineRoot.laneHeight - 6
                        opacity: 0.3
                        Canvas {
                            anchors.fill: parent
                            property var peaks: waveDelegate.modelData.peaks || []
                            property color waveformColor: waveDelegate.modelData.color || root.amber
                            onPeaksChanged: requestPaint()
                            onWaveformColorChanged: requestPaint()
                            onWidthChanged: requestPaint()
                            onHeightChanged: requestPaint()
                            onPaint: {
                                var context = getContext("2d")
                                context.clearRect(0, 0, width, height)
                                if (peaks.length === 0)
                                    return
                                context.fillStyle = waveformColor
                                var step = width / peaks.length
                                var barWidth = Math.max(1, step - 0.5)
                                for (var i = 0; i < peaks.length; ++i) {
                                    var barHeight = Math.max(1, Number(peaks[i]) * height)
                                    context.fillRect(
                                        i * step,
                                        (height - barHeight) / 2,
                                        barWidth,
                                        barHeight
                                    )
                                }
                            }
                        }
                    }
                }

                Repeater {
                    model: timelineRoot.visibleSegments
                    delegate: Rectangle {
                        id: captionClip
                        required property var modelData
                        property int sourceIndex: modelData ? Number(modelData.sourceIndex) : -1
                        property var segment: modelData && modelData.segment ? modelData.segment : ({})
                        property real originalX: 0
                        property real originalWidth: 0
                        property real pointerStart: 0
                        visible: sourceIndex >= 0 && segment.start !== undefined && segment.end !== undefined
                        x: Number(segment.start || 0) * timelineRoot.pixelsPerSecond
                        y: 31 + root.laneForStyle(segment.speaker || "") * timelineRoot.laneHeight
                        width: Math.max(10, (Number(segment.end || 0) - Number(segment.start || 0)) * timelineRoot.pixelsPerSecond)
                        height: timelineRoot.laneHeight - 7
                        radius: 6
                        color: root.speakerColor(segment.speaker || "")
                        opacity: root.appBackend.selectedSegmentIndex === sourceIndex ? 1 : 0.78
                        border.color: root.appBackend.selectedSegmentIndex === sourceIndex ? root.textPrimary : "#66101010"
                        border.width: root.appBackend.selectedSegmentIndex === sourceIndex ? 2 : 1

                        Text {
                            anchors.fill: parent
                            anchors.leftMargin: 8
                            anchors.rightMargin: 8
                            text: captionClip.segment.text || ""
                            color: "#10140F"
                            font.family: captionClip.segment.subtitle_font_family || "Yu Gothic UI"
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
                                root.appBackend.selectSegment(captionClip.sourceIndex)
                                timelineRoot.segmentActivated(captionClip.sourceIndex)
                            }
                            onReleased: root.appBackend.moveSegment(
                                captionClip.sourceIndex,
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
                            visible: timelineRoot.editable && root.appBackend.selectedSegmentIndex === captionClip.sourceIndex
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
                                    captionClip.sourceIndex,
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
                            visible: timelineRoot.editable && root.appBackend.selectedSegmentIndex === captionClip.sourceIndex
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
                                    captionClip.sourceIndex,
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
            Connections {
                target: root
                function onSubtitleSegmentCacheChanged() { Qt.callLater(timelineRoot.refreshViewport) }
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
                model: root.projectSpeakerCache
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
        height: root.editorMode ? 0 : 62
        visible: !root.editorMode
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
                Text { text: "素材  /  文字起こし  /  字幕編集  /  書き出し"; color: root.acid; font.family: "Yu Gothic UI"; font.pixelSize: 9; font.letterSpacing: 1.0 }
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
                    text: root.appBackend.projectDirty ? "● 保存待ち" : (root.appBackend.projectLoaded ? "✓ 保存済み" : "文字起こし後に自動作成")
                    color: root.appBackend.projectDirty ? root.amber : root.textMuted
                    font.family: "Yu Gothic UI"; font.pixelSize: 9
                }
            }
            SmallButton { objectName: "projectOpenButton"; text: "プロジェクトを開く"; enabled: !root.appBackend.running; onClicked: root.appBackend.browseProjectFile() }
            SmallButton { objectName: "sourceSetupButton"; text: "素材設定"; enabled: !root.appBackend.running; onClicked: sourcePopup.open() }
            Rectangle { Layout.preferredWidth: 9; Layout.preferredHeight: 9; radius: 5; color: root.appBackend.running ? root.amber : root.acid }
        }
    }

    RowLayout {
        objectName: "mainWorkspace"
        visible: !root.editorMode
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
                PanelTitle { text: "素材と話者" }
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: 70; radius: 9; color: root.raised; border.color: root.border
                    Column { anchors.fill: parent; anchors.margins: 10; spacing: 3
                        Text { text: "動画"; color: root.textMuted; font.pixelSize: 9; font.family: "Yu Gothic UI" }
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
                            Button {
                                id: sourceSpeakerColorButton
                                objectName: "sourceSpeakerColorButton"
                                Layout.preferredWidth: 28; Layout.preferredHeight: 30
                                enabled: !root.appBackend.running
                                onClicked: root.openSpeakerColorPicker("source", speakerSourceDelegate.index, speakerSourceDelegate.modelData.color)
                                contentItem: Rectangle { radius: 5; color: speakerSourceDelegate.modelData.color; border.color: root.textPrimary; border.width: 1 }
                                background: Rectangle { radius: 6; color: "transparent"; border.color: sourceSpeakerColorButton.hovered ? root.acid : root.border }
                                ToolTip.visible: hovered
                                ToolTip.text: "字幕色を変更"
                            }
                            ColumnLayout { Layout.fillWidth: true; spacing: 0
                                Text { Layout.fillWidth: true; text: speakerSourceDelegate.modelData.name; color: root.textPrimary; font.pixelSize: 11; font.family: "Yu Gothic UI"; elide: Text.ElideRight }
                                Text { Layout.fillWidth: true; text: speakerSourceDelegate.modelData.file_name; color: root.textMuted; font.pixelSize: 9; font.family: "Bahnschrift"; elide: Text.ElideMiddle }
                            }
                            ToolButton { text: "×"; enabled: !root.appBackend.running; onClicked: root.appBackend.removeAudioFile(speakerSourceDelegate.index) }
                        }
                    }
                }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }
                PanelTitle { text: "音声同期" }
                ComboBox { id: referenceCombo; Layout.fillWidth: true; model: root.appBackend.speakers; textRole: "file_name"; valueRole: "path" }
                ComboBox { id: trackCombo; Layout.fillWidth: true; model: root.appBackend.audioTracks; textRole: "label"; valueRole: "selector" }
                RowLayout { Layout.fillWidth: true
                    TimeField { id: manualOffsetField; Layout.fillWidth: true; text: "0.000"; validator: DoubleValidator { bottom: -120; top: 120; decimals: 3 } }
                    SmallButton {
                        text: root.appBackend.alignmentBusy ? "解析中" : "同期解析"
                        enabled: !root.appBackend.running && !root.appBackend.alignmentBusy && root.appBackend.speakers.length > 0 && root.appBackend.sourceSelection.video
                        onClicked: root.appBackend.analyzeAlignment(referenceCombo.currentValue || "", trackCombo.currentValue || "", Number(manualOffsetField.text || 0))
                    }
                }
                Text { Layout.fillWidth: true; text: root.appBackend.alignmentResult.status + (root.appBackend.alignmentResult.offset !== undefined ? "  " + Number(root.appBackend.alignmentResult.offset).toFixed(3) + "s" : ""); color: root.textMuted; font.pixelSize: 10; font.family: "Yu Gothic UI" }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 10

            Rectangle {
                objectName: "workflowStepper"
                Layout.fillWidth: true
                Layout.preferredHeight: 68
                radius: 12; color: root.panel; border.color: root.border
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 8
                    Repeater {
                        model: ["素材", "文字起こし", "字幕編集", "書き出し"]
                        delegate: ColumnLayout {
                            id: stepDelegate
                            required property int index
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: 3
                            Rectangle { Layout.alignment: Qt.AlignHCenter; Layout.preferredWidth: 28; Layout.preferredHeight: 28; radius: 14; color: root.workflowStepNumber() >= stepDelegate.index + 1 ? root.acid : "#27312C"; border.color: root.workflowStepNumber() === stepDelegate.index + 1 ? root.textPrimary : root.border; Text { anchors.centerIn: parent; text: stepDelegate.index + 1; color: root.workflowStepNumber() >= stepDelegate.index + 1 ? "#10140F" : root.textMuted; font.weight: Font.Bold } }
                            Text { Layout.fillWidth: true; text: stepDelegate.modelData; color: root.workflowStepNumber() === stepDelegate.index + 1 ? root.textPrimary : root.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 10; horizontalAlignment: Text.AlignHCenter }
                        }
                    }
                }
            }
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
                    onPositionChanged: if (!mainSeek.pressed) mainSeek.value = mainPlayer.position
                    onDurationChanged: mainSeek.to = Math.max(1, mainPlayer.duration)
                }
                VideoOutput { id: mainVideo; anchors.fill: parent; anchors.bottomMargin: 58; fillMode: VideoOutput.PreserveAspectFit }
                SubtitleOverlay { anchors.fill: mainVideo; player: mainPlayer }
                ColumnLayout {
                    anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                    anchors.margins: 12; spacing: 2
                    Slider { id: mainSeek; Layout.fillWidth: true; from: 0; to: 1; onMoved: mainPlayer.position = value }
                    RowLayout { Layout.fillWidth: true
                        ToolButton { text: mainPlayer.playbackState === MediaPlayer.PlayingState ? "Ⅱ" : "▶"; onClicked: mainPlayer.playbackState === MediaPlayer.PlayingState ? mainPlayer.pause() : mainPlayer.play() }
                        Text { Layout.fillWidth: true; text: root.appBackend.sourceSelection.video ? root.appBackend.sourceSelection.video.split(/[\\/]/).pop() : "動画未選択"; color: root.textPrimary; font.pixelSize: 11; font.family: "Yu Gothic UI"; elide: Text.ElideMiddle }
                        Text { text: root.stamp(mainPlayer.position / 1000) + " / " + root.stamp(mainPlayer.duration / 1000); color: root.textMuted; font.pixelSize: 10; font.family: "Cascadia Mono" }
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
                RowLayout {
                    Layout.fillWidth: true; Layout.preferredHeight: 56; Layout.maximumHeight: 56; Layout.margins: 12
                    Text { Layout.fillWidth: true; text: "次の操作"; color: root.textPrimary; font.family: "Yu Gothic UI"; font.pixelSize: 15; font.weight: Font.Bold }
                    SmallButton { objectName: "settingsToggleButton"; text: root.settingsExpanded ? "設定を閉じる" : "詳細設定"; onClicked: root.settingsExpanded = !root.settingsExpanded }
                }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }
                ScrollView { objectName: "advancedSettingsPanel"; visible: root.settingsExpanded; Layout.fillWidth: true; Layout.fillHeight: root.settingsExpanded; Layout.preferredHeight: root.settingsExpanded ? 320 : 0; Layout.maximumHeight: root.settingsExpanded ? 420 : 0; clip: true
                    ColumnLayout { width: 286; x: 16; spacing: 10
                        Item { Layout.preferredHeight: 2 }
                        PanelTitle { text: "文字起こしエンジン" }
                        RowLayout { Layout.fillWidth: true; Text { text: "処理デバイス"; color: root.textPrimary; Layout.fillWidth: true } ComboBox { id: deviceCombo; model: ["cuda", "cpu"]; Layout.preferredWidth: 110 } }
                        RowLayout { Layout.fillWidth: true; Text { text: "Whisperモデル"; color: root.textPrimary; Layout.fillWidth: true } ComboBox { id: modelCombo; model: ["large-v3", "medium", "small"]; Layout.preferredWidth: 130 } }
                        RowLayout { Layout.fillWidth: true; Text { text: "CPU並列数"; color: root.textPrimary; Layout.fillWidth: true } SpinBox { id: workersSpin; from: 1; to: 16; value: 4 } }
                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }
                        PanelTitle { text: "字幕" }
                        RowLayout { Layout.fillWidth: true; Text { text: "基準文字サイズ"; color: root.textPrimary; Layout.fillWidth: true } SpinBox { id: fontSizeSpin; objectName: "fontSizeSpin"; from: 32; to: 96; value: 50 } }
                        RowLayout { Layout.fillWidth: true; Text { text: "音量サイズ比率"; color: root.textPrimary; Layout.fillWidth: true } SpinBox { id: volumeScaleSpin; objectName: "volumeScaleSpin"; from: 0; to: 50; value: 20 } Text { text: "%"; color: root.textMuted } }
                        RowLayout { Layout.fillWidth: true; Text { text: "単語間隔"; color: root.textPrimary; Layout.fillWidth: true } TimeField { id: gapField; Layout.preferredWidth: 76; text: "0.10" } }
                        RowLayout { Layout.fillWidth: true; Text { text: "終了余白"; color: root.textPrimary; Layout.fillWidth: true } TimeField { id: paddingField; Layout.preferredWidth: 76; text: "0.08" } }
                        RowLayout { Layout.fillWidth: true; Text { text: "最短表示時間"; color: root.textPrimary; Layout.fillWidth: true } TimeField { id: minDurationField; Layout.preferredWidth: 76; text: "0.35" } }
                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }
                        PanelTitle { text: "動画・音声" }
                        RowLayout { Layout.fillWidth: true; Text { text: "動画コーデック"; color: root.textPrimary; Layout.fillWidth: true } ComboBox { id: codecCombo; model: ["h264_nvenc", "libx264"]; Layout.preferredWidth: 132 } }
                        RowLayout { Layout.fillWidth: true; Text { text: "画質"; color: root.textPrimary; Layout.fillWidth: true } SpinBox { id: qualitySpin; from: 14; to: 28; value: 18 } }
                        Switch { id: normalizeSwitch; text: "音量を正規化"; checked: true }
                        RowLayout { Layout.fillWidth: true; Text { text: "目標LUFS"; color: root.textPrimary; Layout.fillWidth: true } TimeField { id: lufsField; Layout.preferredWidth: 76; text: "-16"; validator: DoubleValidator { bottom: -30; top: -5 } } }
                        Switch { id: silenceSwitch; text: "無音部分をカット" }
                        RowLayout { Layout.fillWidth: true; enabled: silenceSwitch.checked; opacity: enabled ? 1 : 0.4; Text { text: "最短無音時間"; color: root.textPrimary; Layout.fillWidth: true } TimeField { id: silenceField; Layout.preferredWidth: 76; text: "1.2" } }
                        RowLayout { Layout.fillWidth: true; enabled: silenceSwitch.checked; opacity: enabled ? 1 : 0.4; Text { text: "発話余白"; color: root.textPrimary; Layout.fillWidth: true } TimeField { id: speechPaddingField; Layout.preferredWidth: 76; text: "0.25" } }
                        Item { Layout.preferredHeight: 6 }
                    }
                }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }
                ColumnLayout { objectName: "workflowActions"; Layout.fillWidth: true; Layout.fillHeight: false; Layout.margins: 12; spacing: 7
                    Text { Layout.fillWidth: true; text: root.appBackend.projectLoaded ? "字幕を編集するか、このまま動画へ焼き付けられます" : "素材の準備ができたら文字起こしを開始します"; color: root.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 10; wrapMode: Text.Wrap }
                    Button {
                        id: transcribeButton
                        objectName: "transcribeButton"
                        Layout.fillWidth: true; Layout.preferredHeight: 46; visible: !root.appBackend.projectLoaded || root.appBackend.activeJob === "transcribe"
                        enabled: !root.appBackend.running && root.appBackend.sourceSelection.video && root.appBackend.sourceSelection.output_dir && root.appBackend.speakers.length > 0 && root.appBackend.dependencyStatus.ready && (deviceCombo.currentText !== "cuda" || root.appBackend.dependencyStatus.cuda) && !root.appBackend.projectLoaded
                        text: root.appBackend.activeJob === "transcribe" ? "文字起こし中..." : "文字起こしを開始"
                        onClicked: root.appBackend.startTranscription(root.currentSettings())
                        contentItem: Text { text: transcribeButton.text; color: transcribeButton.enabled ? "#10140F" : "#68716B"; font.family: "Yu Gothic UI"; font.pixelSize: 12; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                        background: Rectangle { radius: 8; color: transcribeButton.enabled ? root.acid : "#252C28" }
                    }
                    Button {
                        id: editButton
                        objectName: "editSubtitlesButton"
                        Layout.fillWidth: true; Layout.preferredHeight: 46; visible: root.appBackend.projectLoaded
                        enabled: root.appBackend.projectLoaded && !root.appBackend.running
                        text: "字幕を編集する"
                        onClicked: root.openEditorScreen()
                        contentItem: Text { text: editButton.text; color: editButton.enabled ? "#10140F" : "#68716B"; font.family: "Yu Gothic UI"; font.pixelSize: 12; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                        background: Rectangle { radius: 8; color: editButton.enabled ? root.acid : "#252C28" }
                    }
                    Button {
                        id: renderButton
                        objectName: "renderVideoButton"
                        Layout.fillWidth: true; Layout.preferredHeight: 46; visible: root.appBackend.projectLoaded || root.appBackend.activeJob === "render"
                        enabled: root.appBackend.projectLoaded && !root.appBackend.running
                        text: root.appBackend.activeJob === "render" ? "字幕を焼き付け中..." : "字幕を焼き付けて動画を書き出す"
                        onClicked: root.appBackend.renderVideo(root.currentSettings())
                        contentItem: Text { text: renderButton.text; color: renderButton.enabled ? root.textPrimary : "#68716B"; font.family: "Yu Gothic UI"; font.pixelSize: 12; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                        background: Rectangle { radius: 8; color: renderButton.enabled ? root.raised : "#252C28"; border.color: renderButton.enabled ? root.border : "#252C28" }
                    }
                    Text { objectName: "workflowBlockReason"; Layout.fillWidth: true; text: root.transcriptionBlockReason(); visible: text.length > 0; color: root.amber; font.family: "Yu Gothic UI"; font.pixelSize: 9; wrapMode: Text.Wrap }
                    RowLayout { Layout.fillWidth: true
                        SmallButton { objectName: "saveSettingsButton"; Layout.fillWidth: true; text: root.appBackend.running ? "停止" : "設定を保存"; onClicked: root.appBackend.running ? root.appBackend.cancelProcessing() : root.appBackend.saveSettings(root.currentSettings()) }
                        SmallButton { objectName: "outputFolderButton"; Layout.fillWidth: true; text: "出力先を開く"; enabled: Boolean(root.appBackend.sourceSelection.output_dir); onClicked: root.appBackend.openOutputFolder() }
                    }
                }
                Item { visible: !root.settingsExpanded; Layout.fillHeight: true; Layout.preferredHeight: 1; Layout.maximumHeight: 100000 }
            }
        }
    }

    Popup {
        objectName: "sourcePopup"
        id: sourcePopup
        anchors.centerIn: Overlay.overlay
        width: 620; height: 520; modal: true; focus: true; closePolicy: Popup.CloseOnEscape
        background: Rectangle { radius: 14; color: root.panel; border.color: root.border }
        ColumnLayout { anchors.fill: parent; anchors.margins: 18; spacing: 12
            RowLayout { Layout.fillWidth: true; Text { text: "素材設定"; color: root.textPrimary; font.family: "Yu Gothic UI"; font.pixelSize: 17; font.weight: Font.Bold; Layout.fillWidth: true } ToolButton { text: "×"; onClicked: sourcePopup.close() } }
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
                    SmallButton { text: "再確認"; enabled: !root.appBackend.running; onClicked: root.appBackend.refreshDependencies() }
                }
            }
            Rectangle {
                objectName: "sourcePopupDropTarget"
                Layout.fillWidth: true
                Layout.preferredHeight: 68
                radius: 9
                color: sourcePopupDropArea.containsDrag ? "#263326" : root.raised
                border.color: sourcePopupDropArea.containsDrag ? root.acid : root.border
                border.width: sourcePopupDropArea.containsDrag ? 2 : 1
                Column {
                    anchors.centerIn: parent
                    spacing: 3
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: "動画・話者音声をここにドロップ"; color: root.textPrimary; font.family: "Yu Gothic UI"; font.pixelSize: 11; font.weight: Font.DemiBold }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: "動画1件と複数の音声を自動判別します"; color: root.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 9 }
                }
                DropArea {
                    id: sourcePopupDropArea
                    anchors.fill: parent
                    enabled: !root.appBackend.running
                    onEntered: function(drag) { drag.accepted = drag.hasUrls }
                    onDropped: function(drop) { root.importSourceDrop(drop) }
                }
            }
            PanelTitle { text: "動画" }
            RowLayout { Layout.fillWidth: true; Text { Layout.fillWidth: true; text: root.appBackend.sourceSelection.video || "未選択"; color: root.textMuted; elide: Text.ElideMiddle } SmallButton { text: "選択"; enabled: !root.appBackend.running; onClicked: root.appBackend.browseVideoFile() } }
            PanelTitle { text: "話者音声" }
            ListView {
                id: sourceAudioList
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 5; model: root.appBackend.speakers
                delegate: Rectangle { id: sourceAudioDelegate; required property int index; required property var modelData; width: sourceAudioList.width; height: 38; radius: 7; color: root.raised
                    RowLayout { anchors.fill: parent; anchors.margins: 7; Rectangle { Layout.preferredWidth: 7; Layout.preferredHeight: 22; radius: 3; color: sourceAudioDelegate.modelData.color } Text { Layout.fillWidth: true; text: sourceAudioDelegate.modelData.file_name; color: root.textPrimary; elide: Text.ElideMiddle } ToolButton { text: "×"; enabled: !root.appBackend.running; onClicked: root.appBackend.removeAudioFile(sourceAudioDelegate.index) } }
                }
            }
            RowLayout { Layout.fillWidth: true; SmallButton { text: "音声を追加"; enabled: !root.appBackend.running; onClicked: root.appBackend.browseAudioFiles() } SmallButton { text: "クリア"; enabled: !root.appBackend.running; onClicked: root.appBackend.clearAudioFiles() } Item { Layout.fillWidth: true } }
            PanelTitle { text: "出力先フォルダ" }
            RowLayout { Layout.fillWidth: true; Text { Layout.fillWidth: true; text: root.appBackend.sourceSelection.output_dir || "未選択"; color: root.textMuted; elide: Text.ElideMiddle } SmallButton { text: "選択"; enabled: !root.appBackend.running; onClicked: root.appBackend.browseOutputDirectory() } }
            RowLayout { Layout.fillWidth: true; Item { Layout.fillWidth: true } Button { objectName: "sourceDoneButton"; text: "完了"; onClicked: sourcePopup.close() } }
        }
    }

    Rectangle {
        id: editorPage
        objectName: "editorPage"
        anchors.fill: parent
        visible: root.editorMode
        z: 100
        color: "#0D1210"
        border.color: "#46564E"
        focus: visible
        Keys.onEscapePressed: root.closeEditorScreen()
        onVisibleChanged: if (visible) forceActiveFocus()

        Loader {
            id: editorLoader
            anchors.fill: parent
            active: root.editorMode
            sourceComponent: editorContentComponent
        }

        Component {
            id: editorContentComponent
            Item {
                MediaPlayer {
                    id: editorPlayer
                    videoOutput: editorVideo
                    audioOutput: AudioOutput { volume: 0.75 }
                    source: root.appBackend.previewUrl
                    Component.onCompleted: position = root.editorPositionCache
                    onPositionChanged: {
                        root.editorPositionCache = editorPlayer.position
                        if (!editorSeek.pressed)
                            editorSeek.value = editorPlayer.position
                        root.appBackend.selectSegmentAtTime(editorPlayer.position / 1000)
                    }
                    onDurationChanged: editorSeek.to = Math.max(1, editorPlayer.duration)
                }

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 0
            RowLayout {
                Layout.fillWidth: true; Layout.preferredHeight: 58; Layout.leftMargin: 14; Layout.rightMargin: 10; spacing: 8
                Text { text: "字幕編集"; color: root.textPrimary; font.family: "Yu Gothic UI"; font.pixelSize: 17; font.weight: Font.Bold; font.letterSpacing: 1.0 }
                Text { text: root.appBackend.projectDirty ? "● 編集あり" : "✓ 保存済み"; color: root.appBackend.projectDirty ? root.amber : root.acid; font.family: "Yu Gothic UI"; font.pixelSize: 9 }
                Text { objectName: "editorStatusText"; Layout.fillWidth: true; Layout.minimumWidth: 80; text: root.appBackend.stage + " · " + root.appBackend.status; color: root.appBackend.stage === "ERROR" ? root.danger : ((root.appBackend.stage === "CHECK" || root.appBackend.stage === "BUSY") ? root.amber : root.textMuted); font.family: "Yu Gothic UI"; font.pixelSize: 9; horizontalAlignment: Text.AlignRight; elide: Text.ElideRight }
                SmallButton { objectName: "undoCaptionButton"; text: "元に戻す"; enabled: root.appBackend.canUndo; onClicked: root.appBackend.undoSubtitleEdit() }
                SmallButton { objectName: "redoCaptionButton"; text: "やり直す"; enabled: root.appBackend.canRedo; onClicked: root.appBackend.redoSubtitleEdit() }
                SmallButton { objectName: "addCaptionButton"; text: "+ 字幕追加"; onClicked: root.appBackend.addSegment(editorPlayer.position / 1000) }
                SmallButton { objectName: "splitCaptionButton"; text: "分割"; enabled: root.canSplitSelectedSegment(editorPlayer.position); onClicked: root.appBackend.splitSelectedSegment(editorPlayer.position / 1000) }
                SmallButton { objectName: "deleteCaptionButton"; text: "削除"; enabled: root.appBackend.selectedSegmentIndex >= 0; onClicked: root.appBackend.deleteSelectedSegment() }
                SmallButton { objectName: "saveProjectButton"; text: "保存"; onClicked: root.appBackend.saveProject() }
                SmallButton { objectName: "buildAssButton"; text: "ASSを更新"; onClicked: root.appBackend.buildSubtitlePreview(root.currentSettings()) }
                Button {
                    id: editorRenderButton
                    objectName: "editorRenderButton"
                    implicitHeight: 34
                    text: root.appBackend.activeJob === "render" ? "焼き付け中..." : "字幕を焼き付ける"
                    enabled: root.appBackend.projectLoaded && !root.appBackend.running
                    onClicked: root.renderFromEditor()
                    contentItem: Text { text: editorRenderButton.text; color: editorRenderButton.enabled ? "#10140F" : "#68716B"; font.family: "Yu Gothic UI"; font.pixelSize: 10; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    background: Rectangle { radius: 7; color: editorRenderButton.enabled ? root.acid : "#252C28" }
                }
                SmallButton { objectName: "editorBackButton"; text: "メインへ戻る"; onClicked: root.closeEditorScreen() }
            }
            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }

            RowLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.margins: 10; spacing: 10
                ColumnLayout {
                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 760; spacing: 8
                    Rectangle {
                        Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 220; radius: 10; color: "#060806"; border.color: root.border; clip: true
                        VideoOutput { id: editorVideo; anchors.fill: parent; anchors.bottomMargin: 54; fillMode: VideoOutput.PreserveAspectFit }
                        SubtitleOverlay { id: editorOverlay; anchors.fill: editorVideo; player: editorPlayer }
                        ColumnLayout { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 8; spacing: 1
                            Slider { id: editorSeek; Layout.fillWidth: true; from: 0; to: 1; onMoved: editorPlayer.position = value }
                            RowLayout { Layout.fillWidth: true
                                ToolButton { text: editorPlayer.playbackState === MediaPlayer.PlayingState ? "Ⅱ" : "▶"; onClicked: editorPlayer.playbackState === MediaPlayer.PlayingState ? editorPlayer.pause() : editorPlayer.play() }
                                Text { Layout.fillWidth: true; text: root.stamp(editorPlayer.position / 1000); color: root.textPrimary; font.family: "Cascadia Mono"; font.pixelSize: 11 }
                                Text { text: editorOverlay.activeSegments.length + "件表示中"; color: root.textMuted; font.pixelSize: 10 }
                            }
                        }
                    }
                    RowLayout { Layout.fillWidth: true
                        PanelTitle { text: "タイムライン" }
                        Item { Layout.fillWidth: true }
                        Text { text: "スナップ"; color: root.textMuted; font.pixelSize: 9 }
                        SpinBox { id: snapSpin; from: 0; to: 1000; stepSize: 10; value: root.snapMilliseconds; editable: true; onValueModified: root.snapMilliseconds = value }
                        Text { text: "ms"; color: root.textMuted; font.pixelSize: 9 }
                        Text { text: "表示倍率"; color: root.textMuted; font.pixelSize: 9 }
                        Slider { Layout.preferredWidth: 140; from: 16; to: 180; value: root.editorPixelsPerSecond; onMoved: root.editorPixelsPerSecond = value }
                    }
                    SubtitleTimeline {
                        objectName: "editorTimeline"
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.min(320, 90 + Math.max(1, root.projectSpeakerCache.length) * 42)
                        player: editorPlayer
                        pixelsPerSecond: root.editorPixelsPerSecond
                        snapSeconds: root.snapMilliseconds / 1000
                        editable: true
                        Component.onCompleted: Qt.callLater(function() { viewportX = root.editorTimelineScrollX })
                        onViewportXChanged: root.editorTimelineScrollX = viewportX
                        onSegmentActivated: function(index) {
                            var segment = root.appBackend.segmentAt(index)
                            if (segment) editorPlayer.position = Number(segment.start) * 1000
                        }
                    }
                }

                Rectangle {
                    Layout.preferredWidth: 620; Layout.fillHeight: true; radius: 10; color: root.panel; border.color: root.border
                    ColumnLayout { anchors.fill: parent; anchors.margins: 8; spacing: 7
                        RowLayout { Layout.fillWidth: true
                            PanelTitle { text: "話者ごとの字幕色" }
                            Item { Layout.fillWidth: true }
                            Text { text: "色を押して変更"; color: root.textMuted; font.pixelSize: 8 }
                        }
                        ListView {
                            id: projectSpeakerColorList
                            objectName: "projectSpeakerColorList"
                            Layout.fillWidth: true; Layout.preferredHeight: 36
                            orientation: ListView.Horizontal; spacing: 6; clip: true
                            model: root.projectSpeakerCache
                            delegate: Button {
                                id: projectSpeakerColorButton
                                required property int index
                                required property var modelData
                                width: 128; height: 34
                                enabled: !root.appBackend.running
                                onClicked: root.openSpeakerColorPicker("project", index, modelData.color)
                                contentItem: Row {
                                    spacing: 6
                                    Rectangle { width: 20; height: 20; radius: 5; color: projectSpeakerColorButton.modelData.color; border.color: root.textPrimary }
                                    Text { width: 94; text: projectSpeakerColorButton.modelData.name; color: root.textPrimary; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter }
                                }
                                background: Rectangle { radius: 7; color: projectSpeakerColorButton.hovered ? "#27312C" : root.raised; border.color: projectSpeakerColorButton.hovered ? root.acid : root.border }
                                ToolTip.visible: hovered
                                ToolTip.text: modelData.name + " の字幕色を変更"
                            }
                            ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
                        }
                        RowLayout { Layout.fillWidth: true
                            PanelTitle { text: "字幕一覧" }
                            Item { Layout.fillWidth: true }
                            Text { text: "開始 / 終了 / 話者 / フォント / サイズ"; color: root.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 8 }
                        }
                        ListView {
                            id: captionTable
                            objectName: "captionTable"
                            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 5
                            function revealSelectedCaption() {
                                var selectedIndex = root.appBackend.selectedSegmentIndex
                                if (selectedIndex >= 0)
                                    positionViewAtIndex(selectedIndex, ListView.Contain)
                            }
                            model: root.appBackend.subtitleModel
                            currentIndex: root.appBackend.selectedSegmentIndex
                            Component.onCompleted: Qt.callLater(function() { contentY = root.editorCaptionScrollY })
                            onContentYChanged: root.editorCaptionScrollY = contentY
                            onCurrentIndexChanged: if (currentIndex >= 0) root.appBackend.selectSegment(currentIndex)
                            delegate: Rectangle {
                                id: captionRow
                                required property int index
                                required property string segmentId
                                required property real start
                                required property real end
                                required property string text
                                required property string speaker
                                required property int layoutRow
                                required property real subtitleFontScale
                                required property string subtitleFontFamily
                                width: captionTable.width; height: 92; radius: 8
                                color: root.appBackend.selectedSegmentIndex === index ? "#263326" : root.raised
                                border.color: root.appBackend.selectedSegmentIndex === index ? root.acid : root.border
                                MouseArea { anchors.fill: parent; z: -1; onClicked: { root.appBackend.selectSegment(captionRow.index); editorPlayer.position = captionRow.start * 1000 } }
                                ColumnLayout { anchors.fill: parent; anchors.margins: 7; spacing: 5
                                    RowLayout { Layout.fillWidth: true; spacing: 5
                                        Text { text: String(captionRow.index + 1).padStart(4, "0"); color: root.textMuted; font.family: "Cascadia Mono"; font.pixelSize: 9 }
                                        TimeField { Layout.preferredWidth: 72; text: captionRow.start.toFixed(3); onEditingFinished: root.appBackend.updateSegment(captionRow.index, {"start": Number(text)}) }
                                        TimeField { Layout.preferredWidth: 72; text: captionRow.end.toFixed(3); onEditingFinished: root.appBackend.updateSegment(captionRow.index, {"end": Number(text)}) }
                                        ComboBox {
                                            Layout.preferredWidth: 105
                                            model: root.projectSpeakerCache
                                            textRole: "name"
                                            valueRole: "style"
                                            Component.onCompleted: {
                                                for (var i = 0; i < count; ++i) if (valueAt(i) === captionRow.speaker) currentIndex = i
                                            }
                                            onActivated: root.appBackend.updateSegment(captionRow.index, {"speaker": currentValue})
                                        }
                                        ComboBox {
                                            id: captionFontCombo
                                            objectName: "captionFontCombo"
                                            Layout.preferredWidth: 130
                                            model: root.appBackend.fontChoices
                                            textRole: "label"
                                            valueRole: "family"
                                            function syncCurrentFont() {
                                                for (var i = 0; i < count; ++i) {
                                                    if (valueAt(i) === captionRow.subtitleFontFamily) {
                                                        currentIndex = i
                                                        return
                                                    }
                                                }
                                                currentIndex = 0
                                            }
                                            Component.onCompleted: syncCurrentFont()
                                            Connections {
                                                target: captionRow
                                                function onSubtitleFontFamilyChanged() { captionFontCombo.syncCurrentFont() }
                                            }
                                            onActivated: root.appBackend.updateSegment(captionRow.index, {"subtitle_font_family": currentValue})
                                        }
                                        CompactSpinBox {
                                            objectName: "captionSizeSpin"
                                            Layout.preferredWidth: 106; from: 50; to: 200; stepSize: 5
                                            value: Math.round(captionRow.subtitleFontScale * 100)
                                            onValueModified: root.appBackend.updateSegment(captionRow.index, {"subtitle_font_scale": value / 100})
                                        }
                                        Text { text: "%"; color: root.textMuted; font.pixelSize: 9 }
                                    }
                                    TextField {
                                        Layout.fillWidth: true
                                        text: captionRow.text
                                        color: root.textPrimary; selectionColor: root.acid; font.family: captionRow.subtitleFontFamily || "Yu Gothic UI"; font.pixelSize: 12
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
                Connections {
                    target: root.appBackend
                    function onSegmentsChanged() {
                        Qt.callLater(function() { captionTable.revealSelectedCaption() })
                    }
                    function onSelectionChanged() {
                        Qt.callLater(function() { captionTable.revealSelectedCaption() })
                    }
                }
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: 22
        z: 999
        visible: root.acceptingSourceDrop
        radius: 16
        color: "#E6121715"
        border.color: root.acid
        border.width: 3
        Column {
            anchors.centerIn: parent
            spacing: 8
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: "素材をドロップして追加"; color: root.acid; font.family: "Yu Gothic UI"; font.pixelSize: 24; font.weight: Font.Bold }
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: "動画または話者音声"; color: root.textPrimary; font.family: "Yu Gothic UI"; font.pixelSize: 12 }
        }
    }
    DropArea {
        id: globalSourceDropArea
        objectName: "globalSourceDropArea"
        anchors.fill: parent
        z: 1000
        enabled: !root.appBackend.running
        onEntered: function(drag) {
            root.acceptingSourceDrop = drag.hasUrls
            drag.accepted = drag.hasUrls
        }
        onExited: root.acceptingSourceDrop = false
        onDropped: function(drop) { root.importSourceDrop(drop) }
    }

    Shortcut { sequence: StandardKey.Undo; enabled: root.editorMode; onActivated: root.appBackend.undoSubtitleEdit() }
    Shortcut { sequence: StandardKey.Redo; enabled: root.editorMode; onActivated: root.appBackend.redoSubtitleEdit() }
    Shortcut { sequence: StandardKey.Save; enabled: root.editorMode; onActivated: root.appBackend.saveProject() }
    Shortcut { sequence: "Delete"; enabled: root.editorMode && root.appBackend.selectedSegmentIndex >= 0; onActivated: root.appBackend.deleteSelectedSegment() }

    Connections {
        target: root.appBackend
        function onSettingsChanged() { root.syncSettings() }
    }

    Component.onCompleted: {
        root.syncSettings()
    }
    onClosing: {
        root.appBackend.saveProject()
        mainPlayer.stop()
    }
}
