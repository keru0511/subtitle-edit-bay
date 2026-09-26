pragma ComponentBehavior: Bound
import QtQuick
import QtQml.Models
import QtMultimedia

Item {
    id: root
    objectName: "mixerPreviewSession"

    required property var audioBackend
    required property real entryPosition
    property alias player: mixerPlayer

    signal positionUpdated(real positionMs)

    readonly property bool previewReady: !root.audioBackend.audioPreviewPreparing
        && root.audioBackend.audioMixerPreviewChannels.length > 0
    property real initialPosition: -1
    property int initialPositionStableTicks: 0
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
            root.audioBackend.pauseAudioMixerPreview()
            root.syncPreviewPlayers(false)
        } else {
            root.audioBackend.startAudioMixerPreview(mixerPlayer.position)
            mixerPlayer.play()
            root.syncPreviewPlayers(true)
        }
    }

    function seekTo(milliseconds) {
        mixerPlayer.position = Math.max(
            0,
            Math.min(mixerPlayer.duration, milliseconds)
        )
        root.audioBackend.seekAudioMixerPreview(
            mixerPlayer.position,
            mixerPlayer.playbackState === MediaPlayer.PlayingState
        )
        root.syncPreviewPlayers(true)
    }

    function seekBy(milliseconds) {
        root.seekTo(mixerPlayer.position + milliseconds)
    }

    MediaPlayer {
        id: mixerPlayer
        objectName: "mixerPlayer"
        source: root.previewReady ? root.audioBackend.audioPreviewClockUrl : ""
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
                root.audioBackend.pauseAudioMixerPreview()
        }
    }

    Instantiator {
        id: mixerPreviewPlayers
        objectName: "mixerPreviewPlayers"
        model: root.audioBackend.audioMixerPreviewChannels
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
}
