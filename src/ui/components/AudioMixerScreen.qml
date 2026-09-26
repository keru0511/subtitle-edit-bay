pragma ComponentBehavior: Bound
import QtQuick
import QtQml.Models
import QtQuick.Controls
import QtQuick.Layouts
import QtMultimedia

Item {
    id: root
    objectName: "mixerContent"

    required property var appBackend
    required property var colors
    required property var formatTimestamp
    required property var speakers
    required property real entryPosition
    required property real timelinePixelsPerSecond
    required property bool canRender
    property real headerRightInset: 0

    signal positionUpdated(real positionMs)
    signal subtitleEditorRequested()
    signal renderRequested()
    signal saveRequested()
    signal closeRequested()

    component MixerButton: SmallButton {
        textPrimary: root.colors.textPrimary
        disabledText: "#59635D"
        borderColor: root.colors.border
        focusColor: root.colors.acid
        defaultBackground: "#1A211E"
        hoverBackground: "#27312C"
        pressedBackground: "#303B35"
    }

    readonly property bool previewReady: !root.appBackend.audio.audioPreviewPreparing
        && root.appBackend.audio.audioMixerPreviewChannels.length > 0
    property real initialPosition: -1
    property int initialPositionStableTicks: 0
    property real channelScrollPosition: 0
    property bool restoringChannelScroll: false
    Component.onCompleted: {
        initialPosition = root.entryPosition
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
            root.syncPreviewPlayer(mixerPreviewPlayers.objectAt(index), forcePosition)
    }

    function togglePlayback() {
        if (mixerPlayer.playbackState === MediaPlayer.PlayingState) {
            mixerPlayer.pause()
            root.appBackend.audio.pauseAudioMixerPreview()
            root.syncPreviewPlayers(false)
        } else {
            root.appBackend.audio.startAudioMixerPreview(mixerPlayer.position)
            mixerPlayer.play()
            root.syncPreviewPlayers(true)
        }
    }

    function seekTo(milliseconds) {
        mixerPlayer.position = Math.max(
            0,
            Math.min(mixerPlayer.duration, milliseconds)
        )
        root.appBackend.audio.seekAudioMixerPreview(
            mixerPlayer.position,
            mixerPlayer.playbackState === MediaPlayer.PlayingState
        )
        root.syncPreviewPlayers(true)
    }

    function seekBy(milliseconds) {
        root.seekTo(mixerPlayer.position + milliseconds)
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
        root.appBackend.audio.updateAudioMixChannel(index, changes)
    }

    MediaPlayer {
        id: mixerPlayer
        objectName: "mixerPlayer"
        source: root.previewReady ? root.appBackend.audio.audioPreviewClockUrl : ""
        audioOutput: AudioOutput { muted: true }
        Component.onCompleted: root.restoreInitialPosition()
        onSeekableChanged: root.restoreInitialPosition()
        onDurationChanged: root.restoreInitialPosition()
        onMediaStatusChanged: root.restoreInitialPosition()
        onPositionChanged: {
            root.positionUpdated(mixerPlayer.position)
            if (mixerPlayer.playbackState !== MediaPlayer.PlayingState)
                root.syncPreviewPlayers(true)
        }
        onPlaybackStateChanged: {
            root.syncPreviewPlayers(false)
            if (mixerPlayer.playbackState === MediaPlayer.StoppedState)
                root.appBackend.audio.pauseAudioMixerPreview()
        }
    }

    Instantiator {
        id: mixerPreviewPlayers
        objectName: "mixerPreviewPlayers"
        model: root.appBackend.audio.audioMixerPreviewChannels
        delegate: MediaPlayer {
            id: mixerPreviewPlayer
            required property int index
            required property var modelData
            property string previewChannelId: String(modelData.id || "")
            objectName: "mixerPreviewPlayer-" + String(modelData.preview_object_id || previewChannelId)
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
            root.syncPreviewPlayer(object, true)
        }
    }

    Timer {
        interval: 50
        running: root.previewReady && root.initialPosition >= 0
        repeat: true
        onTriggered: root.restoreInitialPosition(true)
    }

    Timer {
        interval: 150
        running: mixerPlayer.playbackState === MediaPlayer.PlayingState
        repeat: true
        onTriggered: root.syncPreviewPlayers(false)
    }

    Timer {
        id: mixerChannelScrollRestoreTimer
        interval: 0
        repeat: false
        onTriggered: root.restoreChannelScroll()
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 64
            Layout.leftMargin: 18
            Layout.rightMargin: 14 + root.headerRightInset
            spacing: 10
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1
                Text { text: "音量ミキサー"; color: root.colors.textPrimary; font.family: "Yu Gothic UI"; font.pixelSize: 19; font.weight: Font.Bold; font.letterSpacing: 1.0 }
                Text { text: "動画内トラックと個別音声を、完成動画用にミックス"; color: root.colors.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 9 }
            }
            Text { text: root.appBackend.projectDirty ? "● 保存待ち" : "✓ 保存済み"; color: root.appBackend.projectDirty ? root.colors.amber : root.colors.acid; font.family: "Yu Gothic UI"; font.pixelSize: 9 }
            Text {
                objectName: "mixerAudioPreviewCacheSummary"
                text: root.appBackend.audio.audioPreviewPreparing ? "プレビューを準備中" : "プレビュー準備済み"
                color: root.colors.textMuted
                font.family: "Cascadia Mono"
                font.pixelSize: 9
            }
            MixerButton {
                objectName: "mixerClearAudioPreviewCacheButton"
                text: "プレビューを作り直す"
                enabled: !root.appBackend.running
                onClicked: {
                    root.appBackend.audio.clearAudioPreviewCache()
                    root.appBackend.audio.prepareAudioMixerPreview()
                }
            }
            MixerButton { objectName: "mixerResetButton"; text: "すべての音声トラックをリセット"; enabled: !root.appBackend.running; onClicked: root.appBackend.audio.resetAudioMixer() }
            MixerButton { objectName: "mixerSaveButton"; text: "保存"; enabled: !root.appBackend.running; onClicked: root.saveRequested() }
            MixerButton { objectName: "mixerToEditorButton"; text: "字幕編集へ"; enabled: !root.appBackend.running; onClicked: root.subtitleEditorRequested() }
            Button {
                id: mixerRenderButton
                objectName: "mixerRenderButton"
                implicitHeight: 34
                text: root.appBackend.workflow.activeJob === "render" ? "書き出し中..." : "動画を書き出す"
                enabled: root.canRender
                onClicked: {
                    root.renderRequested()
                }
                contentItem: Text { text: mixerRenderButton.text; color: mixerRenderButton.enabled ? "#10140F" : "#68716B"; font.family: "Yu Gothic UI"; font.pixelSize: 10; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { radius: 7; color: mixerRenderButton.enabled ? root.colors.acid : "#252C28" }
            }
            MixerButton { objectName: "mixerBackButton"; text: "メインへ戻る"; onClicked: root.closeRequested() }
        }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.colors.border }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(230, Math.max(174, 174 + (root.height - 760) * 0.31))
            Layout.leftMargin: 14
            Layout.rightMargin: 14
            Layout.topMargin: 10
            radius: 10
            color: root.colors.panel
            border.color: root.colors.border

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
                        enabled: root.previewReady
                        onClicked: root.togglePlayback()
                        contentItem: Text {
                            text: mixerPlayer.playbackState === MediaPlayer.PlayingState ? "Ⅱ" : "▶"
                            color: mixerPlayButton.enabled ? "#10140F" : root.colors.textMuted
                            font.pixelSize: 14
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle { radius: 7; color: mixerPlayButton.enabled ? root.colors.acid : "#252C28" }
                    }
                    MixerButton { objectName: "mixerRewindButton"; text: "−5秒"; enabled: root.previewReady; onClicked: root.seekBy(-5000) }
                    Slider {
                        id: mixerSeek
                        objectName: "mixerSeek"
                        Layout.fillWidth: true
                        from: 0
                        to: Math.max(1, mixerPlayer.duration)
                        value: mixerPlayer.position
                        enabled: root.previewReady
                        onMoved: root.seekTo(value)
                    }
                    MixerButton { objectName: "mixerForwardButton"; text: "+5秒"; enabled: root.previewReady; onClicked: root.seekBy(5000) }
                    Text {
                        objectName: "mixerTimeText"
                        Layout.preferredWidth: 142
                        text: root.formatTimestamp(mixerPlayer.position / 1000) + " / " + root.formatTimestamp(mixerPlayer.duration / 1000)
                        color: root.colors.textPrimary
                        font.family: "Cascadia Mono"
                        font.pixelSize: 10
                        horizontalAlignment: Text.AlignRight
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    PanelTitle { titleColor: root.colors.textMuted; text: "プレビュー" }
                    Text { text: root.appBackend.audio.audioPreviewPreparing ? "プレビュー音声を準備中…" : "出力音ライブプレビュー"; color: root.appBackend.audio.audioPreviewPreparing ? root.colors.amber : root.colors.acid; font.family: "Yu Gothic UI"; font.pixelSize: 9 }
                    Item { Layout.fillWidth: true }
                    Text { text: "クリックで再生位置を移動"; color: root.colors.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 8 }
                }
                SubtitleTimeline {
                    appBackend: root.appBackend
                    colors: root.colors
                    formatTimestamp: root.formatTimestamp
                    speakers: root.speakers
                    objectName: "mixerSequence"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    player: mixerPlayer
                    pixelsPerSecond: root.timelinePixelsPerSecond
                    laneHeight: 42
                    editable: false
                    showSegments: false
                    showTrackVolume: true
                    lanes: root.appBackend.audio.audioMixerSequenceChannels
                    waveforms: root.appBackend.audio.audioMixerSequenceChannels
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: 14
            radius: 12
            color: "#090C0B"
            border.color: root.colors.border

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    PanelTitle { titleColor: root.colors.textMuted; text: "音声トラック" }
                    Text { text: root.appBackend.audio.audioMixerChannels.length + "トラック"; color: root.colors.acid; font.family: "Yu Gothic UI"; font.pixelSize: 10 }
                    Item { Layout.fillWidth: true }
                    Text { text: "音量: −60〜+6 dB / ミュート / ソロ"; color: root.colors.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 9 }
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
                    model: root.appBackend.audio.audioMixerChannels
                    onContentXChanged: {
                        if (!root.restoringChannelScroll && !mixerChannelScrollRestoreTimer.running)
                            root.channelScrollPosition = contentX
                    }
                    delegate: AudioMixerChannelStrip {
                        id: mixerStrip
                        height: mixerChannelList.height - 12
                        colors: root.colors
                        running: root.appBackend.running
                        previewLevels: root.appBackend.audio.audioPreviewLevels
                        onChangeRequested: function(changes) {
                            root.updateMixerChannel(mixerStrip.index, changes)
                        }
                    }
                    ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
                }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.colors.border }
                RowLayout {
                    Layout.fillWidth: true
                    Text { Layout.fillWidth: true; text: "使用するトラック、ミュート、ソロ、音量をプレビューへ反映します。"; color: root.colors.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 9; wrapMode: Text.WordWrap }
                    Text { text: "全体の音量"; color: root.colors.textPrimary; font.family: "Yu Gothic UI"; font.pixelSize: 9; font.weight: Font.Bold }
                    Rectangle { objectName: "mixerMasterMeter"; Layout.preferredWidth: 120; Layout.preferredHeight: 9; radius: 4; color: "#070908"; border.color: root.colors.border; Rectangle { anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.margins: 2; width: Math.max(0, (parent.width - 4) * Number(root.appBackend.audio.audioMasterLevel || 0)); radius: 2; color: root.appBackend.audio.audioLimiterReductionDb > 0.01 ? root.colors.amber : root.colors.acid; Behavior on width { NumberAnimation { duration: 45 } } } }
                    Text { objectName: "mixerLimiterReduction"; text: "自動調整 " + Number(root.appBackend.audio.audioLimiterReductionDb || 0).toFixed(1) + " dB"; color: root.appBackend.audio.audioLimiterReductionDb > 0.01 ? root.colors.amber : root.colors.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 9; Layout.preferredWidth: 100 }
                    Text { text: "音声出力: AAC / 48 kHz"; color: root.colors.acid; font.family: "Yu Gothic UI"; font.pixelSize: 9 }
                }
            }
        }
    }
}
