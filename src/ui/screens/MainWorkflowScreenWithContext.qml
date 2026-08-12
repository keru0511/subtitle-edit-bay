pragma ComponentBehavior: Bound
import QtQuick
import "../components"

MainWorkflowScreen {
    id: screenRoot

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
}
