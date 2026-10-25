pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtMultimedia

Rectangle {
    id: root

    required property var appBackend
    required property MediaPlayer player
    required property var colors
    required property var layoutMetrics
    required property bool previewActive
    required property int baseFontSize
    required property int defaultSubtitleFontSize
    required property color outlineColor
    required property int outlineThickness
    required property var speakerColors
    required property var subtitleTextResolver
    required property var formatTimestamp
    property alias videoOutputItem: mainVideo

    signal seekRequested(real positionMs)
    signal selectionSyncRequested(var segments)

    function setPlayheadPosition(positionMs) {
        if (!mainSeek.pressed)
            mainSeek.value = positionMs
    }

    function setDuration(durationMs) {
        mainSeek.to = Math.max(1, durationMs)
    }

    objectName: "mainVideoPanel"
    radius: 12
    color: "#06080D"
    border.color: root.colors.border
    clip: true

    VideoOutput {
        id: mainVideo
        anchors.fill: parent
        anchors.bottomMargin: 58
        fillMode: VideoOutput.PreserveAspectFit
    }

    SubtitleOverlay {
        id: mainSubtitleOverlay
        anchors.fill: mainVideo
        appBackend: root.appBackend
        player: root.player
        layoutMetrics: root.layoutMetrics
        active: root.previewActive
        captionObjectPrefix: "mainSubtitleOverlayCaption"
        baseFontSize: root.baseFontSize
        defaultSubtitleFontSize: root.defaultSubtitleFontSize
        outlineColor: root.outlineColor
        outlineThickness: root.outlineThickness
        speakerColors: root.speakerColors
        subtitleTextResolver: root.subtitleTextResolver
        onActiveSegmentsChanged: root.selectionSyncRequested(mainSubtitleOverlay.activeSegments)
    }

    ColumnLayout {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 12
        spacing: 2

        Slider {
            id: mainSeek
            objectName: "mainPreviewSeekSlider"
            Layout.fillWidth: true
            from: 0
            to: 1
            onMoved: root.seekRequested(value)
        }
        RowLayout {
            Layout.fillWidth: true
            ToolButton {
                objectName: "mainPreviewPlayButton"
                text: root.player.playbackState === MediaPlayer.PlayingState ? "Ⅱ" : "▶"
                onClicked: root.player.playbackState === MediaPlayer.PlayingState
                    ? root.player.pause() : root.player.play()
            }
            Text {
                Layout.fillWidth: true
                text: root.appBackend.sourceSelection.video
                    ? root.appBackend.sourceSelection.video.split(/[\\/]/).pop() : "動画未選択"
                color: root.colors.textPrimary
                font.pixelSize: 11
                font.family: "Yu Gothic UI"
                elide: Text.ElideMiddle
            }
            Text {
                objectName: "mainPreviewTimeLabel"
                text: root.appBackend.workspace.cutTimeline.hasCuts
                    ? ("素材 " + root.formatTimestamp(root.player.position / 1000)
                        + "  出力 " + root.formatTimestamp(Number(root.appBackend.workspace.editorPlayhead.outputPositionMs) / 1000)
                        + " / " + root.formatTimestamp(root.appBackend.workspace.cutOutputDuration))
                    : root.formatTimestamp(root.player.position / 1000)
                        + " / " + root.formatTimestamp(root.player.duration / 1000)
                color: root.colors.textMuted
                font.pixelSize: 10
                font.family: "Cascadia Mono"
            }
        }
    }
}
