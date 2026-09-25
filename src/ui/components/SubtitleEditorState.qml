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

    function canSplitSelectedSegment(positionMs) {
        var index = state.subtitles.selectedSegmentIndex;
        var segmentCount = state.subtitles.segmentCount;
        if (index < 0 || index >= segmentCount)
            return false;
        var segment = state.subtitles.segmentAt(index);
        var seconds = Number(positionMs) / 1000;
        return seconds > Number(segment.start) + 0.05 && seconds < Number(segment.end) - 0.05;
    }

    function beginSubtitleDraft(segmentIndex, text) {
        state.draftSegmentIndex = segmentIndex;
        state.draftText = String(text);
    }

    function updateSubtitleDraft(segmentIndex, text) {
        if (state.draftSegmentIndex !== segmentIndex)
            state.draftSegmentIndex = segmentIndex;
        state.draftText = String(text);
    }

    function clearSubtitleDraft(segmentIndex) {
        if (state.draftSegmentIndex !== segmentIndex)
            return;
        state.draftSegmentIndex = -1;
        state.draftText = "";
    }

    function subtitlePreviewText(segmentData) {
        var sourceIndex = Number(segmentData.sourceIndex);
        if (state.previewEnabled && sourceIndex === state.draftSegmentIndex)
            return state.subtitles.formatSubtitlePreview(sourceIndex, state.draftText);
        if (segmentData.preview_text !== undefined)
            return String(segmentData.preview_text);
        return String(segmentData.text || "");
    }
}
