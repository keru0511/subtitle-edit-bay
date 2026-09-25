pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtMultimedia

Item {
    id: root
    required property var appBackend
    required property MediaPlayer player
    required property SubtitleEditorState editorState
    required property var colors
    required property var formatTimestamp
    signal seekRequested(real positionMs)
    signal previewRequested

    objectName: "workspaceSubtitleEditor"

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 6

        RowLayout {
            Layout.fillWidth: true
            spacing: 5
            SubtitleEditorButton {
                colors: root.colors
                objectName: "workspaceSubtitleUndoButton"
                text: "元に戻す"
                enabled: root.appBackend.subtitles.canUndo
                onClicked: root.appBackend.subtitles.undoSubtitleEdit()
            }
            SubtitleEditorButton {
                colors: root.colors
                objectName: "workspaceSubtitleRedoButton"
                text: "やり直す"
                enabled: root.appBackend.subtitles.canRedo
                onClicked: root.appBackend.subtitles.redoSubtitleEdit()
            }
            SubtitleEditorButton {
                colors: root.colors
                objectName: "workspaceSubtitleAddButton"
                text: "+ 字幕追加"
                enabled: !root.appBackend.running
                onClicked: root.appBackend.subtitles.addSegment(Number(root.appBackend.workspace.editorPlayhead.sourcePositionMs) / 1000)
            }
            SubtitleEditorButton {
                colors: root.colors
                objectName: "workspaceSubtitleSplitButton"
                text: "分割"
                enabled: !root.appBackend.running && root.editorState.canSplitSelectedSegment(root.appBackend.workspace.editorPlayhead.sourcePositionMs)
                onClicked: root.appBackend.subtitles.splitSelectedSegment(Number(root.appBackend.workspace.editorPlayhead.sourcePositionMs) / 1000)
            }
            SubtitleEditorButton {
                colors: root.colors
                objectName: "workspaceSubtitleDeleteButton"
                text: "削除"
                enabled: !root.appBackend.running && root.appBackend.subtitles.selectedSegmentIndex >= 0
                onClicked: root.appBackend.subtitles.deleteSelectedSegment()
            }
            Item {
                Layout.fillWidth: true
            }
            SubtitleEditorButton {
                colors: root.colors
                objectName: "workspaceSubtitleSaveButton"
                text: "保存"
                enabled: !root.appBackend.running
                onClicked: root.appBackend.saveProject()
            }
            SubtitleEditorButton {
                colors: root.colors
                objectName: "workspaceSubtitlePreviewButton"
                text: "プレビュー更新"
                enabled: !root.appBackend.running
                onClicked: root.previewRequested()
            }
        }

        SubtitleTimeline {
            id: workspaceSubtitleTimeline
            appBackend: root.appBackend
            colors: root.colors
            formatTimestamp: root.formatTimestamp
            speakers: root.appBackend.subtitles.projectSpeakers
            waveforms: root.appBackend.subtitles.subtitleWaveforms
            objectName: "workspaceSubtitleTimeline"
            property bool restoringViewport: true
            Layout.fillWidth: true
            Layout.fillHeight: true
            player: root.player
            pixelsPerSecond: root.editorState.pixelsPerSecond
            snapSeconds: root.editorState.snapMilliseconds / 1000
            editable: true
            seekHandler: function (positionMilliseconds) {
                root.seekRequested(positionMilliseconds);
            }
            onViewportXChanged: {
                if (!restoringViewport)
                    root.editorState.timelineScrollX = viewportX;
            }
            onSegmentActivated: function (index) {
                var segment = root.appBackend.subtitles.segmentAt(index);
                if (segment)
                    root.seekRequested(Number(segment.start) * 1000);
            }
            Timer {
                interval: 0
                running: true
                repeat: false
                onTriggered: {
                    workspaceSubtitleTimeline.viewportX = root.editorState.timelineScrollX;
                    workspaceSubtitleTimeline.restoringViewport = false;
                }
            }
        }
    }
}
