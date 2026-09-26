import QtQuick

QtObject {
    id: state
    required property var subtitles
    property bool previewEnabled: false
    property real pixelsPerSecond: 64
    property int snapMilliseconds: 100
    property real positionMs: 0
    property real timelineScrollX: 0
    property real captionScrollY: 0
    property int draftSegmentIndex: -1
    property string draftText: ""
    property string projectPath: ""
    property string draftSegmentId: ""
    property string draftProjectPath: ""
    property string draftOriginalText: ""
    readonly property bool hasPendingSubtitleText: draftSegmentId !== "" && draftText !== draftOriginalText

    function canSplitSelectedSegment(positionMs) {
        var index = state.subtitles.selectedSegmentIndex;
        var segmentCount = state.subtitles.segmentCount;
        if (index < 0 || index >= segmentCount)
            return false;
        var segment = state.subtitles.segmentAt(index);
        var seconds = Number(positionMs) / 1000;
        return seconds > Number(segment.start) + 0.05 && seconds < Number(segment.end) - 0.05;
    }

    function subtitleIndexForId(segmentId, preferredIndex) {
        if (!segmentId)
            return -1
        if (preferredIndex >= 0
                && String(state.subtitles.segmentAt(preferredIndex).id || "") === segmentId)
            return preferredIndex
        for (var index = 0; index < state.subtitles.segmentCount; ++index) {
            if (String(state.subtitles.segmentAt(index).id || "") === segmentId)
                return index
        }
        return -1
    }

    function beginSubtitleDraft(segmentIndex, text) {
        var segment = state.subtitles.segmentAt(segmentIndex)
        state.draftSegmentIndex = segmentIndex
        state.draftSegmentId = String(segment.id || "")
        state.draftProjectPath = state.projectPath
        state.draftOriginalText = String(text)
        state.draftText = String(text)
    }

    function updateSubtitleDraft(segmentIndex, text) {
        if (state.draftSegmentId !== "")
            state.draftText = String(text)
    }

    function clearSubtitleDraft() {
        state.draftSegmentIndex = -1
        state.draftSegmentId = ""
        state.draftProjectPath = ""
        state.draftOriginalText = ""
        state.draftText = ""
    }

    function commitSubtitleDraft(expectedId) {
        if (expectedId !== undefined && expectedId !== state.draftSegmentId)
            return
        var id = state.draftSegmentId
        var projectPath = state.draftProjectPath
        var preferredIndex = state.draftSegmentIndex
        var text = state.draftText
        var changed = state.hasPendingSubtitleText
        // モデル更新・フォーカス通知が再入しても同じ入力を二度反映しない。
        state.clearSubtitleDraft()
        if (!changed || projectPath !== state.projectPath)
            return
        var index = state.subtitleIndexForId(id, preferredIndex)
        if (index < 0)
            return // 削除済みの字幕の本文を、同じ行に移動した別の字幕へ反映しない。
        var selectedIndex = state.subtitles.selectedSegmentIndex
        var selectedId = String(state.subtitles.segmentAt(selectedIndex).id || "")
        state.subtitles.updateSegment(index, {"text": text})
        if (selectedId !== id)
            state.subtitles.selectSegment(state.subtitleIndexForId(selectedId, selectedIndex))
    }

    function subtitlePreviewText(segmentData) {
        var sourceIndex = Number(segmentData.sourceIndex);
        if (state.previewEnabled && String(segmentData.id || "") === state.draftSegmentId)
            return state.subtitles.formatSubtitlePreview(sourceIndex, state.draftText);
        if (segmentData.preview_text !== undefined)
            return String(segmentData.preview_text);
        return String(segmentData.text || "");
    }
}
