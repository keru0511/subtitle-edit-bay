pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: panel
    // qmllint disable unqualified
    property var backend
    property bool expanded: false
    property color panelColor: "#101512"
    property color raisedColor: "#19201D"
    property color borderColor: "#2A3530"
    property color textColor: "#F4F1E8"
    property color mutedColor: "#8E9B94"
    property color accentColor: "#C8FF3D"
    property color errorColor: "#FF8A80"

    width: expanded ? 420 : 320
    height: expanded ? Math.min(680, parent ? parent.height - 24 : 680) : 46
    radius: 10
    color: panelColor
    border.color: backend && backend.codexChatError ? errorColor : borderColor
    border.width: 1
    clip: true

    function authenticated() {
        return backend && backend.codexAuthState === "authenticated"
    }

    function busy() {
        return backend && ["sending", "streaming", "stopping"].indexOf(backend.codexChatState) >= 0
    }

    function authStateLabel() {
        if (!backend)
            return "状態を確認中"
        if (backend.codexConnectionState === "connecting")
            return "接続中"
        if (backend.codexConnectionState === "disconnected")
            return "切断"
        if (backend.codexConnectionState === "error")
            return "接続エラー"
        var labels = {
            "checking": "認証を確認中",
            "logging_in": "ログイン開始中",
            "login_pending": "ログイン待ち",
            "authenticated": backend.codexAuthLabel || "ログイン済み",
            "unauthenticated": "未ログイン",
            "error": "認証エラー"
        }
        return labels[String(backend.codexAuthState || "")] || "状態を確認中"
    }

    function chatStateLabel() {
        if (!backend)
            return ""
        var labels = {
            "sending": "送信中",
            "streaming": "応答を受信中",
            "stopping": "停止中",
            "send_failed": "送信失敗",
            "disconnected": "接続が切れました",
            "idle": "待機中"
        }
        return labels[String(backend.codexChatState || "")] || ""
    }

    function syncModelSelection() {
        if (!backend || modelCombo.count === 0)
            return
        for (var index = 0; index < modelCombo.count; ++index) {
            if (modelCombo.valueAt(index) === backend.codexSelectedModel) {
                modelCombo.currentIndex = index
                return
            }
        }
        modelCombo.currentIndex = -1
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 8
        spacing: 7

        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 30
            spacing: 6

            Text {
                text: "Codexチャット"
                color: panel.textColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 12
                font.weight: Font.Bold
            }
            Text {
                Layout.fillWidth: true
                text: panel.authStateLabel()
                color: backend && ["error", "disconnected"].indexOf(backend.codexConnectionState) >= 0
                    ? panel.errorColor : panel.mutedColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 9
                horizontalAlignment: Text.AlignRight
                elide: Text.ElideRight
            }
            Button {
                id: connectionButton
                objectName: "codexConnectButton"
                visible: !panel.authenticated()
                text: backend && backend.codexAuthState === "login_pending"
                    ? "ブラウザを開く"
                    : (backend && ["error", "disconnected"].indexOf(backend.codexConnectionState) >= 0
                        ? "再接続" : "ログイン")
                enabled: backend && backend.codexConnectionState !== "connecting"
                    && backend.codexAuthState !== "logging_in"
                onClicked: {
                    if (backend.codexAuthState === "login_pending")
                        backend.openCodexLoginPage()
                    else if (["error", "disconnected"].indexOf(backend.codexConnectionState) >= 0)
                        backend.reconnectCodexChat()
                    else
                        backend.startCodexLogin()
                }
            }
            Button {
                objectName: "codexChatToggleButton"
                text: panel.expanded ? "閉じる" : "開く"
                enabled: panel.authenticated()
                onClicked: panel.expanded = !panel.expanded
            }
        }

        ColumnLayout {
            visible: panel.expanded
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 7

            RowLayout {
                Layout.fillWidth: true
                Text { text: "モデル"; color: panel.mutedColor; font.pixelSize: 9 }
                ComboBox {
                    id: modelCombo
                    objectName: "codexModelCombo"
                    Layout.fillWidth: true
                    model: backend ? backend.codexModels : []
                    textRole: "label"
                    valueRole: "id"
                    enabled: backend && backend.codexModels.length > 0
                    Component.onCompleted: panel.syncModelSelection()
                    onActivated: backend.selectCodexModel(currentValue)
                }
                Button {
                    objectName: "codexNewChatButton"
                    text: "新規"
                    enabled: !panel.busy()
                    onClicked: backend.startNewCodexChat()
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Text {
                    Layout.fillWidth: true
                    text: panel.chatStateLabel()
                    color: backend && backend.codexChatState === "send_failed" ? panel.errorColor : panel.mutedColor
                    font.pixelSize: 9
                }
                Button {
                    objectName: "codexReloginButton"
                    text: "再ログイン"
                    enabled: !panel.busy()
                    onClicked: backend.reloginCodex()
                }
                Button {
                    objectName: "codexLogoutButton"
                    text: "ログアウト"
                    enabled: !panel.busy()
                    onClicked: {
                        panel.expanded = false
                        backend.logoutCodex()
                    }
                }
            }

            ListView {
                id: chatMessages
                objectName: "codexChatMessageList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 6
                model: backend ? backend.codexChatMessages : []
                delegate: Rectangle {
                    id: messageDelegate
                    required property var modelData
                    width: chatMessages.width
                    height: messageText.implicitHeight + 18
                    radius: 7
                    color: modelData.role === "user" ? "#253225" : panel.raisedColor
                    border.color: panel.borderColor
                    Text {
                        id: messageText
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 8
                        text: String(messageDelegate.modelData.text || "")
                            + (messageDelegate.modelData.status === "streaming" ? " ▍" : "")
                        color: panel.textColor
                        font.family: "Yu Gothic UI"
                        font.pixelSize: 10
                        wrapMode: Text.Wrap
                    }
                }
                onCountChanged: Qt.callLater(function() { chatMessages.positionViewAtEnd() })
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
            }

            Text {
                Layout.fillWidth: true
                visible: backend && (backend.codexModelError || backend.codexChatError)
                text: backend ? (backend.codexModelError || backend.codexChatError) : ""
                color: panel.errorColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 9
                wrapMode: Text.Wrap
            }

            Text {
                objectName: "codexLocalReadNotice"
                Layout.fillWidth: true
                text: "書き込みは禁止されていますが、Codexはローカルファイルを読み取る場合があります。"
                color: panel.mutedColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 9
                wrapMode: Text.Wrap
            }

            RowLayout {
                Layout.fillWidth: true
                TextArea {
                    id: chatInput
                    objectName: "codexChatInput"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 62
                    placeholderText: "Codexへのメッセージ"
                    wrapMode: TextEdit.Wrap
                    selectByMouse: true
                    enabled: panel.authenticated() && !panel.busy()
                }
                ColumnLayout {
                    Button {
                        objectName: "codexChatSendButton"
                        text: "送信"
                        enabled: panel.authenticated() && !panel.busy() && chatInput.text.trim().length > 0
                        onClicked: {
                            var message = chatInput.text
                            chatInput.clear()
                            backend.sendCodexChatMessage(message)
                        }
                    }
                    Button {
                        objectName: "codexChatStopButton"
                        text: "停止"
                        enabled: panel.busy() && backend.codexChatState !== "stopping"
                        onClicked: backend.stopCodexChat()
                    }
                }
            }
        }
    }

    Connections {
        target: backend
        function onCodexChatChanged() {
            panel.syncModelSelection()
            if (!panel.authenticated())
                panel.expanded = false
            Qt.callLater(function() { chatMessages.positionViewAtEnd() })
        }
    }
    // qmllint enable unqualified
}
