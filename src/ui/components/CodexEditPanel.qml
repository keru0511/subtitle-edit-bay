import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: panel
    property var backend
    property bool expanded: false
    implicitHeight: expanded ? 300 : 42
    radius: 9
    color: "#101812"
    border.color: backend && backend.codexState === "error" ? "#C66B62" : "#34463A"
    clip: true

    function selectedOperationIds() {
        var ids = []
        for (var index = 0; index < proposalList.count; ++index) {
            var item = proposalList.itemAtIndex(index)
            if (item && item.checked && item.operationId)
                ids.push(item.operationId)
        }
        return ids
    }

    function codexRunning() {
        return backend && ["starting", "authenticating", "running"].indexOf(backend.codexState) >= 0
    }

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
                text: backend ? backend.codexState : "disabled"
                color: backend && backend.codexState === "error" ? "#F1A39A" : "#AEBEB3"
                font.family: "Cascadia Mono"
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
                    model: backend ? backend.codexScopes : ["selected", "current", "time_range", "all"]
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
                    text: backend && backend.codexState === "error" ? "再試行" : "送信"
                    enabled: backend && !panel.codexRunning() && promptInput.text.trim().length > 0
                    onClicked: backend.startCodexEdit(
                        promptInput.text,
                        scopeBox.currentText,
                        Number(rangeStartInput.text || 0),
                        Number(rangeEndInput.text || 0)
                    )
                }
                Button {
                    objectName: "codexStopButton"
                    text: "停止"
                    enabled: panel.codexRunning()
                    onClicked: backend.stopCodexEdit()
                }
                Item { Layout.fillWidth: true }
                Text { text: backend ? backend.codexProposal.summary || "" : ""; color: "#B8D7A8"; elide: Text.ElideRight }
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
                model: backend && backend.codexProposal.operations ? backend.codexProposal.operations : []
                delegate: Rectangle {
                    required property var modelData
                    property string operationId: String(modelData.id || "")
                    property bool checked: operationCheck.checked
                    width: proposalList.width
                    height: 34
                    color: "#172219"
                    radius: 5
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 4
                        CheckBox { id: operationCheck; objectName: "codexOperationCheck"; checked: true }
                        Text { text: modelData.type || "operation"; color: "#E8EFEA"; font.pixelSize: 9 }
                        Text { Layout.fillWidth: true; text: modelData.reason || modelData.segment_id || ""; color: "#AEBEB3"; elide: Text.ElideRight; font.pixelSize: 9 }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Button {
                    objectName: "codexApplyButton"
                    text: "選択した変更を適用"
                    enabled: backend && backend.codexProposal.operations && backend.codexProposal.operations.length > 0
                    onClicked: backend.applyCodexProposal(panel.selectedOperationIds())
                }
                Button {
                    objectName: "codexDiscardButton"
                    text: "破棄"
                    enabled: backend && backend.codexProposal.operations && backend.codexProposal.operations.length > 0
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
