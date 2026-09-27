pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: panel
    // qmllint disable unqualified
    property var backend
    property bool expanded: false
    property color panelColor: "#131A26"
    property color raisedColor: "#1A2332"
    property color borderColor: "#243044"
    property color textColor: "#F8FAFC"
    property color mutedColor: "#94A3B8"
    property color accentColor: "#6366F1"
    property color errorColor: "#EF4444"

    implicitWidth: expanded ? 420 : 320
    implicitHeight: expanded ? Math.min(680, parent ? parent.height - 24 : 680) : 46
    radius: 10
    color: panelColor
    border.color: backend && backend.ai.codexChatError ? errorColor : borderColor
    border.width: 1
    clip: true

    function authenticated() {
        return backend && backend.ai.codexAuthState === "authenticated"
    }

    function providerName() {
        return backend && backend.ai.aiChatProviderName ? backend.ai.aiChatProviderName : "AI"
    }

    function busy() {
        return backend && (["sending", "streaming", "stopping"].indexOf(backend.ai.codexChatState) >= 0
            || ["starting", "authenticating", "running"].indexOf(backend.ai.codexState) >= 0
            || ["starting", "authenticating", "running"].indexOf(backend.audio.audioMixProposalState) >= 0)
    }

    function authStateLabel() {
        if (!backend)
            return "状態を確認中"
        if (backend.ai.codexConnectionState === "connecting")
            return "接続中"
        if (backend.ai.codexConnectionState === "disconnected")
            return "切断"
        if (backend.ai.codexConnectionState === "error")
            return "接続エラー"
        var labels = {
            "checking": "認証を確認中",
            "logging_in": "ログイン開始中",
            "login_pending": "ログイン待ち",
            "authenticated": backend.ai.codexAuthLabel || "ログイン済み",
            "unauthenticated": "未ログイン",
            "error": "認証エラー"
        }
        return labels[String(backend.ai.codexAuthState || "")] || "状態を確認中"
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
        return labels[String(backend.ai.codexChatState || "")] || ""
    }

    function syncModelSelection() {
        if (!backend || modelCombo.count === 0)
            return
        for (var index = 0; index < modelCombo.count; ++index) {
            if (modelCombo.valueAt(index) === backend.ai.codexSelectedModel) {
                modelCombo.currentIndex = index
                return
            }
        }
        modelCombo.currentIndex = -1
    }

    function syncProviderSelection() {
        if (!backend || providerCombo.count === 0)
            return
        for (var index = 0; index < providerCombo.count; ++index) {
            if (providerCombo.valueAt(index) === backend.ai.aiChatProviderId) {
                providerCombo.currentIndex = index
                return
            }
        }
        providerCombo.currentIndex = -1
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 8
        spacing: 7

        ColumnLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            spacing: 4

            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                Text {
                    text: panel.providerName() + "チャット"
                    textFormat: Text.PlainText
                    color: panel.textColor
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 12
                    font.weight: Font.Bold
                }
                Item { Layout.fillWidth: true }
                SmallButton {
                    objectName: "codexChatToggleButton"
                    Layout.preferredWidth: 48
                    Layout.minimumWidth: 48
                    Layout.maximumWidth: 48
                    Layout.preferredHeight: 30
                    Layout.maximumHeight: 30
                    text: panel.expanded ? "閉じる" : "開く"
                    enabled: panel.authenticated()
                    onClicked: panel.expanded = !panel.expanded
                }
            }
            RowLayout {
                visible: panel.expanded
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                spacing: 6
                ComboBox {
                    id: providerCombo
                    objectName: "aiProviderHeaderCombo"
                    Layout.preferredWidth: 88
                    Layout.minimumWidth: 70
                    model: backend ? backend.ai.aiChatProviders : []
                    textRole: "label"
                    valueRole: "id"
                    enabled: backend && !panel.busy() && backend.ai.aiChatProviders.length > 0
                    Component.onCompleted: panel.syncProviderSelection()
                    onActivated: backend.ai.selectAIProvider(currentValue)
                }
                Text {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: panel.authStateLabel()
                    textFormat: Text.PlainText
                    color: backend && ["error", "disconnected"].indexOf(backend.ai.codexConnectionState) >= 0
                        ? panel.errorColor : panel.mutedColor
                    font.family: "Yu Gothic UI"
                    font.pixelSize: 9
                    horizontalAlignment: Text.AlignRight
                    elide: Text.ElideRight
                }
                SmallButton {
                    id: connectionButton
                    objectName: "codexConnectButton"
                    Layout.preferredWidth: connectionButton.text === "ブラウザを開く" ? 84 : 62
                    Layout.minimumWidth: connectionButton.text === "ブラウザを開く" ? 84 : 62
                    Layout.maximumWidth: connectionButton.text === "ブラウザを開く" ? 84 : 62
                    Layout.preferredHeight: 30
                    Layout.maximumHeight: 30
                    visible: !panel.authenticated()
                        && (!backend || backend.ai.aiChatLoginAvailable)
                    text: backend && backend.ai.codexAuthState === "login_pending"
                        ? "ブラウザを開く"
                        : (backend && ["error", "disconnected"].indexOf(backend.ai.codexConnectionState) >= 0
                            ? "再接続" : "ログイン")
                    enabled: backend && backend.ai.codexConnectionState !== "connecting"
                        && backend.ai.codexAuthState !== "logging_in"
                    onClicked: {
                        if (backend.ai.codexAuthState === "login_pending")
                            backend.ai.openAIProviderLoginPage()
                        else if (["error", "disconnected"].indexOf(backend.ai.codexConnectionState) >= 0)
                            backend.ai.reconnectAIChat()
                        else
                            backend.ai.startAIProviderLogin()
                    }
                }
            }
        }

            ColumnLayout {
                visible: panel.expanded
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 7

            RowLayout {
                Layout.fillWidth: true
                Text { text: "モデル"; textFormat: Text.PlainText; color: panel.mutedColor; font.pixelSize: 9 }
                ComboBox {
                    id: modelCombo
                    objectName: "codexModelCombo"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    model: backend ? backend.ai.codexModels : []
                    textRole: "label"
                    valueRole: "id"
                    visible: backend && backend.ai.aiChatModelSelectionSupported
                    enabled: visible && backend.ai.codexModels.length > 0 && !panel.busy()
                    Component.onCompleted: panel.syncModelSelection()
                    onActivated: backend.ai.selectCodexModel(currentValue)
                }
                Text {
                    visible: backend && !backend.ai.aiChatModelSelectionSupported
                    text: "プロバイダ既定"
                    color: panel.textColor
                    font.pixelSize: 9
                }
                SmallButton {
                    objectName: "codexNewChatButton"
                    Layout.preferredWidth: 48
                    Layout.minimumWidth: 48
                    Layout.maximumWidth: 48
                    text: "新規"
                    enabled: !panel.busy()
                    onClicked: backend.ai.startNewCodexChat()
                }
            }

            RowLayout {
                Layout.fillWidth: true
                visible: editScope.currentValue === "time_range"
                Text { text: "範囲"; color: panel.mutedColor; font.pixelSize: 9 }
                TextField {
                    id: rangeStart
                    objectName: "codexChatRangeStart"
                    Layout.fillWidth: true
                    text: "0.000"
                    validator: DoubleValidator { bottom: 0; top: 86400; decimals: 3 }
                }
                Text { text: "〜"; color: panel.mutedColor }
                TextField {
                    id: rangeEnd
                    objectName: "codexChatRangeEnd"
                    Layout.fillWidth: true
                    text: "0.000"
                    validator: DoubleValidator { bottom: 0; top: 86400; decimals: 3 }
                }
                Text { text: "秒"; color: panel.mutedColor; font.pixelSize: 9 }
            }

            RowLayout {
                Layout.fillWidth: true
                Text {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: panel.chatStateLabel()
                    textFormat: Text.PlainText
                    color: backend && backend.ai.codexChatState === "send_failed" ? panel.errorColor : panel.mutedColor
                    font.pixelSize: 9
                }
                SmallButton {
                    objectName: "codexReloginButton"
                    Layout.preferredWidth: 68
                    Layout.minimumWidth: 68
                    Layout.maximumWidth: 68
                    text: "再ログイン"
                    enabled: !panel.busy()
                    onClicked: backend.ai.reloginAIProvider()
                }
                SmallButton {
                    objectName: "codexLogoutButton"
                    Layout.preferredWidth: 68
                    Layout.minimumWidth: 68
                    Layout.maximumWidth: 68
                    text: "ログアウト"
                    enabled: !panel.busy()
                    onClicked: {
                        panel.expanded = false
                        backend.ai.logoutAIProvider()
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
                model: backend ? backend.ai.codexChatMessages : []
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
                        textFormat: Text.PlainText
                        color: panel.textColor
                        font.family: "Yu Gothic UI"
                        font.pixelSize: 10
                        wrapMode: Text.Wrap
                    }
                }
                onCountChanged: Qt.callLater(function() { chatMessages.positionViewAtEnd() })
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
            }

            CodexEditPanel {
                objectName: "codexChatProposalCard"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                Layout.preferredHeight: implicitHeight
                backend: panel.backend
            }

            Text {
                Layout.fillWidth: true
                visible: backend && (backend.ai.codexModelError || backend.ai.codexChatError)
                text: backend ? (backend.ai.codexModelError || backend.ai.codexChatError) : ""
                textFormat: Text.PlainText
                color: panel.errorColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 9
                wrapMode: Text.Wrap
            }

            Text {
                Layout.fillWidth: true
                visible: backend && backend.ai.aiChatAuthHint
                text: backend ? backend.ai.aiChatAuthHint : ""
                textFormat: Text.PlainText
                color: panel.mutedColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 9
                wrapMode: Text.Wrap
            }

            Text {
                objectName: "codexLocalReadNotice"
                Layout.fillWidth: true
                text: "書き込みは禁止されていますが、Codexはローカルファイルを読み取る場合があります。"
                textFormat: Text.PlainText
                color: panel.mutedColor
                font.family: "Yu Gothic UI"
                font.pixelSize: 9
                wrapMode: Text.Wrap
            }

            RowLayout {
                Layout.fillWidth: true
                ComboBox {
                    id: editScope
                    objectName: "codexChatEditScope"
                    Layout.preferredWidth: 92
                    model: [
                        {"label": "自動", "value": "auto"},
                        {"label": "選択字幕", "value": "selected"},
                        {"label": "再生位置", "value": "current"},
                        {"label": "時間範囲", "value": "time_range"},
                        {"label": "全字幕", "value": "all"}
                    ]
                    textRole: "label"
                    valueRole: "value"
                    ToolTip.visible: hovered
                    ToolTip.text: "字幕編集の対象。自動では全字幕を選びません"
                }
                TextArea {
                    id: chatInput
                    objectName: "codexChatInput"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    Layout.preferredHeight: 62
                    placeholderText: panel.providerName() + "へのメッセージ"
                    textFormat: TextEdit.PlainText
                    wrapMode: TextEdit.Wrap
                    selectByMouse: true
                    enabled: panel.authenticated() && !panel.busy()
                }
                ColumnLayout {
                    SmallButton {
                        objectName: "codexChatSendButton"
                        Layout.preferredWidth: 48
                        Layout.minimumWidth: 48
                        Layout.maximumWidth: 48
                        text: "送信"
                        enabled: panel.authenticated() && !panel.busy() && chatInput.text.trim().length > 0
                        onClicked: {
                            var message = chatInput.text
                            chatInput.clear()
                            backend.ai.sendCodexChatMessage(
                                message,
                                editScope.currentValue,
                                Number(rangeStart.text || 0),
                                Number(rangeEnd.text || 0)
                            )
                        }
                    }
                    SmallButton {
                        objectName: "codexChatStopButton"
                        Layout.preferredWidth: 48
                        Layout.minimumWidth: 48
                        Layout.maximumWidth: 48
                        text: "停止"
                        enabled: panel.busy() && backend.ai.codexChatState !== "stopping"
                        onClicked: backend.ai.stopCodexChat()
                    }
                }
            }
        }
    }

    Connections {
        target: backend ? backend.ai : null
        function onCodexChatChanged() {
            panel.syncProviderSelection()
            panel.syncModelSelection()
            if (!panel.authenticated())
                panel.expanded = false
            Qt.callLater(function() { chatMessages.positionViewAtEnd() })
        }
        function onAiChatChanged() {
            panel.syncProviderSelection()
            panel.syncModelSelection()
            if (!panel.authenticated())
                panel.expanded = false
        }
    }
    // qmllint enable unqualified
}
