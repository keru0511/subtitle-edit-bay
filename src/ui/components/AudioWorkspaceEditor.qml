pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtMultimedia

Item {
    id: root
    objectName: "workspaceAudioEditor"

    required property var appBackend
    required property MediaPlayer player
    required property var previewBridge
    required property var colors
    required property var formatTimestamp
    required property var speakers
    required property real pixelsPerSecond
    required property real savedViewportX

    signal seekRequested(real positionMs)
    signal viewportChangedByUser(real viewportX)

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 6

        RowLayout {
            Layout.fillWidth: true
            PanelTitle { titleColor: root.colors.textMuted; text: "音声タイムライン" }
            Text {
                objectName: "workspaceAudioPreviewStatus"
                text: root.appBackend.audio.audioPreviewPreparing
                    ? "プレビュー音声を準備中…"
                    : (root.previewBridge.intentionalSilence
                        ? "すべての音声トラックが無効です"
                        : (root.previewBridge.previewReady
                            ? "共通プレビューへ接続済み"
                            : "ミックスを準備できないため元の音声を再生します"))
                color: root.previewBridge.previewReady
                    || root.previewBridge.intentionalSilence
                    ? root.colors.acid
                    : root.colors.textMuted
                font.family: "Yu Gothic UI"
                font.pixelSize: 9
            }
            Item { Layout.fillWidth: true }
            Text { text: "再生・シークは中央プレビューと共通"; color: root.colors.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 8 }
        }

        SubtitleTimeline {
            appBackend: root.appBackend
            colors: root.colors
            formatTimestamp: root.formatTimestamp
            speakers: root.speakers
            id: workspaceAudioTimeline
            objectName: "workspaceAudioTimeline"
            property bool restoringViewport: true
            Layout.fillWidth: true
            Layout.fillHeight: true
            player: root.player
            pixelsPerSecond: root.pixelsPerSecond
            laneHeight: 34
            editable: false
            showSegments: false
            showTrackVolume: true
            lanes: root.appBackend.audio.audioMixerSequenceChannels
            waveforms: root.appBackend.audio.audioMixerSequenceChannels
            seekHandler: function(positionMilliseconds) {
                root.seekRequested(positionMilliseconds)
            }
            onViewportXChanged: {
                if (!restoringViewport)
                    root.viewportChangedByUser(viewportX)
            }
            Timer {
                interval: 0
                running: true
                repeat: false
                onTriggered: {
                    workspaceAudioTimeline.viewportX = root.savedViewportX
                    workspaceAudioTimeline.restoringViewport = false
                }
            }
        }
    }
}
