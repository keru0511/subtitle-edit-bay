pragma ComponentBehavior: Bound
import QtQuick
import "../components"

MainWorkflowScreen {
    id: screenRoot

    function selectedSubtitleSegment() {
        var index = screenRoot.appBackend.selectedSegmentIndex
        var segments = screenRoot.appBackend.subtitleSegments || []
        if (index < 0 || index >= segments.length)
            return ({})
        return segments[index]
    }

    TranscriptionContextPanel {
        id: contextPanel
        objectName: "mainTranscriptionContextPanel"
        width: Math.min(360, Math.max(300, screenRoot.width * 0.26))
        height: Math.min(336, Math.max(280, screenRoot.height - 150))
        anchors.right: parent.right
        anchors.rightMargin: 14
        anchors.top: parent.top
        anchors.topMargin: 76
        z: 90
        visible: !screenRoot.editorMode
            && !screenRoot.mixerMode
            && !screenRoot.appBackend.projectLoaded
        context: screenRoot.appBackend.transcriptionContext
        running: screenRoot.appBackend.running
        panelColor: screenRoot.panel
        raisedColor: screenRoot.raised
        borderColor: screenRoot.border
        textPrimaryColor: screenRoot.textPrimary
        textMutedColor: screenRoot.textMuted
        accentColor: screenRoot.acid
        onTranscriptionContextEdited: function(context) {
            screenRoot.appBackend.setTranscriptionContext(context)
        }
    }

    SubtitleLineCountPanel {
        id: lineCountPanel
        objectName: "editorSubtitleLineCountPanel"
        anchors.right: parent.right
        anchors.rightMargin: 24
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 24
        z: 210
        visible: screenRoot.editorMode && screenRoot.appBackend.projectLoaded
        selectedSegmentIndex: screenRoot.appBackend.selectedSegmentIndex
        segment: screenRoot.selectedSubtitleSegment()
        running: screenRoot.appBackend.running
        panelColor: screenRoot.panel
        raisedColor: screenRoot.raised
        borderColor: screenRoot.border
        textPrimaryColor: screenRoot.textPrimary
        textMutedColor: screenRoot.textMuted
        accentColor: screenRoot.acid
        onLineCountChanged: function(segmentIndex, lineCount) {
            screenRoot.appBackend.updateSegment(segmentIndex, {"subtitle_line_count": lineCount})
        }
    }
}
