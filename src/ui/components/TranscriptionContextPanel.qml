pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: panelRoot
    objectName: "transcriptionContextPanel"

    property var context: ({})
    property bool running: false
    property color panelColor: "#121715"
    property color raisedColor: "#19201D"
    property color borderColor: "#2A3530"
    property color textPrimaryColor: "#F4F1E8"
    property color textMutedColor: "#8E9B94"
    property color accentColor: "#C8FF3D"

    signal transcriptionContextEdited(var context)

    function valueOrEmpty(value) {
        return value === undefined || value === null ? "" : String(value)
    }

    function _toStringList(value) {
        if (!(value instanceof Array)) {
            return []
        }
        var terms = []
        for (var index = 0; index < value.length; index += 1) {
            if (typeof value[index] !== "string") {
                continue
            }
            var normalized = String(value[index]).trim()
            if (normalized.length > 0) {
                terms.push(normalized)
            }
        }
        return terms
    }

    function applyContext(value) {
        var current = value || ({})
        gameTitleField.text = valueOrEmpty(current.game_title)
        gameNotesField.text = valueOrEmpty(current.game_notes)
        creatorTermsField.text = valueOrEmpty(current.creator_terms_text)
        dictionaryPathField.text = valueOrEmpty(current.dictionary_path)
        dictionaryConfirmedSwitch.checked = Boolean(current.dictionary_confirmed)
        webDictionarySwitch.checked = Boolean(current.web_dictionary_enabled)

        webDictionaryCandidateModel.clear()
        var candidates = _toStringList(current.web_dictionary_candidates)
        var selected = _toStringList(current.web_dictionary_terms)
        var selectedLookup = {}
        for (var selectedIndex = 0; selectedIndex < selected.length; selectedIndex += 1) {
            selectedLookup[selected[selectedIndex]] = true
        }
        for (var candidateIndex = 0; candidateIndex < candidates.length; candidateIndex += 1) {
            var candidate = candidates[candidateIndex]
            webDictionaryCandidateModel.append({
                "term": candidate,
                "selected": Boolean(selectedLookup[candidate]),
            })
        }
    }

    function contextPayload() {
        var selectedTerms = []
        for (var index = 0; index < webDictionaryCandidateModel.count; index += 1) {
            var item = webDictionaryCandidateModel.get(index)
            if (item.selected) {
                selectedTerms.push(item.term)
            }
        }

        var candidateTerms = []
        for (var candidateTermIndex = 0; candidateTermIndex < webDictionaryCandidateModel.count; candidateTermIndex += 1) {
            candidateTerms.push(webDictionaryCandidateModel.get(candidateTermIndex).term)
        }

        return {
            "game_title": gameTitleField.text,
            "game_notes": gameNotesField.text,
            "creator_terms_text": creatorTermsField.text,
            "dictionary_path": dictionaryPathField.text,
            "dictionary_confirmed": dictionaryConfirmedSwitch.checked,
            "web_dictionary_enabled": webDictionarySwitch.checked,
            "web_dictionary_candidates": candidateTerms,
            "web_dictionary_terms": selectedTerms
        }
    }

    function commitContext() {
        transcriptionContextEdited(contextPayload())
    }

    onContextChanged: applyContext(context)
    Component.onCompleted: applyContext(context)

    color: panelRoot.panelColor
    border.color: panelRoot.borderColor
    radius: 10

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 8

        Text {
            Layout.fillWidth: true
            text: "文字起こし辞書"
            color: panelRoot.textMutedColor
            font.family: "Yu Gothic UI"
            font.pixelSize: 10
            font.weight: Font.Bold
            font.letterSpacing: 1.0
        }

        TextField {
            id: gameTitleField
            objectName: "transcriptionGameTitleField"
            Layout.fillWidth: true
            enabled: !panelRoot.running
            placeholderText: "ゲームタイトル"
            text: ""
            color: panelRoot.textPrimaryColor
            selectionColor: panelRoot.accentColor
            selectedTextColor: "#10140F"
            font.family: "Yu Gothic UI"
            font.pixelSize: 11
            onEditingFinished: panelRoot.commitContext()
            background: Rectangle {
                radius: 6
                color: panelRoot.raisedColor
                border.color: gameTitleField.activeFocus ? panelRoot.accentColor : panelRoot.borderColor
            }
        }

        TextArea {
            id: creatorTermsField
            objectName: "transcriptionCreatorTermsField"
            Layout.fillWidth: true
            Layout.preferredHeight: 72
            enabled: !panelRoot.running
            placeholderText: "作成者が確認した固有名詞・略称（1行に1語、またはカンマ区切り）"
            wrapMode: TextEdit.Wrap
            color: panelRoot.textPrimaryColor
            selectionColor: panelRoot.accentColor
            selectedTextColor: "#10140F"
            font.family: "Yu Gothic UI"
            font.pixelSize: 10
            onEditingFinished: panelRoot.commitContext()
            background: Rectangle {
                radius: 6
                color: panelRoot.raisedColor
                border.color: creatorTermsField.activeFocus ? panelRoot.accentColor : panelRoot.borderColor
            }
        }

        TextArea {
            id: gameNotesField
            objectName: "transcriptionGameNotesField"
            Layout.fillWidth: true
            Layout.preferredHeight: 54
            enabled: !panelRoot.running
            placeholderText: "補足メモ（任意）"
            wrapMode: TextEdit.Wrap
            color: panelRoot.textPrimaryColor
            selectionColor: panelRoot.accentColor
            selectedTextColor: "#10140F"
            font.family: "Yu Gothic UI"
            font.pixelSize: 10
            onEditingFinished: panelRoot.commitContext()
            background: Rectangle {
                radius: 6
                color: panelRoot.raisedColor
                border.color: gameNotesField.activeFocus ? panelRoot.accentColor : panelRoot.borderColor
            }
        }

        TextField {
            id: dictionaryPathField
            objectName: "transcriptionDictionaryPathField"
            Layout.fillWidth: true
            enabled: !panelRoot.running
            placeholderText: "確認済み辞書JSONのpath（任意）"
            color: panelRoot.textPrimaryColor
            selectionColor: panelRoot.accentColor
            selectedTextColor: "#10140F"
            font.family: "Yu Gothic UI"
            font.pixelSize: 10
            onEditingFinished: panelRoot.commitContext()
            background: Rectangle {
                radius: 6
                color: panelRoot.raisedColor
                border.color: dictionaryPathField.activeFocus ? panelRoot.accentColor : panelRoot.borderColor
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Switch {
                id: dictionaryConfirmedSwitch
                objectName: "transcriptionDictionaryConfirmedSwitch"
                enabled: !panelRoot.running
                checked: false
                onToggled: panelRoot.commitContext()
            }
            Text {
                Layout.fillWidth: true
                text: "この辞書を確認済みとしてASRへ渡す"
                color: panelRoot.textPrimaryColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 10
                wrapMode: Text.Wrap
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Switch {
                id: webDictionarySwitch
                objectName: "transcriptionWebDictionarySwitch"
                enabled: !panelRoot.running
                checked: false
                onToggled: panelRoot.commitContext()
            }
            Text {
                Layout.fillWidth: true
                text: "Web候補辞書を使う（候補は確認後にのみ適用）"
                color: panelRoot.textMutedColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 10
                wrapMode: Text.Wrap
            }
            Button {
                id: refreshWebDictionaryButton
                objectName: "transcriptionWebDictionaryRefreshButton"
                enabled: !panelRoot.running
                text: "候補を再読込"
                onClicked: panelRoot.commitContext()
            }
        }

        Text {
            Layout.fillWidth: true
            text: "未確認の辞書候補は文字起こしへ渡されません。辞書を変更するとtranscript cacheのfingerprintも変わります。"
            color: panelRoot.textMutedColor
            font.family: "Yu Gothic UI"
            font.pixelSize: 9
            wrapMode: Text.Wrap
        }

        Text {
            Layout.fillWidth: true
            text: "Web候補（チェックがONのみ適用）"
            color: panelRoot.textPrimaryColor
            font.family: "Yu Gothic UI"
            font.pixelSize: 10
            font.weight: Font.Bold
        }

        ScrollView {
            id: webDictionaryCandidateList
            objectName: "transcriptionWebDictionaryCandidateList"
            Layout.fillWidth: true
            Layout.preferredHeight: 130
            clip: true

            ListView {
                id: webDictionaryCandidates
                model: webDictionaryCandidateModel
                interactive: false
                delegate: RowLayout {
                    width: ListView.view.width
                    spacing: 8
                    CheckBox {
                        Layout.fillWidth: true
                        objectName: "transcriptionWebDictionaryCandidateItem"
                        text: model.term
                        checked: model.selected
                        enabled: !panelRoot.running
                        onToggled: {
                            webDictionaryCandidateModel.setProperty(index, "selected", checked)
                            panelRoot.commitContext()
                        }
                    }
                }
            }
        }

        Text {
            Layout.fillWidth: true
            visible: webDictionaryCandidateModel.count === 0
            text: "現在表示できる候補はありません。ゲームタイトルまたは補足ノートを追加してください。"
            color: panelRoot.textMutedColor
            font.family: "Yu Gothic UI"
            font.pixelSize: 9
            wrapMode: Text.Wrap
        }
    }

    ListModel {
        id: webDictionaryCandidateModel
    }
}
