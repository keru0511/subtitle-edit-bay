pragma ComponentBehavior: Bound
import QtQuick
import QtMultimedia

Item {
    id: root
    objectName: "workspaceAudioPreviewBridge"

    property var backend: null
    property var player: null
    property bool active: false
    property bool prepared: false
    property int preparedGeneration: -1
    property int seekRevision: 0
    readonly property bool previewReady: Boolean(root.backend)
        && !root.backend.audioPreviewPreparing
        && root.backend.audioMixerPreviewChannels.length > 0

    function isPlaying() {
        return root.player
            && root.player.playbackState === MediaPlayer.PlayingState
    }

    function syncPreviewPlayer(previewPlayer, forcePosition) {
        if (!root.player || !previewPlayer)
            return
        var target = Number(root.player.position)
            - Number(previewPlayer.previewOffsetMilliseconds || 0)
        if (target < 0) {
            previewPlayer.scheduleSync(0, true, false)
            return
        }
        if (previewPlayer.duration > 0 && target >= previewPlayer.duration) {
            previewPlayer.scheduleSync(previewPlayer.duration, true, false)
            return
        }
        previewPlayer.scheduleSync(
            target,
            forcePosition,
            root.active && root.isPlaying()
        )
    }

    function syncPreviewPlayers(forcePosition) {
        for (var index = 0; index < previewPlayers.count; ++index)
            root.syncPreviewPlayer(previewPlayers.objectAt(index), forcePosition)
    }

    function syncPlaybackState() {
        if (!root.active || !root.backend || !root.player || !root.previewReady)
            return
        if (root.isPlaying())
            root.backend.startAudioMixerPreview(Math.round(root.player.position))
        else
            root.backend.pauseAudioMixerPreview()
        root.syncPreviewPlayers(true)
    }

    function schedulePlaybackSync() {
        playbackSyncTimer.restart()
    }

    function activatePreview() {
        if (!root.active || !root.backend)
            return
        var generation = Number(root.backend.audioPreviewGeneration)
        if (!root.prepared || root.preparedGeneration !== generation) {
            root.prepared = true
            root.preparedGeneration = generation
            root.backend.prepareAudioMixerPreview()
        }
        root.schedulePlaybackSync()
    }

    Component.onCompleted: if (root.active) root.activatePreview()
    Component.onDestruction: {
        if (root.backend)
            root.backend.stopAudioMixerPreview()
    }
    onActiveChanged: {
        if (active) {
            root.activatePreview()
        } else if (root.backend) {
            root.backend.stopAudioMixerPreview()
            root.syncPreviewPlayers(true)
        }
    }
    onPreviewReadyChanged: {
        if (!active || !backend)
            return
        if (previewReady) {
            root.schedulePlaybackSync()
        } else {
            backend.pauseAudioMixerPreview()
            root.syncPreviewPlayers(true)
        }
    }
    onSeekRevisionChanged: {
        if (!root.active || !root.backend || !root.player || !root.previewReady)
            return
        root.backend.seekAudioMixerPreview(
            Math.round(root.player.position),
            root.isPlaying()
        )
        root.syncPreviewPlayers(true)
    }

    Connections {
        target: root.player
        enabled: root.active && root.player !== null

        function onPlaybackStateChanged() {
            root.syncPlaybackState()
        }
    }

    Connections {
        target: root.backend
        enabled: root.backend !== null

        function onAudioPreviewCacheChanged() {
            if (root.active)
                root.activatePreview()
        }
    }

    Timer {
        id: playbackSyncTimer
        interval: 0
        repeat: false
        onTriggered: root.syncPlaybackState()
    }

    Instantiator {
        id: previewPlayers
        objectName: "workspaceAudioPreviewPlayers"
        model: root.prepared && root.backend ? root.backend.audioMixerPreviewChannels : []
        delegate: MediaPlayer {
            id: previewPlayer
            required property int index
            required property var modelData
            property string previewChannelId: String(modelData.id || "")
            property real previewOffsetMilliseconds: Number(modelData.preview_offset_seconds || 0) * 1000
            property int requestedAudioTrack: Number(modelData.preview_audio_track_index || 0)
            property real pendingSyncPosition: 0
            property bool pendingForcePosition: false
            property bool pendingPlayback: false
            property bool hasPendingSync: false

            objectName: "workspaceAudioPreviewPlayer-" + previewChannelId
            source: modelData.preview_url || ""
            audioOutput: AudioOutput { muted: true }
            // The backend owns and exposes one buffer output per channel.
            // qmllint disable missing-type
            audioBufferOutput: modelData.preview_buffer_output
            // qmllint enable missing-type

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

            onTracksChanged: applyPendingSync()
            onSeekableChanged: applyPendingSync()
            onMediaStatusChanged: applyPendingSync()
        }
        onObjectAdded: function(index, object) {
            root.syncPreviewPlayer(object, true)
        }
    }

    Timer {
        interval: 150
        running: root.active && root.previewReady && root.isPlaying()
        repeat: true
        onTriggered: root.syncPreviewPlayers(false)
    }
}
