pragma ComponentBehavior: Bound
import QtQuick

QtObject {
    id: root

    required property var appBackend
    required property var workflowCapabilities
    required property var settingsProvider
    readonly property string transcriptionBlockReason: {
        if (!root.appBackend.sourceSelection.video || !root.hasTranscriptionAudio())
            return "";
        return root.workflowCapabilities.canTranscribe ? "" : String(root.workflowCapabilities.transcriptionReason || "");
    }

    signal sourceSettingsRequested
    signal overwriteConfirmationRequested(var request)

    function hasTranscriptionAudio() {
        if (root.appBackend.speakers.length > 0)
            return true;
        for (var index = 0; index < root.appBackend.audioTracks.length; ++index) {
            if (String(root.appBackend.audioTracks[index].selector || "").length > 0)
                return true;
        }
        return false;
    }

    function startNewVideoEdit() {
        if (!root.appBackend.sourceSelection.video)
            root.appBackend.browseVideoFile();
        if (!root.appBackend.sourceSelection.video || root.appBackend.projectLoaded)
            return;
        if (root.appBackend.transcriptionProjectExists())
            root.appBackend.loadProject(root.appBackend.projectSavePath);
        else
            root.appBackend.createEmptyProject();
    }

    function startTranscription() {
        var executionSettings = JSON.parse(JSON.stringify(root.settingsProvider()));
        if (!root.appBackend.sourceSelection.video || !root.hasTranscriptionAudio()) {
            root.sourceSettingsRequested();
            return;
        }
        if (!root.workflowCapabilities.canTranscribe)
            return;
        if (!root.appBackend.projectLoaded && root.appBackend.transcriptionProjectExists()) {
            root.overwriteConfirmationRequested({
                "settings": executionSettings,
                "sources": JSON.parse(JSON.stringify(root.appBackend.sourceSelection)),
                "projectPath": String(root.appBackend.projectSavePath)
            });
            return;
        }
        if (!root.appBackend.projectLoaded && !root.appBackend.createEmptyProject())
            return;
        root.appBackend.workflow.startTranscription(executionSettings, true);
    }
}
