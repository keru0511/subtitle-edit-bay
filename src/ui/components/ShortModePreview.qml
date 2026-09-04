import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtMultimedia

Rectangle {
    id: previewRoot
    objectName: "shortModePreview"
    color: "#080A09"
    border.color: "#2A3530"
    radius: 12
    clip: true

    property var appBackend: null
    property var clipData: null
    property string fallbackBackgroundColor: "#000000"
    property string activeClipKey: ""
    readonly property var shortSettings: appBackend ? appBackend.shortVideoSettings : ({})
    readonly property var appSettings: appBackend ? appBackend.settings : ({})
    readonly property string previewSource: appBackend ? appBackend.previewUrl : ""

    function clipPlaybackKey(clip) {
        if (!clip) return ""
        return String(clip.index) + "|" + String(clip.segment_id || "") + "|"
            + String(Number(clip.start)) + "|" + String(Number(clip.end))
    }

    function syncClipPlayback(force) {
        var nextKey = previewRoot.clipPlaybackKey(previewRoot.clipData)
        if (!force && nextKey === previewRoot.activeClipKey) return
        previewRoot.activeClipKey = nextKey
        if (nextKey !== "" && previewRoot.previewSource !== "") {
            previewPlayer.position = previewRoot.clipData.start * 1000
            previewPlayer.play()
        } else {
            previewPlayer.stop()
        }
    }

    function normalizedSubtitleScalePercent() {
        var configuredScale = shortSettings ? shortSettings.subtitle_scale_percent : undefined
        if (configuredScale === undefined || configuredScale === null || configuredScale === "")
            return 150
        var numericScale = Number(configuredScale)
        return isFinite(numericScale) ? Math.max(0, numericScale) : 150
    }

    readonly property int subtitleBaseFontSize: Math.max(
        3,
        Math.round(
            Number(appSettings.subtitle_font_size || 50)
            * previewRoot.normalizedSubtitleScalePercent() / 100
        )
    )
    readonly property string subtitleOutlineColor: String(appSettings.subtitle_outline_color || "#000000")
    readonly property int subtitleOutlineThickness: Number(appSettings.subtitle_outline_thickness || 3)

    function previewAt(seconds) {
        previewPlayer.position = Math.max(0, Number(seconds)) * 1000
        previewPlayer.play()
    }

    Layout.fillHeight: true
    Layout.preferredWidth: parent ? parent.height * 9 / 16 : 540
    Layout.maximumWidth: 540
    Layout.minimumWidth: 200

    readonly property string backgroundColor: {
        if (!clipData || !clipData.background_color) return fallbackBackgroundColor
        return clipData.background_color.startsWith("#") ? clipData.background_color : "#" + clipData.background_color
    }

    MediaPlayer {
        id: previewPlayer
        source: previewRoot.previewSource
        videoOutput: previewVideo
        audioOutput: AudioOutput { volume: 0.7 }

        onPositionChanged: {
            if (!previewRoot.clipData) {
                return
            }
            var endMs = previewRoot.clipData.end * 1000
            if (position > endMs) {
                previewPlayer.position = previewRoot.clipData.start * 1000
            }
        }

        onPlaybackStateChanged: {
            if (playbackState === MediaPlayer.StoppedState && previewRoot.clipData && previewRoot.appBackend && previewRoot.appBackend.previewUrl) {
                previewPlayer.position = previewRoot.clipData.start * 1000
                previewPlayer.play()
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        color: previewRoot.backgroundColor
    }

    VideoOutput {
        id: previewVideo
        anchors.fill: parent
        fillMode: {
            if (!previewRoot.clipData) return VideoOutput.PreserveAspectCrop
            if (previewRoot.clipData.fit === "contain") return VideoOutput.PreserveAspectFit
            return VideoOutput.PreserveAspectCrop
        }
    }

    SubtitleOverlay {
        id: subtitleOverlay
        anchors.fill: previewVideo
        appBackend: previewRoot.appBackend
        player: previewPlayer
        layoutMetrics: previewRoot.appBackend ? previewRoot.appBackend.subtitleLayoutMetrics : ({})
        captionObjectPrefix: "shortSubtitleOverlayCaption"
        baseFontSize: previewRoot.subtitleBaseFontSize
        defaultSubtitleFontSize: 50
        outlineColor: previewRoot.subtitleOutlineColor
        outlineThickness: previewRoot.subtitleOutlineThickness
        speakerColors: previewRoot.appBackend ? previewRoot.appBackend.projectSpeakers : []
    }

    Text {
        anchors.fill: parent
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        color: "#8E9B94"
        font.family: "Yu Gothic UI"
        font.pixelSize: 12
        text: !previewRoot.appBackend || !previewRoot.appBackend.previewUrl ? "プレビューする動画が選択されていません" : ""
        visible: text !== ""
        wrapMode: Text.Wrap
    }

    onClipDataChanged: previewRoot.syncClipPlayback(false)
    onPreviewSourceChanged: previewRoot.syncClipPlayback(true)
}
