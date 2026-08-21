pragma ComponentBehavior: Bound
import QtQuick
import QtQml.Models
import QtQuick.Controls
import QtQuick.Dialogs
import "../components"
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
    readonly property int defaultSubtitleFontSize: 50
    property int subtitleFontSizePercent: 100
    property string subtitleOutlineColor: "#000000"
    property int subtitleOutlineThickness: 3
    readonly property int selectedSubtitleFontSize: Math.max(
        3,
        Math.round(root.defaultSubtitleFontSize * root.subtitleFontSizePercent / 100)
    )
    readonly property string selectedSubtitleOutlineColor: root.subtitleOutlineColor
    readonly property int selectedSubtitleOutlineThickness: root.subtitleOutlineThickness
    property var projectSpeakerCache: root.appBackend.projectSpeakers
    property var subtitleWaveformCache: root.appBackend.subtitleWaveforms
    property real editorPositionCache: 0
    property real editorTimelineScrollX: 0
    property real editorCaptionScrollY: 0
    property int editorDraftSegmentIndex: -1
    property string editorDraftText: ""
    property bool editorMode: false
    property bool mixerMode: false
    property bool dictionaryMode: false
    property bool shortMode: false
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

    ColorDialog {
        id: outlineColorDialog
        objectName: "outlineColorDialog"
        title: "字幕の縁取り色を選択"
        onAccepted: outlineColorButton.colorValue = selectedColor.toString()
    }

    function openOutlineColorPicker() {
        outlineColorDialog.selectedColor = outlineColorButton.colorValue
        outlineColorDialog.open()
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
            "subtitle_font_size": root.selectedSubtitleFontSize,
            "subtitle_outline_color": root.selectedSubtitleOutlineColor,
            "subtitle_outline_thickness": root.selectedSubtitleOutlineThickness,
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
            "postprocess_workers": workersSpin.value,
            "reference_audio": root.appBackend.speakers.length > 0 ? referenceCombo.currentValue : "",
            "reference_track": root.appBackend.audioTracks.length > 0 ? trackCombo.currentValue : "",
            "alignment_offset_adjustment": Number(manualOffsetField.text || 0)
        }
    }

    function coalesceSetting(value, fallback) {
        return value === undefined || value === null ? fallback : value
    }

    function userFacingStateLabel(value) {
        var text = String(value || "")
        if (text.length === 0)
            return ""
        var labels = {
            "starting": "準備中",
            "authenticating": "接続中",
            "running": "処理中",
            "cancelling": "キャンセル中",
            "cancelled": "キャンセル済み",
            "idle": "待機中",
            "ready": "準備完了",
            "complete": "完了",
            "completed": "完了",
            "success": "完了",
            "error": "エラー",
            "disabled": "利用できません"
        }
        var key = text.toLowerCase()
        if (labels[key] !== undefined)
            return labels[key]
        return /[\u3040-\u30ff\u3400-\u9fff]/.test(text) ? text : "処理状況を確認中"
    }

    function userFacingStatusLabel(stage, status) {
        var stageLabels = {
            "READY": "準備完了",
            "STARTING": "準備中",
            "CHECK": "確認中",
            "BUSY": "処理中",
            "TRANSCRIBE": "文字起こし中",
            "COMPLETE": "完了",
            "ERROR": "エラー",
            "UPDATE": "更新中",
            "RENDER": "書き出し中"
        }
        var stageLabel = stageLabels[String(stage || "").toUpperCase()] || ""
        var statusLabel = root.userFacingStateLabel(status)
        if (!stageLabel)
            return statusLabel
        if (!statusLabel || statusLabel === stageLabel || statusLabel === "処理状況を確認中")
            return stageLabel
        return stageLabel + " · " + statusLabel
    }

    function alignmentStatusLabel(value) {
        var labels = {
            "未解析": "未解析",
            "running": "調整中",
            "success": "調整完了",
            "completed": "調整完了",
            "error": "調整に失敗しました"
        }
        var text = String(value || "")
        var key = text.toLowerCase()
        if (labels[key] !== undefined)
            return labels[key]
        return /^[\u3040-\u30ff\u3400-\u9fff]/.test(text) ? text : "結果を確認中"
    }

    function formatBytes(value) {
        var bytes = Number(value || 0)
        if (!isFinite(bytes) || bytes <= 0)
            return "0 KB"
        if (bytes < 1024 * 1024)
            return (bytes / 1024).toFixed(1) + " KB"
        return (bytes / (1024 * 1024)).toFixed(1) + " MB"
    }

    function syncSettings() {
        var value = root.appBackend.settings
        qualitySpin.value = Number(coalesceSetting(value.nvenc_cq, 18))
        root.subtitleFontSizePercent = Math.round(Number(coalesceSetting(value.subtitle_font_size, root.defaultSubtitleFontSize)) / root.defaultSubtitleFontSize * 100)
        fontSizeSpin.value = root.subtitleFontSizePercent
        root.subtitleOutlineColor = String(coalesceSetting(value.subtitle_outline_color, "#000000"))
        outlineColorButton.colorValue = root.subtitleOutlineColor
        root.subtitleOutlineThickness = Number(value.subtitle_outline_thickness === undefined ? 3 : value.subtitle_outline_thickness)
        outlineThicknessSpin.value = root.subtitleOutlineThickness
        volumeScaleSpin.value = Number(value.subtitle_volume_scale_percent === undefined ? 20 : value.subtitle_volume_scale_percent)
        gapField.text = Number(coalesceSetting(value.subtitle_max_gap_seconds, 0.1)).toFixed(2)
        paddingField.text = Number(coalesceSetting(value.subtitle_end_padding_seconds, 0.08)).toFixed(2)
        minDurationField.text = Number(coalesceSetting(value.subtitle_min_duration_seconds, 0.35)).toFixed(2)
        silenceField.text = Number(coalesceSetting(value.no_speech_min_seconds, 1.2)).toFixed(1)
        speechPaddingField.text = Number(coalesceSetting(value.speech_padding_seconds, 0.25)).toFixed(2)
        speechThresholdField.text = parseFloat(String(coalesceSetting(value.speech_threshold_db, "-40dB"))).toFixed(0)
        lufsField.text = Number(coalesceSetting(value.audio_target_lufs, -16)).toFixed(0)
        normalizeSwitch.checked = value.audio_normalize === undefined ? true : value.audio_normalize
        silenceSwitch.checked = Boolean(value.cut_no_speech)
        workersSpin.value = Number(coalesceSetting(value.postprocess_workers, 4))
        modelCombo.currentIndex = Math.max(0, modelCombo.find(coalesceSetting(value.model, "large-v3")))
        deviceCombo.currentIndex = Math.max(0, deviceCombo.find(coalesceSetting(value.device, "cuda")))
        manualOffsetField.text = Number(coalesceSetting(value.alignment_offset_adjustment, 0)).toFixed(3)
    }
    function toggleSettingsPopup() {
        if (advancedSettingsPopup.opened)
            advancedSettingsPopup.close()
        else
            advancedSettingsPopup.open()
    }

    function transcriptionBlockReason() {
        if (root.appBackend.running)
            return "処理中です。完了または停止するまで入力と編集は変更できません"
        if (!root.appBackend.dependencyStatus.ready)
            return "実行ツールが不足しています: " + root.appBackend.dependencyStatus.missing.join(", ")
        if (deviceCombo.currentText === "cuda" && !root.appBackend.dependencyStatus.cuda)
            return "GPU処理を利用できません。処理方法をCPUに変更するか、アプリの実行環境を修復してください"
        if (!root.appBackend.sourceSelection.video)
            return "素材設定で動画を指定してください"
        if (root.appBackend.speakers.length === 0 && root.appBackend.audioTracks.length <= 1)
            return "話者音声または動画内音声が必要です"
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

    function beginSubtitleDraft(segmentIndex, text) {
        root.editorDraftSegmentIndex = segmentIndex
        root.editorDraftText = String(text)
    }

    function updateSubtitleDraft(segmentIndex, text) {
        if (root.editorDraftSegmentIndex !== segmentIndex)
            root.editorDraftSegmentIndex = segmentIndex
        root.editorDraftText = String(text)
    }

    function clearSubtitleDraft(segmentIndex) {
        if (root.editorDraftSegmentIndex !== segmentIndex)
            return
        root.editorDraftSegmentIndex = -1
        root.editorDraftText = ""
    }

    function subtitlePreviewText(segmentData) {
        var sourceIndex = Number(segmentData.sourceIndex)
        if (root.editorMode && sourceIndex === root.editorDraftSegmentIndex)
            return root.appBackend.formatSubtitlePreview(sourceIndex, root.editorDraftText)
        if (segmentData.preview_text !== undefined)
            return String(segmentData.preview_text)
        return String(segmentData.text || "")
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

    function closeSettingsPopup() {
        if (advancedSettingsPopup.opened)
            advancedSettingsPopup.close()
    }

    function openEditorScreen() {
        root.closeSettingsPopup()
        if (!root.mixerMode) {
            root.editorPositionCache = mainPlayer.position
            mainPlayer.pause()
        } else
            root.appBackend.stopAudioMixerPreview()
        root.mixerMode = false
        root.dictionaryMode = false
        root.shortMode = false
        root.editorMode = true
    }

    function closeEditorScreen() {
        mainPlayer.position = root.editorPositionCache
        root.editorMode = false
    }

    function openMixerScreen() {
        root.closeSettingsPopup()
        root.editorPositionCache = mainPlayer.position
        mainPlayer.pause()
        root.editorMode = false
        root.dictionaryMode = false
        root.shortMode = false
        root.appBackend.prepareAudioMixerPreview()
        root.mixerMode = true
    }

    function closeMixerScreen() {
        root.appBackend.stopAudioMixerPreview()
        mainPlayer.position = root.editorPositionCache
        root.mixerMode = false
    }

    function openDictionaryScreen() {
        if (root.appBackend.running)
            return
        root.closeSettingsPopup()
        root.editorPositionCache = mainPlayer.position
        mainPlayer.pause()
        root.appBackend.stopAudioMixerPreview()
        root.editorMode = false
        root.mixerMode = false
        root.shortMode = false
        root.dictionaryMode = true
    }

    function closeDictionaryScreen() {
        mainPlayer.position = root.editorPositionCache
        root.dictionaryMode = false
    }

    function openShortModeScreen() {
        if (root.appBackend.running)
            return
        root.closeSettingsPopup()
        root.editorPositionCache = mainPlayer.position
        mainPlayer.pause()
        root.appBackend.stopAudioMixerPreview()
        root.editorMode = false
        root.mixerMode = false
        root.dictionaryMode = false
        root.shortMode = true
    }

    function closeShortModeScreen() {
        root.shortMode = false
        mainPlayer.position = root.editorPositionCache
    }

    function volumePercentToDb(percent) {
        var value = Math.max(0, Number(percent) || 0)
        return value <= 0 ? -60 : Math.max(-60, Math.min(6, 20 * Math.log(value / 100) / Math.LN10))
    }

    function dbToVolumePercent(db) {
        var value = Number(db)
        return value <= -60 ? 0 : Math.max(0, Math.min(200, 100 * Math.pow(10, value / 20)))
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

    component SubtitleTimeline: Rectangle {
        id: timelineRoot
        property MediaPlayer player
        property real pixelsPerSecond: 40
        property real snapSeconds: 0.1
        property int laneHeight: 42
        readonly property int rulerHeight: 28
        readonly property int laneInset: 3
        property bool editable: true
        property bool showSegments: true
        property bool showTrackVolume: false
        property var lanes: root.projectSpeakerCache
        property var waveforms: root.subtitleWaveformCache
        property alias viewportX: timelineFlick.contentX
        property alias viewportY: timelineFlick.contentY
        property var visibleSegments: []
        property var visibleRulerTicks: []

        function refreshViewport() {
            if (timelineRoot.pixelsPerSecond <= 0)
                return
            var pixels = timelineRoot.pixelsPerSecond
            var padding = Math.max(2, timelineFlick.width / pixels * 0.25)
            var viewportStart = Math.max(0, timelineFlick.contentX / pixels - padding)
            var viewportEnd = (timelineFlick.contentX + timelineFlick.width) / pixels + padding
            timelineRoot.visibleSegments = timelineRoot.showSegments
                ? root.appBackend.visibleSubtitleSegments(viewportStart, viewportEnd)
                : []

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

        function laneForItem(item) {
            var key = item && item.lane_id !== undefined ? item.lane_id : (item ? item.style : "")
            for (var index = 0; index < timelineRoot.lanes.length; ++index) {
                var lane = timelineRoot.lanes[index]
                var laneKey = lane && lane.lane_id !== undefined ? lane.lane_id : lane.style
                if (laneKey === key)
                    return index
            }
            return 0
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
            contentHeight: timelineRoot.rulerHeight + Math.max(1, timelineRoot.lanes.length) * timelineRoot.laneHeight
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
                    model: timelineRoot.lanes
                    delegate: Rectangle {
                        required property int index
                        objectName: "timelineLaneBody-" + index
                        y: timelineRoot.rulerHeight + index * timelineRoot.laneHeight
                        width: timelineCanvas.width
                        height: timelineRoot.laneHeight
                        color: index % 2 === 0 ? "#111714" : "#0D1210"
                        border.color: "#202923"
                    }
                }

                Repeater {
                    model: timelineRoot.showTrackVolume ? timelineRoot.lanes : []
                    delegate: Rectangle {
                        id: trackVolumeBar
                        objectName: "mixerSequenceVolumeBar"
                        required property var modelData
                        property int laneIndex: timelineRoot.laneForItem(modelData)
                        property real volumeRatio: Math.max(0, Math.min(1, Number(modelData.volume_percent || 0) / 100))
                        x: Math.max(0, Number(modelData.offset_seconds || 0)) * timelineRoot.pixelsPerSecond
                        y: timelineRoot.rulerHeight + laneIndex * timelineRoot.laneHeight + (timelineRoot.laneHeight - height) / 2
                        width: Math.max(4, Number(modelData.duration_seconds || 0) * timelineRoot.pixelsPerSecond)
                        height: Math.max(3, (timelineRoot.laneHeight - 12) * volumeRatio)
                        radius: 3
                        color: modelData.color || root.amber
                        opacity: modelData.audible ? 0.32 : 0.09
                    }
                }

                Repeater {
                    model: timelineRoot.waveforms
                    delegate: Item {
                        id: waveDelegate
                        required property var modelData
                        property int laneIndex: timelineRoot.laneForItem(modelData)
                        x: Number(modelData.offset_seconds || 0) * timelineRoot.pixelsPerSecond
                        y: timelineRoot.rulerHeight + laneIndex * timelineRoot.laneHeight + timelineRoot.laneInset
                        width: Number(modelData.duration_seconds || 0) * timelineRoot.pixelsPerSecond
                        height: timelineRoot.laneHeight - timelineRoot.laneInset * 2
                        opacity: modelData.audible === false ? 0.08 : 0.3
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
                        y: timelineRoot.rulerHeight + root.laneForStyle(segment.speaker || "") * timelineRoot.laneHeight + timelineRoot.laneInset
                        width: Math.max(10, (Number(segment.end || 0) - Number(segment.start || 0)) * timelineRoot.pixelsPerSecond)
                        height: timelineRoot.laneHeight - timelineRoot.laneInset * 2 - 1
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
            y: timelineRoot.rulerHeight - timelineFlick.contentY
            width: 86
            height: timelineRoot.lanes.length * timelineRoot.laneHeight
            Repeater {
                model: timelineRoot.lanes
                delegate: Rectangle {
                    id: laneLabel
                    required property int index
                    required property var modelData
                    objectName: "timelineLaneLabel-" + index
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
        height: root.editorMode || root.mixerMode || root.dictionaryMode || root.shortMode ? 0 : 62
        visible: !root.editorMode && !root.mixerMode && !root.dictionaryMode && !root.shortMode
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
                Text { text: "素材  /  文字起こし  /  字幕・音量編集  /  書き出し"; color: root.acid; font.family: "Yu Gothic UI"; font.pixelSize: 9; font.letterSpacing: 1.0 }
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
            ColumnLayout {
                spacing: 1
                Text {
                    Layout.preferredWidth: 300
                    text: "バージョン: " + root.appBackend.applicationInfo.version
                    color: root.acid
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 10
                    elide: Text.ElideRight
                }
            }
            SmallButton { objectName: "checkForUpdatesButton"; text: "更新確認"; enabled: !root.appBackend.running && !root.appBackend.updateBusy; onClicked: root.appBackend.checkForUpdates() }
            SmallButton { objectName: "projectOpenButton"; text: "プロジェクトを開く"; enabled: !root.appBackend.running; onClicked: root.appBackend.browseProjectFile() }
            SmallButton { objectName: "sourceSetupButton"; text: "素材設定"; enabled: !root.appBackend.running; onClicked: sourcePopup.open() }
            Rectangle { Layout.preferredWidth: 9; Layout.preferredHeight: 9; radius: 5; color: root.appBackend.running ? root.amber : root.acid }
        }
    }

    Dialog {
        id: updateDialog
        objectName: "updateDialog"
        anchors.centerIn: parent
        modal: true
        title: "更新の確認"
        visible: root.appBackend.updateAvailable && (!root.appBackend.updateBusy || root.appBackend.updateDownloadActive)
        standardButtons: Dialog.NoButton
        width: 500
        height: 320
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 8
            Text { text: "現在のバージョン: " + root.appBackend.updateCurrentVersion; color: root.textPrimary; font.family: "Yu Gothic UI"; font.pixelSize: 12 }
            Text { text: "最新バージョン: " + root.appBackend.updateLatestVersion; color: root.acid; font.family: "Yu Gothic UI"; font.pixelSize: 12 }
            Text {
                text: root.appBackend.updateReleaseNotes
                color: root.textMuted
                font.family: "Yu Gothic UI"
                font.pixelSize: 10
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                Layout.fillHeight: true
            }
            Text {
                visible: root.appBackend.updatePackageSize > 0
                text: "ダウンロードサイズ: " + root.formatBytes(root.appBackend.updatePackageSize)
                color: root.textMuted
                font.family: "Yu Gothic UI"
                font.pixelSize: 11
                Layout.fillWidth: true
            }
            ProgressBar {
                objectName: "updateDownloadProgressBar"
                visible: root.appBackend.updateDownloadActive
                from: 0
                to: root.appBackend.updateDownloadTotal > 0 ? root.appBackend.updateDownloadTotal : 1
                value: root.appBackend.updateDownloadBytes
                Layout.fillWidth: true
            }
            RowLayout {
                Button { objectName: "applyUpdateButton"; text: root.appBackend.updatePackageReady ? "再起動して更新" : "アップデート"; visible: root.appBackend.stage !== "UPDATE"; enabled: !root.appBackend.running && !root.appBackend.projectDirty && !root.appBackend.updateBusy; onClicked: root.appBackend.applyUpdate() }
                Button { objectName: "cancelUpdateDownloadButton"; text: "ダウンロードをキャンセル"; visible: root.appBackend.updateDownloadActive; enabled: true; onClicked: root.appBackend.cancelUpdateDownload() }
                Button { objectName: "restartApplicationButton"; text: "再起動"; visible: root.appBackend.stage === "UPDATE" && !root.appBackend.running; enabled: !root.appBackend.running; onClicked: root.appBackend.restartApplication() }
                Button { objectName: "dismissUpdateDialogButton"; text: "閉じる"; enabled: !root.appBackend.running && !root.appBackend.updateBusy; onClicked: { root.appBackend.dismissUpdateInfo(); updateDialog.close(); } }
            }
        }
    }

        Popup {
            id: advancedSettingsPopup
            objectName: "advancedSettingsPopup"
            // Keep the popup clear of the action bar's right-aligned toggle,
            // including at the 1220px minimum window width.
            x: Math.max(12, root.width - width - 430)
            y: 84
            width: Math.min(360, root.width - 24)
            height: Math.min(620, root.height - 120)
            modal: false
            focus: true
            // The toggle button is outside this non-modal popup. Let the toggle
            // handler own the close action so an outside press cannot close the
            // popup before the same press reopens it through onSettingsRequested.
            closePolicy: Popup.CloseOnEscape
            onOpened: root.settingsExpanded = true
            onClosed: root.settingsExpanded = false
            contentItem: ScrollView {
                objectName: "advancedSettingsPanel"
                anchors.fill: parent
                clip: true
                ColumnLayout { width: Math.max(0, advancedSettingsPopup.width - 34); x: 16; spacing: 10
                        SmallButton { objectName: "settingsPopupSaveButton"; Layout.fillWidth: true; text: "設定を保存"; enabled: !root.appBackend.running; onClicked: root.appBackend.saveSettings(root.currentSettings()) }
                        SmallButton { objectName: "settingsPopupCloseButton"; Layout.fillWidth: true; text: "閉じる"; onClicked: advancedSettingsPopup.close() }
                        Item { Layout.preferredHeight: 2 }
                        PanelTitle { text: "文字起こしエンジン" }
                        RowLayout { Layout.fillWidth: true; Text { text: "処理デバイス"; color: root.textPrimary; Layout.fillWidth: true } ComboBox { id: deviceCombo; model: ["cuda", "cpu"]; Layout.preferredWidth: 110 } }
                        RowLayout { Layout.fillWidth: true; Text { text: "Whisperモデル"; color: root.textPrimary; Layout.fillWidth: true } ComboBox { id: modelCombo; model: ["large-v3", "medium", "small"]; Layout.preferredWidth: 130 } }
                        RowLayout { Layout.fillWidth: true; Text { text: "CPU並列数"; color: root.textPrimary; Layout.fillWidth: true } SpinBox { id: workersSpin; from: 1; to: 16; value: 4 } }
                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }
                        PanelTitle { text: "字幕" }
                        RowLayout { Layout.fillWidth: true; Text { text: "基準文字サイズ"; color: root.textPrimary; Layout.fillWidth: true } SpinBox { id: fontSizeSpin; objectName: "fontSizeSpin"; from: 10; to: 900; value: 100; onValueChanged: root.subtitleFontSizePercent = value } Text { text: "%"; color: root.textMuted } }
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: "縁取り色"; color: root.textPrimary; Layout.fillWidth: true }
                            Button {
                                id: outlineColorButton
                                objectName: "outlineColorButton"
                                property string colorValue: "#000000"
                                onColorValueChanged: root.subtitleOutlineColor = colorValue
                                Layout.preferredWidth: 112
                                Layout.preferredHeight: 32
                                onClicked: root.openOutlineColorPicker()
                                contentItem: Row {
                                    spacing: 7
                                    Rectangle { width: 20; height: 20; radius: 4; color: outlineColorButton.colorValue; border.color: root.border }
                                    Text { text: outlineColorButton.colorValue; color: root.textPrimary; font.pixelSize: 10; anchors.verticalCenter: parent.verticalCenter }
                                }
                                background: Rectangle { radius: 6; color: root.raised; border.color: root.border }
                            }
                        }
                        RowLayout { Layout.fillWidth: true; Text { text: "縁取り太さ"; color: root.textPrimary; Layout.fillWidth: true } SpinBox { id: outlineThicknessSpin; objectName: "outlineThicknessSpin"; from: 0; to: 20; value: 3; onValueChanged: root.subtitleOutlineThickness = value } Text { text: "px"; color: root.textMuted } }
                        RowLayout { Layout.fillWidth: true; Text { text: "字幕の音量バランス"; color: root.textPrimary; Layout.fillWidth: true } SpinBox { id: volumeScaleSpin; objectName: "volumeScaleSpin"; from: 0; to: 50; value: 20 } Text { text: "%"; color: root.textMuted } }
                        RowLayout { Layout.fillWidth: true; Text { text: "単語間隔"; color: root.textPrimary; Layout.fillWidth: true } TimeField { id: gapField; Layout.preferredWidth: 76; text: "0.10" } }
                        RowLayout { Layout.fillWidth: true; Text { text: "終了余白"; color: root.textPrimary; Layout.fillWidth: true } TimeField { id: paddingField; Layout.preferredWidth: 76; text: "0.08" } }
                        RowLayout { Layout.fillWidth: true; Text { text: "最短表示時間"; color: root.textPrimary; Layout.fillWidth: true } TimeField { id: minDurationField; Layout.preferredWidth: 76; text: "0.35" } }
                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }
                        PanelTitle { text: "動画・音声" }
                        RowLayout { Layout.fillWidth: true; Text { text: "動画書き出し"; color: root.textPrimary; Layout.fillWidth: true } Text { objectName: "automaticVideoCodecText"; text: root.appBackend.dependencyStatus.nvenc ? "GPU（自動）" : "CPU（自動）"; color: root.acid; font.family: "Yu Gothic UI" } }
                        RowLayout { Layout.fillWidth: true; Text { text: "画質"; color: root.textPrimary; Layout.fillWidth: true } SpinBox { id: qualitySpin; objectName: "qualitySpin"; from: 14; to: 28; value: 18 } }
                        Switch { id: normalizeSwitch; objectName: "normalizeSwitch"; text: "音量を正規化"; checked: true }
                        RowLayout { Layout.fillWidth: true; Text { text: "目標LUFS"; color: root.textPrimary; Layout.fillWidth: true } TimeField { id: lufsField; objectName: "lufsField"; Layout.preferredWidth: 76; text: "-16"; validator: DoubleValidator { bottom: -30; top: -5 } } }
                        Switch { id: silenceSwitch; objectName: "silenceSwitch"; text: "無音部分をカット" }
                        RowLayout { Layout.fillWidth: true; enabled: silenceSwitch.checked; opacity: enabled ? 1 : 0.4; Text { text: "最短無音時間"; color: root.textPrimary; Layout.fillWidth: true } TimeField { id: silenceField; objectName: "silenceField"; Layout.preferredWidth: 76; text: "1.2" } }
                        RowLayout { Layout.fillWidth: true; enabled: silenceSwitch.checked; opacity: enabled ? 1 : 0.4; Text { text: "発話余白"; color: root.textPrimary; Layout.fillWidth: true } TimeField { id: speechPaddingField; objectName: "speechPaddingField"; Layout.preferredWidth: 76; text: "0.25" } }
                        RowLayout { Layout.fillWidth: true; enabled: silenceSwitch.checked; opacity: enabled ? 1 : 0.4; Text { text: "無音判定閾値"; color: root.textPrimary; Layout.fillWidth: true } TimeField { id: speechThresholdField; objectName: "speechThresholdField"; Layout.preferredWidth: 76; text: "-40"; validator: DoubleValidator { bottom: -100; top: 0; decimals: 1 } } }
                        Item { Layout.preferredHeight: 6 }
                    }
                }

        }

    RowLayout {
        objectName: "mainWorkspace"
        visible: !root.editorMode && !root.mixerMode && !root.dictionaryMode && !root.shortMode
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
                        text: root.appBackend.alignmentBusy ? "調整中" : "音声のずれを自動調整"
                        enabled: !root.appBackend.running && !root.appBackend.alignmentBusy && root.appBackend.speakers.length > 0 && root.appBackend.sourceSelection.video
                        onClicked: root.appBackend.analyzeAlignment(referenceCombo.currentValue || "", trackCombo.currentValue || "", Number(manualOffsetField.text || 0))
                    }
                }
                Text { Layout.fillWidth: true; text: root.alignmentStatusLabel(root.appBackend.alignmentResult.status) + (root.appBackend.alignmentResult.offset !== undefined ? "  " + Number(root.appBackend.alignmentResult.offset).toFixed(3) + "秒" : ""); color: root.textMuted; font.pixelSize: 10; font.family: "Yu Gothic UI" }
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
                        model: ["素材", "文字起こし", "字幕・音量編集", "書き出し"]
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
            ContextActionBar {
                objectName: "contextActionBar"
                Layout.fillWidth: true
                projectLoaded: root.appBackend.projectLoaded
                running: root.appBackend.running
                activeJob: root.appBackend.activeJob
                canCreateProject: Boolean(root.appBackend.sourceSelection.video) && Boolean(root.appBackend.sourceSelection.output_dir)
                canStartTranscription: !root.appBackend.running && root.appBackend.sourceSelection.video && root.appBackend.sourceSelection.output_dir && (root.appBackend.speakers.length > 0 || root.appBackend.audioTracks.length > 1) && root.appBackend.dependencyStatus.ready && (deviceCombo.currentText !== "cuda" || root.appBackend.dependencyStatus.cuda)
                blockReason: root.transcriptionBlockReason()
                audioMixerAvailable: root.appBackend.audioMixerAvailable
                mixerBlockReason: root.appBackend.projectLoaded && !root.appBackend.audioMixerAvailable ? "音声トラックがないため音量を調整できません" : ""
                subtitleAvailable: root.appBackend.subtitleSegments.length > 0
                outputFolderAvailable: Boolean(root.appBackend.sourceSelection.output_dir)
                settingsExpanded: root.settingsExpanded
                onSettingsRequested: root.toggleSettingsPopup()
                onDictionaryRequested: root.openDictionaryScreen()
                onCreateProjectRequested: root.appBackend.createEmptyProject()
                onStartTranscriptionRequested: {
                    if (root.appBackend.projectLoaded)
                        transcriptionMergeDialog.open()
                    else if (root.appBackend.transcriptionProjectExists())
                        overwriteProjectDialog.open()
                    else
                        root.appBackend.startTranscription(root.currentSettings(), false)
                }
                onEditorRequested: root.openEditorScreen()
                onMixerRequested: root.openMixerScreen()
                onShortModeRequested: root.openShortModeScreen()
                onRenderRequested: root.appBackend.renderVideo(root.currentSettings())
                onSaveOrStopRequested: {
                    if (!root.appBackend.running)
                        root.appBackend.saveSettings(root.currentSettings())
                    else if (root.appBackend.activeJob !== "update")
                        root.appBackend.cancelProcessing()
                }
                onOutputFolderRequested: root.appBackend.openOutputFolder()
            }
            Rectangle {
                objectName: "mainVideoPanel"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: applicationLogPanel.expanded && root.height <= 800 ? 140 : 300
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
                SubtitleOverlay {
                    anchors.fill: mainVideo
                    appBackend: root.appBackend
                    player: mainPlayer
                    captionObjectPrefix: "mainSubtitleOverlayCaption"
                    baseFontSize: root.selectedSubtitleFontSize
                    defaultSubtitleFontSize: root.defaultSubtitleFontSize
                    outlineColor: root.selectedSubtitleOutlineColor
                    outlineThickness: root.selectedSubtitleOutlineThickness
                    speakerColors: root.projectSpeakerCache
                    subtitleTextResolver: function(segmentData) { return root.subtitlePreviewText(segmentData) }
                }
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

            ApplicationLogPanel {
                id: applicationLogPanel
                objectName: "applicationLogPanel"
                Layout.fillWidth: true
                Layout.preferredHeight: implicitHeight
                Layout.minimumHeight: implicitHeight
                backend: root.appBackend
            }
        }

        CodexSidebarContainer {
            Layout.preferredWidth: 300
            Layout.minimumWidth: 280
            Layout.fillHeight: true
        }
    }

  Dialog {
      id: overwriteProjectDialog
      objectName: "overwriteProjectDialog"
      anchors.centerIn: Overlay.overlay
      modal: true
      title: "既存プロジェクトの上書き"
      standardButtons: Dialog.Yes | Dialog.No

      contentItem: Text {
          width: 420
          text: "同じ動画の編集プロジェクトが既に存在します。\n既存プロジェクトを上書きして文字起こしを再実行しますか？"
          color: root.textPrimary
          font.family: "Yu Gothic UI"
          font.pixelSize: 12
          wrapMode: Text.Wrap
      }

      onAccepted: root.appBackend.startTranscription(root.currentSettings(), true)
  }

  Dialog {
      id: transcriptionMergeDialog
      objectName: "transcriptionMergeDialog"
      anchors.centerIn: Overlay.overlay
      modal: true
      title: "既存字幕の取り込み方法"
      standardButtons: Dialog.NoButton

      contentItem: ColumnLayout {
          width: 440
          spacing: 10
          Text {
              Layout.fillWidth: true
              text: "既存の字幕があります。文字起こし結果の取り込み方法を選択してください。既存字幕は確認なしに削除されません。"
              color: root.textPrimary
              font.family: "Yu Gothic UI"
              font.pixelSize: 12
              wrapMode: Text.Wrap
          }
          RowLayout {
              Layout.fillWidth: true
              Item { Layout.fillWidth: true }
              Button { objectName: "transcriptionMergeCancelButton"; text: "キャンセル"; onClicked: transcriptionMergeDialog.close() }
              Button { objectName: "transcriptionMergeAppendButton"; text: "追加・統合"; onClicked: { transcriptionMergeDialog.close(); root.appBackend.transcribeProject(root.currentSettings(), "merge") } }
              Button { objectName: "transcriptionMergeReplaceButton"; text: "置き換え"; onClicked: { transcriptionMergeDialog.close(); root.appBackend.transcribeProject(root.currentSettings(), "replace") } }
          }
      }
  }

  Popup {
        objectName: "sourcePopup"
        id: sourcePopup
        anchors.centerIn: Overlay.overlay
        width: 620; height: 520; modal: true; focus: true; closePolicy: Popup.CloseOnEscape
        onOpened: root.appBackend.beginSourceRelink()
        onClosed: root.appBackend.finishSourceRelink()
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
            RowLayout { Layout.fillWidth: true; Text { objectName: "sourceVideoPathText"; Layout.fillWidth: true; text: root.appBackend.sourceSelection.video || "未選択"; color: root.textMuted; elide: Text.ElideMiddle } SmallButton { text: "選択"; enabled: !root.appBackend.running; onClicked: root.appBackend.browseVideoFile() } }
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
            RowLayout { Layout.fillWidth: true; Item { Layout.fillWidth: true } Button { objectName: "sourceRelinkButton"; text: "素材を再指定"; enabled: root.appBackend.projectLoaded && !root.appBackend.running; onClicked: root.appBackend.relinkProjectSources() } Button { objectName: "sourceDoneButton"; text: "完了"; onClicked: sourcePopup.close() } }
        }
    }

    Rectangle {
        id: mixerPage
        objectName: "mixerPage"
        anchors.fill: parent
        visible: root.mixerMode
        z: 100
        color: "#0D1210"
        border.color: "#46564E"
        focus: visible
        Keys.onEscapePressed: root.closeMixerScreen()
        onVisibleChanged: if (visible) forceActiveFocus()

        Loader {
            id: mixerLoader
            anchors.fill: parent
            active: root.mixerMode
            sourceComponent: mixerContentComponent
        }

        Component {
            id: mixerContentComponent
            Item {
                id: mixerContent
                objectName: "mixerContent"
                readonly property bool previewReady: !root.appBackend.audioPreviewPreparing
                    && root.appBackend.audioMixerPreviewChannels.length > 0
                property real initialPosition: -1
                property int initialPositionStableTicks: 0
                property real channelScrollPosition: 0
                property bool restoringChannelScroll: false
                Component.onCompleted: {
                    initialPosition = root.editorPositionCache
                    restoreInitialPosition()
                }

                function restoreInitialPosition(confirmStable) {
                    if (!mixerPlayer.seekable || mixerPlayer.duration <= 0 || initialPosition < 0)
                        return
                    var target = Math.max(
                        0,
                        Math.min(mixerPlayer.duration, initialPosition)
                    )
                    if (Math.abs(mixerPlayer.position - target) <= 80) {
                        if (confirmStable) {
                            initialPositionStableTicks += 1
                            if (initialPositionStableTicks >= 6)
                                initialPosition = -1
                        }
                        return
                    }
                    initialPositionStableTicks = 0
                    mixerPlayer.position = target
                }

                function syncPreviewPlayer(player, forcePosition) {
                    if (!player)
                        return
                    var target = mixerPlayer.position - Number(player.previewOffsetMilliseconds || 0)
                    if (target < 0) {
                        player.scheduleSync(0, true, false)
                        return
                    }
                    if (player.duration > 0 && target >= player.duration) {
                        player.scheduleSync(player.duration, true, false)
                        return
                    }
                    player.scheduleSync(
                        target,
                        forcePosition,
                        mixerPlayer.playbackState === MediaPlayer.PlayingState
                    )
                }

                function syncPreviewPlayers(forcePosition) {
                    for (var index = 0; index < mixerPreviewPlayers.count; ++index)
                        mixerContent.syncPreviewPlayer(mixerPreviewPlayers.objectAt(index), forcePosition)
                }

                function togglePlayback() {
                    if (mixerPlayer.playbackState === MediaPlayer.PlayingState) {
                        mixerPlayer.pause()
                        root.appBackend.pauseAudioMixerPreview()
                        mixerContent.syncPreviewPlayers(false)
                    } else {
                        root.appBackend.startAudioMixerPreview(mixerPlayer.position)
                        mixerPlayer.play()
                        mixerContent.syncPreviewPlayers(true)
                    }
                }

                function seekTo(milliseconds) {
                    mixerPlayer.position = Math.max(
                        0,
                        Math.min(mixerPlayer.duration, milliseconds)
                    )
                    root.appBackend.seekAudioMixerPreview(
                        mixerPlayer.position,
                        mixerPlayer.playbackState === MediaPlayer.PlayingState
                    )
                    mixerContent.syncPreviewPlayers(true)
                }

                function seekBy(milliseconds) {
                    mixerContent.seekTo(mixerPlayer.position + milliseconds)
                }

                function restoreChannelScroll() {
                    var maximum = Math.max(0, mixerChannelList.contentWidth - mixerChannelList.width)
                    restoringChannelScroll = true
                    mixerChannelList.contentX = Math.max(0, Math.min(maximum, channelScrollPosition))
                    restoringChannelScroll = false
                }

                function updateMixerChannel(index, changes) {
                    channelScrollPosition = mixerChannelList.contentX
                    mixerChannelScrollRestoreTimer.restart()
                    root.appBackend.updateAudioMixChannel(index, changes)
                }

                MediaPlayer {
                    id: mixerPlayer
                    objectName: "mixerPlayer"
                    source: mixerContent.previewReady ? root.appBackend.audioPreviewClockUrl : ""
                    audioOutput: AudioOutput { muted: true }
                    Component.onCompleted: mixerContent.restoreInitialPosition()
                    onSeekableChanged: mixerContent.restoreInitialPosition()
                    onDurationChanged: mixerContent.restoreInitialPosition()
                    onMediaStatusChanged: mixerContent.restoreInitialPosition()
                    onPositionChanged: {
                        root.editorPositionCache = mixerPlayer.position
                        if (mixerPlayer.playbackState !== MediaPlayer.PlayingState)
                            mixerContent.syncPreviewPlayers(true)
                    }
                    onPlaybackStateChanged: {
                        mixerContent.syncPreviewPlayers(false)
                        if (mixerPlayer.playbackState === MediaPlayer.StoppedState)
                            root.appBackend.pauseAudioMixerPreview()
                    }
                }

                Instantiator {
                    id: mixerPreviewPlayers
                    objectName: "mixerPreviewPlayers"
                    model: root.appBackend.audioMixerPreviewChannels
                    delegate: MediaPlayer {
                        id: mixerPreviewPlayer
                        required property int index
                        required property var modelData
                        property string previewChannelId: String(modelData.id || "")
                        objectName: "mixerPreviewPlayer-" + previewChannelId
                        property real previewOffsetMilliseconds: Number(modelData.preview_offset_seconds || 0) * 1000
                        property int requestedAudioTrack: Number(modelData.preview_audio_track_index || 0)
                        property real pendingSyncPosition: 0
                        property bool pendingForcePosition: false
                        property bool pendingPlayback: false
                        property bool hasPendingSync: false

                        function scheduleSync(target, forcePosition, shouldPlay) {
                            pendingSyncPosition = Math.max(0, Number(target || 0))
                            pendingForcePosition = pendingForcePosition || Boolean(forcePosition)
                            pendingPlayback = Boolean(shouldPlay)
                            hasPendingSync = true
                            applyPendingSync()
                        }

                        function applyPendingSync() {
                            if (!hasPendingSync || audioTracks.length <= requestedAudioTrack)
                                return
                            if (activeAudioTrack !== requestedAudioTrack)
                                activeAudioTrack = requestedAudioTrack
                            var target = duration > 0
                                ? Math.min(duration, pendingSyncPosition)
                                : pendingSyncPosition
                            if (target > 0 && !seekable)
                                return
                            if (pendingForcePosition || Math.abs(position - target) > 180)
                                position = target
                            var shouldPlay = pendingPlayback && (duration <= 0 || target < duration)
                            hasPendingSync = false
                            pendingForcePosition = false
                            if (shouldPlay)
                                play()
                            else
                                pause()
                        }

                        source: modelData.preview_url || ""
                        audioOutput: AudioOutput {
                            objectName: "mixerPreviewAudioOutput"
                            muted: true
                        }
                        // qmllint disable missing-type
                        audioBufferOutput: modelData.preview_buffer_output
                        // qmllint enable missing-type
                        onTracksChanged: applyPendingSync()
                        onSeekableChanged: applyPendingSync()
                        onMediaStatusChanged: applyPendingSync()
                    }
                    onObjectAdded: function(index, object) {
                        mixerContent.syncPreviewPlayer(object, true)
                    }
                }

                Timer {
                    interval: 50
                    running: mixerContent.previewReady && mixerContent.initialPosition >= 0
                    repeat: true
                    onTriggered: mixerContent.restoreInitialPosition(true)
                }

                Timer {
                    interval: 150
                    running: mixerPlayer.playbackState === MediaPlayer.PlayingState
                    repeat: true
                    onTriggered: mixerContent.syncPreviewPlayers(false)
                }

                Timer {
                    id: mixerChannelScrollRestoreTimer
                    interval: 0
                    repeat: false
                    onTriggered: mixerContent.restoreChannelScroll()
                }

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 0

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 64
                        Layout.leftMargin: 18
                        Layout.rightMargin: 14
                        spacing: 10
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 1
                            Text { text: "音量ミキサー"; color: root.textPrimary; font.family: "Yu Gothic UI"; font.pixelSize: 19; font.weight: Font.Bold; font.letterSpacing: 1.0 }
                            Text { text: "動画内トラックと個別音声を、完成動画用にミックス"; color: root.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 9 }
                        }
                        Text { text: root.appBackend.projectDirty ? "● 保存待ち" : "✓ 保存済み"; color: root.appBackend.projectDirty ? root.amber : root.acid; font.family: "Yu Gothic UI"; font.pixelSize: 9 }
                        Text {
                            objectName: "mixerAudioPreviewCacheSummary"
                            text: root.appBackend.audioPreviewPreparing ? "プレビューを準備中" : "プレビュー準備済み"
                            color: root.textMuted
                            font.family: "Cascadia Mono"
                            font.pixelSize: 9
                        }
                        SmallButton {
                            objectName: "mixerClearAudioPreviewCacheButton"
                            text: "プレビューを作り直す"
                            enabled: !root.appBackend.running
                            onClicked: {
                                root.appBackend.clearAudioPreviewCache()
                                root.appBackend.prepareAudioMixerPreview()
                            }
                        }
                        SmallButton { objectName: "mixerResetButton"; text: "すべての音声トラックをリセット"; enabled: !root.appBackend.running; onClicked: root.appBackend.resetAudioMixer() }
                        SmallButton { objectName: "mixerSaveButton"; text: "保存"; enabled: !root.appBackend.running; onClicked: root.appBackend.saveProject() }
                        SmallButton { objectName: "mixerToEditorButton"; text: "字幕編集へ"; enabled: !root.appBackend.running; onClicked: root.openEditorScreen() }
                        Button {
                            id: mixerRenderButton
                            objectName: "mixerRenderButton"
                            implicitHeight: 34
                            text: root.appBackend.activeJob === "render" ? "書き出し中..." : "動画を書き出す"
                            enabled: root.appBackend.projectLoaded && !root.appBackend.running
                            onClicked: {
                                root.closeMixerScreen()
                                root.appBackend.renderVideo(root.currentSettings())
                            }
                            contentItem: Text { text: mixerRenderButton.text; color: mixerRenderButton.enabled ? "#10140F" : "#68716B"; font.family: "Yu Gothic UI"; font.pixelSize: 10; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                            background: Rectangle { radius: 7; color: mixerRenderButton.enabled ? root.acid : "#252C28" }
                        }
                        SmallButton { objectName: "mixerBackButton"; text: "メインへ戻る"; onClicked: root.closeMixerScreen() }
                    }
                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.min(230, Math.max(174, 174 + (mixerContent.height - 760) * 0.31))
                        Layout.leftMargin: 14
                        Layout.rightMargin: 14
                        Layout.topMargin: 10
                        radius: 10
                        color: root.panel
                        border.color: root.border

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 10
                            spacing: 7
                            RowLayout {
                                Layout.fillWidth: true
                                Button {
                                    id: mixerPlayButton
                                    objectName: "mixerPlayButton"
                                    Layout.preferredWidth: 46
                                    Layout.preferredHeight: 34
                                    enabled: mixerContent.previewReady
                                    onClicked: mixerContent.togglePlayback()
                                    contentItem: Text {
                                        text: mixerPlayer.playbackState === MediaPlayer.PlayingState ? "Ⅱ" : "▶"
                                        color: mixerPlayButton.enabled ? "#10140F" : root.textMuted
                                        font.pixelSize: 14
                                        horizontalAlignment: Text.AlignHCenter
                                        verticalAlignment: Text.AlignVCenter
                                    }
                                    background: Rectangle { radius: 7; color: mixerPlayButton.enabled ? root.acid : "#252C28" }
                                }
                                SmallButton { objectName: "mixerRewindButton"; text: "−5秒"; enabled: mixerContent.previewReady; onClicked: mixerContent.seekBy(-5000) }
                                Slider {
                                    id: mixerSeek
                                    objectName: "mixerSeek"
                                    Layout.fillWidth: true
                                    from: 0
                                    to: Math.max(1, mixerPlayer.duration)
                                    value: mixerPlayer.position
                                    enabled: mixerContent.previewReady
                                    onMoved: mixerContent.seekTo(value)
                                }
                                SmallButton { objectName: "mixerForwardButton"; text: "+5秒"; enabled: mixerContent.previewReady; onClicked: mixerContent.seekBy(5000) }
                                Text {
                                    objectName: "mixerTimeText"
                                    Layout.preferredWidth: 142
                                    text: root.stamp(mixerPlayer.position / 1000) + " / " + root.stamp(mixerPlayer.duration / 1000)
                                    color: root.textPrimary
                                    font.family: "Cascadia Mono"
                                    font.pixelSize: 10
                                    horizontalAlignment: Text.AlignRight
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                PanelTitle { text: "プレビュー" }
                                Text { text: root.appBackend.audioPreviewPreparing ? "プレビュー音声を準備中…" : "出力音ライブプレビュー"; color: root.appBackend.audioPreviewPreparing ? root.amber : root.acid; font.family: "Yu Gothic UI"; font.pixelSize: 9 }
                                Item { Layout.fillWidth: true }
                                Text { text: "クリックで再生位置を移動"; color: root.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 8 }
                            }
                            SubtitleTimeline {
                                objectName: "mixerSequence"
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                player: mixerPlayer
                                pixelsPerSecond: root.timelinePixelsPerSecond
                                laneHeight: 42
                                editable: false
                                showSegments: false
                                showTrackVolume: true
                                lanes: root.appBackend.audioMixerSequenceChannels
                                waveforms: root.appBackend.audioMixerSequenceChannels
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.margins: 14
                        radius: 12
                        color: "#090C0B"
                        border.color: root.border

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 10
                            RowLayout {
                                Layout.fillWidth: true
                                PanelTitle { text: "音声トラック" }
                                Text { text: root.appBackend.audioMixerChannels.length + "トラック"; color: root.acid; font.family: "Yu Gothic UI"; font.pixelSize: 10 }
                                Item { Layout.fillWidth: true }
                                Text { text: "音量: −60〜+6 dB / ミュート / ソロ"; color: root.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 9 }
                            }
                            ListView {
                                id: mixerChannelList
                                objectName: "mixerChannelList"
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                orientation: ListView.Horizontal
                                spacing: 12
                                clip: true
                                boundsBehavior: Flickable.StopAtBounds
                                model: root.appBackend.audioMixerChannels
                                onContentXChanged: {
                                    if (!mixerContent.restoringChannelScroll && !mixerChannelScrollRestoreTimer.running)
                                        mixerContent.channelScrollPosition = contentX
                                }
                                delegate: Rectangle {
                                    id: mixerStrip
                                    required property int index
                                    required property var modelData
                                    objectName: "mixerChannelStrip-" + index
                                    property real previewLevel: Number(root.appBackend.audioPreviewLevels[modelData.id] || 0)
                                    width: 170
                                    height: mixerChannelList.height - 12
                                    radius: 9
                                    color: modelData.enabled ? "#171E1A" : "#101512"
                                    border.width: modelData.solo ? 2 : 1
                                    border.color: modelData.solo ? root.acid : (modelData.muted ? root.amber : root.border)
                                    opacity: modelData.enabled ? 1.0 : 0.58

                                    ColumnLayout {
                                        anchors.fill: parent
                                        anchors.margins: 10
                                        spacing: 7
                                        RowLayout {
                                            Layout.fillWidth: true
                                            Text { text: "トラック " + String(mixerStrip.index + 1).padStart(2, "0"); color: root.acid; font.family: "Yu Gothic UI"; font.pixelSize: 10; font.weight: Font.Bold }
                                            Item { Layout.fillWidth: true }
                                            Rectangle {
                                                Layout.preferredWidth: 62; Layout.preferredHeight: 20; radius: 4
                                                color: mixerStrip.modelData.kind === "external" ? "#253225" : "#272C30"
                                                Text { anchors.centerIn: parent; text: mixerStrip.modelData.kind === "external" ? "外部音声" : "動画音声"; color: root.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 8; font.weight: Font.Bold }
                                            }
                                        }
                                        Text { Layout.fillWidth: true; text: mixerStrip.modelData.label; color: root.textPrimary; font.family: "Yu Gothic UI"; font.pixelSize: 11; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideMiddle }
                                        Text { Layout.fillWidth: true; text: mixerStrip.modelData.kind === "external" ? "外部音声を使用" : "動画音声を使用"; color: root.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 8; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight }
                                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }

                                        Item {
                                            Layout.fillWidth: true
                                            Layout.fillHeight: true
                                            Layout.minimumHeight: 250
                                            RowLayout {
                                                anchors.fill: parent
                                                anchors.leftMargin: 13
                                                anchors.rightMargin: 13
                                                spacing: 8
                                                Column {
                                                    Layout.preferredWidth: 30
                                                    Layout.fillHeight: true
                                                    topPadding: 7
                                                    Text { width: parent.width; text: "+6"; color: root.textMuted; font.family: "Cascadia Mono"; font.pixelSize: 8; horizontalAlignment: Text.AlignRight }
                                                    Item { width: 1; height: (parent.height - 62) * 0.15 }
                                                    Text { width: parent.width; text: "0"; color: root.textPrimary; font.family: "Cascadia Mono"; font.pixelSize: 8; horizontalAlignment: Text.AlignRight }
                                                    Item { width: 1; height: (parent.height - 62) * 0.12 }
                                                    Text { width: parent.width; text: "−6"; color: root.textMuted; font.family: "Cascadia Mono"; font.pixelSize: 8; horizontalAlignment: Text.AlignRight }
                                                    Item { width: 1; height: (parent.height - 62) * 0.12 }
                                                    Text { width: parent.width; text: "−12"; color: root.textMuted; font.family: "Cascadia Mono"; font.pixelSize: 8; horizontalAlignment: Text.AlignRight }
                                                    Item { width: 1; height: (parent.height - 62) * 0.18 }
                                                    Text { width: parent.width; text: "−24"; color: root.textMuted; font.family: "Cascadia Mono"; font.pixelSize: 8; horizontalAlignment: Text.AlignRight }
                                                    Item { width: 1; height: (parent.height - 62) * 0.22 }
                                                    Text { width: parent.width; text: "−∞"; color: root.textMuted; font.family: "Cascadia Mono"; font.pixelSize: 8; horizontalAlignment: Text.AlignRight }
                                                }
                                                Slider {
                                                    id: channelFader
                                                    objectName: "mixerChannelFader"
                                                    Layout.fillHeight: true
                                                    Layout.preferredWidth: 54
                                                    orientation: Qt.Vertical
                                                    from: -60
                                                    to: 6.0206
                                                    stepSize: 0.5
                                                    value: root.volumePercentToDb(mixerStrip.modelData.volume_percent)
                                                    enabled: !root.appBackend.running && mixerStrip.modelData.enabled
                                                    onMoved: mixerContent.updateMixerChannel(mixerStrip.index, {"volume_percent": root.dbToVolumePercent(value)})
                                                    onPressedChanged: if (!pressed) mixerContent.updateMixerChannel(mixerStrip.index, {"volume_percent": root.dbToVolumePercent(value)})
                                                    background: Rectangle {
                                                        x: channelFader.leftPadding + channelFader.availableWidth / 2 - width / 2
                                                        y: channelFader.topPadding
                                                        width: 7
                                                        height: channelFader.availableHeight
                                                        radius: 3
                                                        color: "#090C0B"
                                                        border.color: root.border
                                                        Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: parent.height * channelFader.position; radius: 3; color: mixerStrip.modelData.muted ? root.amber : root.acid; opacity: 0.72 }
                                                    }
                                                    handle: Rectangle {
                                                        x: channelFader.leftPadding + channelFader.availableWidth / 2 - width / 2
                                                        y: channelFader.topPadding + (1 - channelFader.position) * (channelFader.availableHeight - height)
                                                        implicitWidth: 44
                                                        implicitHeight: 18
                                                        radius: 4
                                                        color: channelFader.pressed ? root.acid : root.textPrimary
                                                        border.color: "#0B0E0D"
                                                        border.width: 2
                                                        Rectangle { anchors.horizontalCenter: parent.horizontalCenter; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 8; height: 2; color: "#222A26" }
                                                    }
                                                }
                                                Rectangle {
                                                    Layout.preferredWidth: 13
                                                    Layout.fillHeight: true
                                                    Layout.topMargin: 7
                                                    Layout.bottomMargin: 7
                                                    radius: 4
                                                    color: "#070908"
                                                    border.color: root.border
                                                    Rectangle {
                                                        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 2
                                                        height: Math.max(2, (parent.height - 4) * mixerStrip.previewLevel)
                                                        radius: 2
                                                        Behavior on height { NumberAnimation { duration: 70 } }
                                                        gradient: Gradient {
                                                            GradientStop { position: 0.0; color: root.acid }
                                                            GradientStop { position: 0.72; color: root.amber }
                                                            GradientStop { position: 1.0; color: root.danger }
                                                        }
                                                    }
                                                }
                                            }
                                        }

                                        Text {
                                            Layout.fillWidth: true
                                            text: channelFader.value <= -59.9 ? "−∞ dB" : (channelFader.value >= 0 ? "+" : "") + channelFader.value.toFixed(1) + " dB"
                                            color: root.textPrimary; font.family: "Cascadia Mono"; font.pixelSize: 13; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter
                                        }
                                        Text { Layout.fillWidth: true; text: Math.round(Number(mixerStrip.modelData.volume_percent)) + "%"; color: root.textMuted; font.family: "Cascadia Mono"; font.pixelSize: 9; horizontalAlignment: Text.AlignHCenter }
                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: 6
                                            Button {
                                                id: channelMuteButton
                                                objectName: "mixerMuteButton"
                                                Layout.fillWidth: true; Layout.preferredHeight: 34
                                                text: "M"
                                                enabled: !root.appBackend.running
                                                onClicked: mixerContent.updateMixerChannel(mixerStrip.index, {"muted": !mixerStrip.modelData.muted})
                                                contentItem: Text { text: channelMuteButton.text; color: mixerStrip.modelData.muted ? "#10140F" : root.textPrimary; font.family: "Bahnschrift"; font.pixelSize: 13; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                                                background: Rectangle { radius: 6; color: mixerStrip.modelData.muted ? root.amber : root.raised; border.color: mixerStrip.modelData.muted ? root.amber : root.border }
                                                ToolTip.visible: hovered
                                                ToolTip.text: "ミュート"
                                            }
                                            Button {
                                                id: channelSoloButton
                                                objectName: "mixerSoloButton"
                                                Layout.fillWidth: true; Layout.preferredHeight: 34
                                                text: "S"
                                                enabled: !root.appBackend.running
                                                onClicked: mixerContent.updateMixerChannel(mixerStrip.index, {"solo": !mixerStrip.modelData.solo})
                                                contentItem: Text { text: channelSoloButton.text; color: mixerStrip.modelData.solo ? "#10140F" : root.textPrimary; font.family: "Bahnschrift"; font.pixelSize: 13; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                                                background: Rectangle { radius: 6; color: mixerStrip.modelData.solo ? root.acid : root.raised; border.color: mixerStrip.modelData.solo ? root.acid : root.border }
                                                ToolTip.visible: hovered
                                                ToolTip.text: "ソロ"
                                            }
                                        }
                                        CheckBox {
                                            objectName: "mixerChannelEnabledCheck"
                                            Layout.alignment: Qt.AlignHCenter
                                            text: "使用する"
                                            checked: Boolean(mixerStrip.modelData.enabled)
                                            enabled: !root.appBackend.running
                                            onToggled: mixerContent.updateMixerChannel(mixerStrip.index, {"enabled": checked})
                                        }
                                    }
                                }
                                ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
                            }
                            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.border }
                            RowLayout {
                                Layout.fillWidth: true
                                Text { Layout.fillWidth: true; text: "使用するトラック、ミュート、ソロ、音量をプレビューへ反映します。"; color: root.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 9; wrapMode: Text.WordWrap }
                                Text { text: "全体の音量"; color: root.textPrimary; font.family: "Yu Gothic UI"; font.pixelSize: 9; font.weight: Font.Bold }
                                Rectangle { objectName: "mixerMasterMeter"; Layout.preferredWidth: 120; Layout.preferredHeight: 9; radius: 4; color: "#070908"; border.color: root.border; Rectangle { anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.margins: 2; width: Math.max(0, (parent.width - 4) * Number(root.appBackend.audioMasterLevel || 0)); radius: 2; color: root.appBackend.audioLimiterReductionDb > 0.01 ? root.amber : root.acid; Behavior on width { NumberAnimation { duration: 45 } } } }
                                Text { objectName: "mixerLimiterReduction"; text: "自動調整 " + Number(root.appBackend.audioLimiterReductionDb || 0).toFixed(1) + " dB"; color: root.appBackend.audioLimiterReductionDb > 0.01 ? root.amber : root.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 9; Layout.preferredWidth: 100 }
                                Text { text: "音声出力: AAC / 48 kHz"; color: root.acid; font.family: "Yu Gothic UI"; font.pixelSize: 9 }
                            }
                        }
                    }
                }
            }
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
                Text { objectName: "editorStatusText"; Layout.fillWidth: true; Layout.minimumWidth: 80; text: root.userFacingStatusLabel(root.appBackend.stage, root.appBackend.status); color: root.appBackend.stage === "ERROR" ? root.danger : ((root.appBackend.stage === "CHECK" || root.appBackend.stage === "BUSY") ? root.amber : root.textMuted); font.family: "Yu Gothic UI"; font.pixelSize: 9; horizontalAlignment: Text.AlignRight; elide: Text.ElideRight }
                SmallButton { objectName: "undoCaptionButton"; text: "元に戻す"; enabled: root.appBackend.canUndo; onClicked: root.appBackend.undoSubtitleEdit() }
                SmallButton { objectName: "redoCaptionButton"; text: "やり直す"; enabled: root.appBackend.canRedo; onClicked: root.appBackend.redoSubtitleEdit() }
                SmallButton { objectName: "addCaptionButton"; text: "+ 字幕追加"; onClicked: root.appBackend.addSegment(editorPlayer.position / 1000) }
                SmallButton { objectName: "splitCaptionButton"; text: "分割"; enabled: root.canSplitSelectedSegment(editorPlayer.position); onClicked: root.appBackend.splitSelectedSegment(editorPlayer.position / 1000) }
                SmallButton { objectName: "deleteCaptionButton"; text: "削除"; enabled: root.appBackend.selectedSegmentIndex >= 0; onClicked: root.appBackend.deleteSelectedSegment() }
                SmallButton { objectName: "saveProjectButton"; text: "保存"; onClicked: root.appBackend.saveProject() }
                SmallButton { objectName: "buildAssButton"; text: "プレビューを更新"; onClicked: root.appBackend.buildSubtitlePreview(root.currentSettings()) }
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
                        SubtitleOverlay {
                            id: editorOverlay
                            anchors.fill: editorVideo
                            appBackend: root.appBackend
                            player: editorPlayer
                            captionObjectPrefix: "editorSubtitleOverlayCaption"
                            baseFontSize: root.selectedSubtitleFontSize
                            defaultSubtitleFontSize: root.defaultSubtitleFontSize
                            outlineColor: root.selectedSubtitleOutlineColor
                            outlineThickness: root.selectedSubtitleOutlineThickness
                            speakerColors: root.projectSpeakerCache
                            subtitleTextResolver: function(segmentData) { return root.subtitlePreviewText(segmentData) }
                        }
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
                        CodexEditPanel {
                            id: codexEditPanel
                            objectName: "codexEditPanel"
                            Layout.fillWidth: true
                            Layout.preferredHeight: implicitHeight
                            backend: root.appBackend
                            currentTime: editorPlayer.position / 1000
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
                                required property string editorText
                                required property string speaker
                                required property int layoutRow
                                required property real subtitleFontScale
                                required property string subtitleFontFamily
                                width: captionTable.width; height: 122; radius: 8
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
                                    TextArea {
                                        id: captionTextArea
                                        objectName: "captionTextArea"
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 52
                                        text: captionRow.editorText
                                        color: root.textPrimary; selectionColor: root.acid; font.family: captionRow.subtitleFontFamily || "Yu Gothic UI"; font.pixelSize: 12
                                        wrapMode: TextEdit.Wrap
                                        selectByMouse: true
                                        onTextChanged: {
                                            if (activeFocus)
                                                root.updateSubtitleDraft(captionRow.index, text)
                                        }
                                        onActiveFocusChanged: {
                                            if (activeFocus) {
                                                root.beginSubtitleDraft(captionRow.index, text)
                                            } else {
                                                var editedText = text
                                                if (editedText !== captionRow.editorText)
                                                    root.appBackend.updateSegment(captionRow.index, {"text": editedText})
                                                root.clearSubtitleDraft(captionRow.index)
                                            }
                                        }
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

                Rectangle {
                    objectName: "editorEmptyState"
                    anchors.centerIn: parent
                    width: 360
                    height: 112
                    visible: root.appBackend.segmentCount === 0
                    z: 10
                    radius: 10
                    color: "#17201B"
                    border.color: root.border
                    Column {
                        anchors.centerIn: parent
                        spacing: 8
                        Text { anchors.horizontalCenter: parent.horizontalCenter; text: "字幕がありません"; color: root.textPrimary; font.family: "Yu Gothic UI"; font.pixelSize: 16; font.weight: Font.Bold }
                        Text { anchors.horizontalCenter: parent.horizontalCenter; text: "上部の「+ 字幕追加」から手動で追加できます"; color: root.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 11 }
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
        id: shortModePage
        objectName: "shortModePage"
        anchors.fill: parent
        visible: root.shortMode
        z: 100
        color: "#0D1210"
        border.color: "#46564E"
        focus: visible
        Keys.onEscapePressed: root.closeShortModeScreen()
        onVisibleChanged: if (visible) forceActiveFocus()

        Loader {
            id: shortModeLoader
            anchors.fill: parent
            active: root.shortMode
            source: "ShortModeScreen.qml"
            onLoaded: shortModeLoader.item.mainRoot = root
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
    Shortcut { sequence: StandardKey.Save; enabled: root.editorMode || root.mixerMode; onActivated: root.appBackend.saveProject() }
    Shortcut { sequence: "Delete"; enabled: root.editorMode && root.appBackend.selectedSegmentIndex >= 0; onActivated: root.appBackend.deleteSelectedSegment() }

    Connections {
        target: root.appBackend
        function onSettingsChanged() { root.syncSettings() }
    }

    Component.onCompleted: {
        root.syncSettings()
    }
    onClosing: function(close) {
        if (root.appBackend.running && root.appBackend.activeJob === "update") {
            close.accepted = false
            return
        }
        root.appBackend.saveProject()
        mainPlayer.stop()
    }
}

