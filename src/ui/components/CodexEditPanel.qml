import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: panel
    property var backend
    property var proposalData: backend && backend.codexProposal ? backend.codexProposal : ({"summary": "", "operations": []})
    property var selectedOperationState: ({})
    property real currentTime: 0
    property bool expanded: false
    implicitHeight: expanded ? 300 : 42
    radius: 9
    color: "#101812"
    border.color: backend && backend.codexState === "error" ? "#C66B62" : "#34463A"
    clip: true

    function selectedOperationIds() {
        var ids = []
        var operations = panel.proposalData && panel.proposalData.operations
            ? panel.proposalData.operations : []
        for (var index = 0; index < operations.length; ++index) {
            var operationId = panel.operationIdFor(operations[index], index)
            if (panel.isOperationSelected(operationId))
                ids.push(operationId)
        }
        return ids
    }

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

    function syncSelectionState() {
        var next = {}
        var operations = panel.proposalData && panel.proposalData.operations
            ? panel.proposalData.operations : []
        for (var index = 0; index < operations.length; ++index) {
            var operationId = panel.operationIdFor(operations[index], index)
            next[operationId] = selectedOperationState[operationId] === undefined
                ? true : Boolean(selectedOperationState[operationId])
        }
        selectedOperationState = next
    }

    function codexRunning() {
        return backend && ["starting", "authenticating", "running"].indexOf(backend.codexState) >= 0
    }

    function stateLabel(value) {
        var labels = {
            "disabled": "利用できません",
            "starting": "準備中",
            "authenticating": "接続中",
            "running": "編集中",
            "error": "エラー",
            "idle": "待機中"
        }
        return labels[String(value || "")] || "状態を確認中"
    }

    function operationLabel(value) {
        var labels = {
            "update_segment": "字幕を更新",
            "add_segment": "字幕を追加",
            "delete_segment": "字幕を削除",
            "split_segment": "字幕を分割",
            "merge_segments": "字幕を結合"
        }
        return labels[String(value || "")] || "字幕を編集"
    }

    onProposalDataChanged: syncSelectionState()
    Component.onCompleted: syncSelectionState()

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 8
        spacing: 6

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: "Codexで編集"
                color: "#E8EFEA"
                font.family: "Yu Gothic UI"
                font.pixelSize: 11
                font.weight: Font.Bold
            }
            Text {
                Layout.fillWidth: true
                text: panel.stateLabel(backend ? backend.codexState : "disabled")
                color: backend && backend.codexState === "error" ? "#F1A39A" : "#AEBEB3"
                font.family: "Yu Gothic UI"
                font.pixelSize: 9
                horizontalAlignment: Text.AlignRight
            }
            Button {
                objectName: "codexEditToggleButton"
                text: panel.expanded ? "閉じる" : "開く"
                onClicked: panel.expanded = !panel.expanded
            }
        }

        ColumnLayout {
            visible: panel.expanded
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 6

            RowLayout {
                Layout.fillWidth: true
                ComboBox {
                    id: scopeBox
                    objectName: "codexScopeCombo"
                    Layout.preferredWidth: 105
                    model: [
                        { "label": "選択中の字幕", "value": "selected" },
                        { "label": "再生位置の字幕", "value": "current" },
                        { "label": "時間範囲", "value": "time_range" },
                        { "label": "すべての字幕", "value": "all" }
                    ]
                    textRole: "label"
                    valueRole: "value"
                }
                TextField {
                    id: rangeStartInput
                    objectName: "codexRangeStartField"
                    Layout.preferredWidth: 65
                    text: "0.000"
                    validator: DoubleValidator { bottom: 0; top: 86400; decimals: 3 }
                }
                TextField {
                    id: rangeEndInput
                    objectName: "codexRangeEndField"
                    Layout.preferredWidth: 65
                    text: "0.000"
                    validator: DoubleValidator { bottom: 0; top: 86400; decimals: 3 }
                }
                Text { text: "秒"; color: "#AEBEB3"; font.pixelSize: 9 }
            }

            TextArea {
                id: promptInput
                objectName: "codexPromptInput"
                Layout.fillWidth: true
                Layout.preferredHeight: 54
                placeholderText: "字幕への依頼を入力"
                wrapMode: TextEdit.Wrap
                selectByMouse: true
            }

            RowLayout {
                Layout.fillWidth: true
                Button {
                    objectName: "codexSendButton"
                    text: backend && backend.codexState === "error" ? "もう一度作成" : "提案を作成"
                    enabled: backend && !panel.codexRunning() && promptInput.text.trim().length > 0
                    onClicked: {
                        backend.setCodexCurrentTime(panel.currentTime)
                        backend.startCodexEdit(
                            promptInput.text,
                            scopeBox.currentValue,
                            Number(rangeStartInput.text || 0),
                            Number(rangeEndInput.text || 0)
                        )
                    }
                }
                Button {
                    objectName: "codexStopButton"
                    text: "停止"
                    enabled: panel.codexRunning()
                    onClicked: backend.stopCodexEdit()
                }
                Item { Layout.fillWidth: true }
                Text { text: panel.proposalData.summary || ""; color: "#B8D7A8"; elide: Text.ElideRight }
            }

            Text {
                objectName: "codexAgentMessageText"
                Layout.fillWidth: true
                text: backend ? backend.codexMessage : ""
                color: "#AEBEB3"
                font.family: "Cascadia Mono"
                font.pixelSize: 9
                elide: Text.ElideRight
            }

            ListView {
                id: proposalList
                objectName: "codexProposalList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: panel.proposalData.operations || []
                delegate: Rectangle {
                        required property var modelData
                    property string operationId: panel.operationIdFor(modelData, index)
                    width: proposalList.width
                    height: 34
                    color: "#172219"
                    radius: 5
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 4
                        CheckBox {
                            id: operationCheck
                            objectName: "codexOperationCheck"
                            checked: panel.isOperationSelected(operationId)
                            onToggled: panel.setOperationSelected(operationId, checked)
                        }
                        Text { text: panel.operationLabel(modelData.type); color: "#E8EFEA"; font.pixelSize: 9 }
                        Text { Layout.fillWidth: true; text: modelData.reason || "字幕の変更を提案"; color: "#AEBEB3"; elide: Text.ElideRight; font.pixelSize: 9 }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Button {
                    objectName: "codexApplyButton"
                    text: "選択した変更を適用"
                    enabled: backend && !panel.codexRunning() && panel.proposalData.operations
                        && panel.proposalData.operations.length > 0
                    onClicked: backend.applyCodexProposal(panel.selectedOperationIds())
                }
                Button {
                    objectName: "codexDiscardButton"
                    text: "破棄"
                    enabled: backend && !panel.codexRunning() && panel.proposalData.operations
                        && panel.proposalData.operations.length > 0
                    onClicked: backend.discardCodexProposal()
                }
            }
            Text {
                visible: backend && backend.codexError
                text: backend ? backend.codexError : ""
                color: "#F1A39A"
                font.pixelSize: 9
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
        }
    }
}
