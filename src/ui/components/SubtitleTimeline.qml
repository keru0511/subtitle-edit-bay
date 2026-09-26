pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtMultimedia

Rectangle {
    id: timelineRoot
    required property var appBackend
    required property var colors
    required property var formatTimestamp
    property var speakers: []
    property MediaPlayer player
    property real pixelsPerSecond: 40
    property real snapSeconds: 0.1
    property int laneHeight: 42
    readonly property int rulerHeight: 28
    readonly property int laneInset: 3
    property bool editable: true
    property bool showSegments: true
    property bool showTrackVolume: false
    property var seekHandler: null
    property var lanes: timelineRoot.speakers
    property var waveforms: []
    property alias viewportX: timelineFlick.contentX
    property alias viewportY: timelineFlick.contentY
    property var visibleSegments: []
    property var visibleRulerTicks: []

    function refreshViewport() {
        if (timelineRoot.pixelsPerSecond <= 0)
            return;
        var pixels = timelineRoot.pixelsPerSecond;
        var padding = Math.max(2, timelineFlick.width / pixels * 0.25);
        var viewportStart = Math.max(0, timelineFlick.contentX / pixels - padding);
        var viewportEnd = (timelineFlick.contentX + timelineFlick.width) / pixels + padding;
        timelineRoot.visibleSegments = timelineRoot.showSegments ? timelineRoot.appBackend.subtitles.visibleSubtitleSegments(viewportStart, viewportEnd) : [];

        var ticks = [];
        var firstTick = Math.max(0, Math.floor(viewportStart / 10) * 10);
        var lastTick = Math.ceil(viewportEnd / 10) * 10;
        for (var tick = firstTick; tick <= lastTick; tick += 10)
            ticks.push(tick);
        timelineRoot.visibleRulerTicks = ticks;
    }

    function followPlaybackPosition(positionMs) {
        if (timelineRoot.pixelsPerSecond <= 0 || timelineFlick.width <= 0)
            return;
        var targetX = Math.max(0, Number(positionMs) / 1000 * timelineRoot.pixelsPerSecond);
        var viewportWidth = timelineFlick.width;
        var anchorX = viewportWidth * 0.35;
        var currentX = timelineFlick.contentX;
        var desiredX = currentX;
        if (targetX < currentX || targetX > currentX + viewportWidth)
            desiredX = targetX - anchorX;
        else if (timelineRoot.player && timelineRoot.player.playbackState === MediaPlayer.PlayingState && targetX > currentX + anchorX)
            desiredX = targetX - anchorX;
        var maximumX = Math.max(0, timelineFlick.contentWidth - viewportWidth);
        desiredX = Math.max(0, Math.min(maximumX, desiredX));
        if (Math.abs(desiredX - currentX) > 0.5)
            timelineFlick.contentX = desiredX;
    }

    function laneForItem(item) {
        var key = item && item.lane_id !== undefined ? item.lane_id : (item ? item.style : "");
        for (var index = 0; index < timelineRoot.lanes.length; ++index) {
            var lane = timelineRoot.lanes[index];
            var laneKey = lane && lane.lane_id !== undefined ? lane.lane_id : lane.style;
            if (laneKey === key)
                return index;
        }
        return 0;
    }

    onPixelsPerSecondChanged: timelineRoot.refreshViewport()
    signal segmentActivated(int index)
    Connections {
        target: timelineRoot.appBackend.subtitles
        function onSegmentsChanged() {
            Qt.callLater(timelineRoot.refreshViewport);
        }
    }
    Connections {
        target: timelineRoot.player
        function onPositionChanged() {
            if (timelineRoot.player)
                timelineRoot.followPlaybackPosition(timelineRoot.player.position);
        }
    }

    color: "#0E1311"
    border.color: timelineRoot.colors.border
    radius: 10
    clip: true

    Flickable {
        id: timelineFlick
        anchors.fill: parent
        anchors.leftMargin: 86
        clip: true
        interactive: true
        boundsBehavior: Flickable.StopAtBounds
        contentWidth: Math.max(width, timelineRoot.appBackend.projectDuration * timelineRoot.pixelsPerSecond + 120)
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
                onClicked: function (mouse) {
                    var position = Math.max(0, mouse.x / timelineRoot.pixelsPerSecond * 1000);
                    if (timelineRoot.seekHandler)
                        timelineRoot.seekHandler(position);
                    else if (timelineRoot.player)
                        timelineRoot.player.position = position;
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
                    Rectangle {
                        anchors.fill: parent
                        color: "#26302B"
                    }
                    Text {
                        x: 4
                        y: 3
                        text: timelineRoot.formatTimestamp(rulerTick.modelData)
                        color: timelineRoot.colors.textMuted
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
                    color: modelData.color || timelineRoot.colors.amber
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
                        property color waveformColor: waveDelegate.modelData.color || timelineRoot.colors.amber
                        onPeaksChanged: requestPaint()
                        onWaveformColorChanged: requestPaint()
                        onWidthChanged: requestPaint()
                        onHeightChanged: requestPaint()
                        onPaint: {
                            var context = getContext("2d");
                            context.clearRect(0, 0, width, height);
                            if (peaks.length === 0)
                                return;
                            context.fillStyle = waveformColor;
                            var step = width / peaks.length;
                            var barWidth = Math.max(1, step - 0.5);
                            for (var i = 0; i < peaks.length; ++i) {
                                var barHeight = Math.max(1, Number(peaks[i]) * height);
                                context.fillRect(i * step, (height - barHeight) / 2, barWidth, barHeight);
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
                    objectName: "timelineCaption-" + sourceIndex
                    // visibleSubtitleSegments returns a flat segment view. Keep
                    // compatibility with explicitly wrapped diagnostic data.
                    property var segment: modelData && modelData.segment ? modelData.segment : (modelData || ({}))
                    property real originalX: 0
                    property real originalWidth: 0
                    property real pointerStart: 0
                    visible: sourceIndex >= 0 && segment.start !== undefined && segment.end !== undefined
                    x: Number(segment.start || 0) * timelineRoot.pixelsPerSecond
                    y: timelineRoot.rulerHeight + timelineRoot.laneForStyle(segment.speaker || "") * timelineRoot.laneHeight + timelineRoot.laneInset
                    width: Math.max(10, (Number(segment.end || 0) - Number(segment.start || 0)) * timelineRoot.pixelsPerSecond)
                    height: timelineRoot.laneHeight - timelineRoot.laneInset * 2 - 1
                    radius: 6
                    color: timelineRoot.speakerColor(segment.speaker || "")
                    opacity: timelineRoot.appBackend.subtitles.selectedSegmentIndex === sourceIndex ? 1 : 0.78
                    border.color: timelineRoot.appBackend.subtitles.selectedSegmentIndex === sourceIndex ? timelineRoot.colors.textPrimary : "#66101010"
                    border.width: timelineRoot.appBackend.subtitles.selectedSegmentIndex === sourceIndex ? 2 : 1

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
                            timelineRoot.appBackend.subtitles.selectSegment(captionClip.sourceIndex);
                            timelineRoot.segmentActivated(captionClip.sourceIndex);
                        }
                        onReleased: timelineRoot.appBackend.subtitles.moveSegment(captionClip.sourceIndex, captionClip.x / timelineRoot.pixelsPerSecond, (captionClip.x + captionClip.width) / timelineRoot.pixelsPerSecond, timelineRoot.snapSeconds)
                    }

                    Rectangle {
                        id: leftHandle
                        objectName: "timelineCaptionStartHandle"
                        z: 3
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        width: 7
                        height: parent.height
                        radius: 3
                        color: "#EEFFFFFF"
                        visible: timelineRoot.editable && timelineRoot.appBackend.subtitles.selectedSegmentIndex === captionClip.sourceIndex
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.SizeHorCursor
                            preventStealing: true
                            onPressed: function (mouse) {
                                captionClip.originalX = captionClip.x;
                                captionClip.originalWidth = captionClip.width;
                                captionClip.pointerStart = mapToItem(timelineCanvas, mouse.x, mouse.y).x;
                            }
                            onPositionChanged: function (mouse) {
                                if (!pressed)
                                    return;
                                var pointer = mapToItem(timelineCanvas, mouse.x, mouse.y).x;
                                var delta = Math.min(captionClip.originalWidth - 4, pointer - captionClip.pointerStart);
                                captionClip.x = Math.max(0, captionClip.originalX + delta);
                                captionClip.width = captionClip.originalWidth - (captionClip.x - captionClip.originalX);
                            }
                            onReleased: timelineRoot.appBackend.subtitles.resizeSegmentStart(captionClip.sourceIndex, captionClip.x / timelineRoot.pixelsPerSecond, timelineRoot.snapSeconds)
                        }
                    }

                    Rectangle {
                        id: rightHandle
                        objectName: "timelineCaptionEndHandle"
                        z: 3
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        width: 7
                        height: parent.height
                        radius: 3
                        color: "#EEFFFFFF"
                        visible: timelineRoot.editable && timelineRoot.appBackend.subtitles.selectedSegmentIndex === captionClip.sourceIndex
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.SizeHorCursor
                            preventStealing: true
                            onPressed: function (mouse) {
                                captionClip.originalWidth = captionClip.width;
                                captionClip.pointerStart = mapToItem(timelineCanvas, mouse.x, mouse.y).x;
                            }
                            onPositionChanged: function (mouse) {
                                if (!pressed)
                                    return;
                                var pointer = mapToItem(timelineCanvas, mouse.x, mouse.y).x;
                                captionClip.width = Math.max(4, captionClip.originalWidth + pointer - captionClip.pointerStart);
                            }
                            onReleased: timelineRoot.appBackend.subtitles.resizeSegmentEnd(captionClip.sourceIndex, (captionClip.x + captionClip.width) / timelineRoot.pixelsPerSecond, timelineRoot.snapSeconds)
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
                color: timelineRoot.colors.acid
                Rectangle {
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: 9
                    height: 9
                    radius: 5
                    color: timelineRoot.colors.acid
                }
            }
        }
        ScrollBar.horizontal: ScrollBar {
            policy: ScrollBar.AlwaysOn
        }
        ScrollBar.vertical: ScrollBar {
            policy: ScrollBar.AsNeeded
        }
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
                border.color: timelineRoot.colors.border
                Row {
                    anchors.centerIn: parent
                    spacing: 6
                    Rectangle {
                        width: 7
                        height: 22
                        radius: 3
                        color: laneLabel.modelData.color
                    }
                    Text {
                        width: 62
                        text: laneLabel.modelData.name
                        color: timelineRoot.colors.textPrimary
                        font.family: "Yu Gothic UI"
                        font.pixelSize: 10
                        elide: Text.ElideRight
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
            }
        }
    }
    function speakerColor(style) {
        var speakers = timelineRoot.speakers;
        for (var i = 0; i < speakers.length; ++i) {
            if (speakers[i].style === style)
                return speakers[i].color;
        }
        return timelineRoot.colors.amber;
    }

    function laneForStyle(style) {
        var speakers = timelineRoot.speakers;
        for (var i = 0; i < speakers.length; ++i) {
            if (speakers[i].style === style)
                return i;
        }
        return 0;
    }
}
