import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: candidateRoot
    objectName: "highlightCandidateList"
    property var appBackend: null
    property int sortMode: 0
    property string categoryFilter: "all"
    signal previewRequested(real seconds)

    function visibleCandidates() {
        var candidates = appBackend ? appBackend.highlightCandidates : []
        var filtered = []
        for (var index = 0; index < candidates.length; ++index) {
            if (candidateRoot.categoryFilter === "all" || candidates[index].category === candidateRoot.categoryFilter) {
                var candidate = Object.assign({}, candidates[index])
                candidate.source_index = index
                filtered.push(candidate)
            }
        }
        filtered.sort(function(first, second) {
            return candidateRoot.sortMode === 0
                ? Number(second.score) - Number(first.score)
                : Number(first.start) - Number(second.start)
        })
        return filtered
    }

    RowLayout {
        Layout.fillWidth: true
        Text { text: "見どころ候補"; color: "#E8EFEA"; font.family: "Yu Gothic UI"; font.pixelSize: 12; font.weight: Font.Bold }
        Text { Layout.fillWidth: true; text: appBackend ? Math.round(appBackend.highlightAnalysisProgress * 100) + "%" : ""; color: "#AEBEB3"; font.pixelSize: 9 }
        ComboBox { objectName: "highlightSortCombo"; model: ["おすすめ順", "時間順"]; onActivated: candidateRoot.sortMode = currentIndex }
        ComboBox { objectName: "highlightCategoryCombo"; model: ["all", "conversation", "emphasis"]; onActivated: candidateRoot.categoryFilter = currentText }
        Button {
            objectName: "highlightAnalyzeButton"
            text: appBackend && appBackend.highlightAnalysisState === "running" ? "解析中" : "解析"
            enabled: appBackend && appBackend.highlightAnalysisState !== "running" && !appBackend.running
            onClicked: appBackend.startHighlightAnalysis()
        }
        Button {
            objectName: "highlightCancelButton"
            text: "キャンセル"
            enabled: appBackend && appBackend.highlightAnalysisState === "running"
            onClicked: appBackend.cancelHighlightAnalysis()
        }
    }

    RowLayout {
        Layout.fillWidth: true
        Text { text: appBackend ? appBackend.highlightAnalysisState : "idle"; color: "#8E9B94"; font.pixelSize: 9 }
        Item { Layout.fillWidth: true }
        Button { objectName: "highlightRetryButton"; text: "再解析"; enabled: appBackend && appBackend.highlightAnalysisState !== "running"; onClicked: appBackend.retryHighlightAnalysis() }
        Button { objectName: "highlightUndoRejectButton"; text: "却下を戻す"; enabled: appBackend; onClicked: appBackend.undoHighlightRejection() }
    }

    ListView {
        id: candidateListView
        objectName: "highlightCandidateListView"
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        spacing: 5
        model: candidateRoot.visibleCandidates()

        delegate: Rectangle {
            required property var modelData
            required property int index
            width: candidateListView.width
            height: 76
            radius: 7
            color: "#121A15"
            border.color: "#2A3530"
            RowLayout {
                anchors.fill: parent
                anchors.margins: 6
                spacing: 6
                ColumnLayout {
                    Layout.fillWidth: true
                    Text { Layout.fillWidth: true; text: (modelData.start || 0).toFixed(2) + " - " + (modelData.end || 0).toFixed(2) + "  " + modelData.category; color: "#C8FF3D"; font.family: "Cascadia Mono"; font.pixelSize: 9 }
                    Text { Layout.fillWidth: true; text: modelData.subtitle_excerpt || ""; color: "#E8EFEA"; elide: Text.ElideRight; font.pixelSize: 10 }
                    Text { Layout.fillWidth: true; text: "score " + Number(modelData.score || 0).toFixed(3) + "  " + (modelData.reason || ""); color: "#8E9B94"; elide: Text.ElideRight; font.pixelSize: 8 }
                }
                Button { objectName: "highlightPreviewButton"; text: "再生"; onClicked: candidateRoot.previewRequested(Number(modelData.start || 0)) }
                Button { objectName: "highlightAddButton"; text: "追加"; onClicked: appBackend.addHighlightCandidate(modelData.source_index) }
                Button { objectName: "highlightRejectButton"; text: "却下"; onClicked: appBackend.rejectHighlightCandidate(modelData.source_index) }
            }
        }
    }
}
