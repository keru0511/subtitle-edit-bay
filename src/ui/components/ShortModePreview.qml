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

    Layout.fillHeight: true
    Layout.preferredWidth: parent ? parent.height * 9 / 16 : 540
    Layout.maximumWidth: 540
    Layout.minimumWidth: 200

    readonly property string backgroundColor: {
        if (!clip || !clip.background_color) return fallbackBackgroundColor
        return clip.background_color.startsWith("#") ? clip.background_color : "#" + clip.background_color
    }

    MediaPlayer {
        id: previewPlayer
        source: previewRoot.appBackend ? previewRoot.appBackend.previewUrl : ""
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

    onClipChanged: {
        if (clip && previewRoot.appBackend && previewRoot.appBackend.previewUrl) {
            previewPlayer.stop()
            previewPlayer.position = clip.start * 1000
            previewPlayer.play()
        } else {
            previewPlayer.stop()
        }
    }
}
