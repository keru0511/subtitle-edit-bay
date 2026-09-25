pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: card
    // qmllint disable unqualified
    property var backend
    property bool audioHasProposal: Boolean(backend && backend.audio.audioMixProposal
        && backend.audio.audioMixProposal.operations && backend.audio.audioMixProposal.operations.length > 0)
    property bool audioBusy: Boolean(backend)
        && ["starting", "authenticating", "running"].indexOf(backend.audio.audioMixProposalState) >= 0
    property bool audioProposal: audioBusy || audioHasProposal
    property var proposalData: audioProposal && backend.audio.audioMixProposal
        ? backend.audio.audioMixProposal
        : (backend && backend.ai.codexProposal ? backend.ai.codexProposal : ({"summary": "", "operations": []}))
    property var operations: proposalData && proposalData.operations
        ? proposalData.operations : []
    property var selectedOperationState: ({})
    visible: Boolean(backend) && (backend.ai.codexState === "running"
        || backend.ai.codexState === "starting" || backend.ai.codexState === "authenticating"
        || audioBusy || operations.length > 0)
    implicitHeight: visible ? Math.min(220, content.implicitHeight + 16) : 0
    radius: 7
    color: "#18211C"
    border.color: backend && (backend.ai.codexState === "error" || backend.audio.audioMixProposalState === "error")
        ? "#FF8A80" : "#405247"
    clip: true

    function operationIdFor(operation, index) {
        return String(operation && operation.id ? operation.id : "operation-" + String(index + 1).padStart(4, "0"))
    }
    function isOperationSelected(operationId) {
        return selectedOperationState[operationId] === undefined
            ? true : Boolean(selectedOperationState[operationId])
    }
    function setOperationSelected(operationId, selected) {
        var next = {}
        for (var key in selectedOperationState)
            next[key] = selectedOperationState[key]
        next[operationId] = Boolean(selected)
        selectedOperationState = next
    }
    function selectedOperationIds() {
        var ids = []
        for (var index = 0; index < operations.length; ++index) {
            var operationId = operationIdFor(operations[index], index)
            if (isOperationSelected(operationId))
                ids.push(operationId)
        }
        return ids
    }
    function operationLabel(value) {
        var labels = {
            "update_segment": "字幕を更新", "add_segment": "字幕を追加",
            "delete_segment": "字幕を削除", "split_segment": "字幕を分割",
            "merge_segments": "字幕を結合", "update_audio_channel": "音声チャンネルを更新"
        }
        return labels[String(value || "")] || "字幕を編集"
    }

    ColumnLayout {
        id: content
        anchors.fill: parent
        anchors.margins: 8
        spacing: 5
        RowLayout {
            Layout.fillWidth: true
            Text { text: card.audioProposal ? "音量ミキサーの提案" : "字幕編集の提案"; color: "#C8FF3D"; font.bold: true; font.pixelSize: 10 }
            Text {
                Layout.fillWidth: true
                text: card.proposalData.summary || (card.audioBusy ? "音量ミキサーの変更案を作成しています…" : (backend ? backend.ai.codexMessage : ""))
                color: "#B8C7BE"; elide: Text.ElideRight; font.pixelSize: 9
            }
        }
        ListView {
            id: proposalList
            objectName: "codexProposalList"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(112, contentHeight)
            clip: true
            model: card.operations
            delegate: RowLayout {
                required property var modelData
                property string operationId: card.operationIdFor(modelData, index)
                width: proposalList.width
                height: 28
                CheckBox {
                    objectName: "codexOperationCheck"
                    checked: card.isOperationSelected(parent.operationId)
                    onToggled: card.setOperationSelected(parent.operationId, checked)
                }
                Text { text: card.operationLabel(parent.modelData.type); color: "#F4F1E8"; font.pixelSize: 9 }
                Text { Layout.fillWidth: true; text: parent.modelData.reason || (card.audioProposal ? "音声の変更" : "字幕の変更"); color: "#8E9B94"; elide: Text.ElideRight; font.pixelSize: 9 }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Button {
                objectName: "codexApplyButton"
                text: "選択した変更を適用"
                enabled: card.operations.length > 0
                    && card.selectedOperationIds().length > 0
                    && (card.audioProposal
                        ? ["starting", "authenticating", "running"].indexOf(backend.audio.audioMixProposalState) < 0
                        : ["starting", "authenticating", "running"].indexOf(backend.ai.codexState) < 0)
                onClicked: {
                    if (card.audioProposal)
                        backend.audio.applyAudioMixProposal(card.selectedOperationIds(), false)
                    else
                        backend.ai.applyCodexProposal(card.selectedOperationIds())
                }
            }
            Button {
                objectName: "codexAudioAllowSilenceButton"
                visible: card.audioProposal
                text: "無音化を許可して適用"
                enabled: card.operations.length > 0
                    && card.selectedOperationIds().length > 0
                    && ["starting", "authenticating", "running"].indexOf(backend.audio.audioMixProposalState) < 0
                onClicked: backend.audio.applyAudioMixProposal(card.selectedOperationIds(), true)
            }
            Button {
                objectName: "codexDiscardButton"
                text: "破棄"
                enabled: card.operations.length > 0
                onClicked: {
                    if (card.audioProposal)
                        backend.audio.discardAudioMixProposal()
                    else
                        backend.ai.discardCodexProposal()
                }
            }
            Item { Layout.fillWidth: true }
        }
    }
    // qmllint enable unqualified
}
