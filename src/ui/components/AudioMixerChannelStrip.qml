pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    required property int index
    required property var modelData
    required property var colors
    required property bool running
    required property var previewLevels

    signal changeRequested(var changes)

    function volumePercentToDb(percent) {
        var value = Math.max(0, Number(percent) || 0)
        return value <= 0 ? -60 : Math.max(-60, Math.min(6, 20 * Math.log(value / 100) / Math.LN10))
    }

    function dbToVolumePercent(db) {
        var value = Number(db)
        return value <= -60 ? 0 : Math.max(0, Math.min(200, 100 * Math.pow(10, value / 20)))
    }

    id: root
    objectName: "mixerChannelStrip-" + root.index
    readonly property real previewLevel: Number(root.previewLevels[root.modelData.id] || 0)
    width: 170
    radius: 9
    color: modelData.enabled ? "#171E1A" : "#101512"
    border.width: modelData.solo ? 2 : 1
    border.color: modelData.solo ? root.colors.acid : (modelData.muted ? root.colors.amber : root.colors.border)
    opacity: modelData.enabled ? 1.0 : 0.58

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 7
        RowLayout {
            Layout.fillWidth: true
            Text { text: "トラック " + String(root.index + 1).padStart(2, "0"); color: root.colors.acid; font.family: "Yu Gothic UI"; font.pixelSize: 10; font.weight: Font.Bold }
            Item { Layout.fillWidth: true }
            Rectangle {
                Layout.preferredWidth: 62; Layout.preferredHeight: 20; radius: 4
                color: root.modelData.kind === "external" ? "#253225" : "#272C30"
                Text { anchors.centerIn: parent; text: root.modelData.kind === "external" ? "外部音声" : "動画音声"; color: root.colors.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 8; font.weight: Font.Bold }
            }
        }
        Text { Layout.fillWidth: true; text: root.modelData.label; color: root.colors.textPrimary; font.family: "Yu Gothic UI"; font.pixelSize: 11; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideMiddle }
        Text { Layout.fillWidth: true; text: root.modelData.kind === "external" ? "外部音声を使用" : "動画音声を使用"; color: root.colors.textMuted; font.family: "Yu Gothic UI"; font.pixelSize: 8; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.colors.border }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 250
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 13
                anchors.rightMargin: 13
                spacing: 8
                Column {
                    Layout.preferredWidth: 30
                    Layout.fillHeight: true
                    topPadding: 7
                    Text { width: parent.width; text: "+6"; color: root.colors.textMuted; font.family: "Cascadia Mono"; font.pixelSize: 8; horizontalAlignment: Text.AlignRight }
                    Item { width: 1; height: (parent.height - 62) * 0.15 }
                    Text { width: parent.width; text: "0"; color: root.colors.textPrimary; font.family: "Cascadia Mono"; font.pixelSize: 8; horizontalAlignment: Text.AlignRight }
                    Item { width: 1; height: (parent.height - 62) * 0.12 }
                    Text { width: parent.width; text: "−6"; color: root.colors.textMuted; font.family: "Cascadia Mono"; font.pixelSize: 8; horizontalAlignment: Text.AlignRight }
                    Item { width: 1; height: (parent.height - 62) * 0.12 }
                    Text { width: parent.width; text: "−12"; color: root.colors.textMuted; font.family: "Cascadia Mono"; font.pixelSize: 8; horizontalAlignment: Text.AlignRight }
                    Item { width: 1; height: (parent.height - 62) * 0.18 }
                    Text { width: parent.width; text: "−24"; color: root.colors.textMuted; font.family: "Cascadia Mono"; font.pixelSize: 8; horizontalAlignment: Text.AlignRight }
                    Item { width: 1; height: (parent.height - 62) * 0.22 }
                    Text { width: parent.width; text: "−∞"; color: root.colors.textMuted; font.family: "Cascadia Mono"; font.pixelSize: 8; horizontalAlignment: Text.AlignRight }
                }
                Slider {
                    id: channelFader
                    objectName: "mixerChannelFader"
                    Layout.fillHeight: true
                    Layout.preferredWidth: 54
                    orientation: Qt.Vertical
                    from: -60
                    to: 6.0206
                    stepSize: 0.5
                    value: root.volumePercentToDb(root.modelData.volume_percent)
                    enabled: !root.running && root.modelData.enabled
                    onMoved: root.changeRequested({"volume_percent": root.dbToVolumePercent(value)})
                    onPressedChanged: if (!pressed) root.changeRequested({"volume_percent": root.dbToVolumePercent(value)})
                    background: Rectangle {
                        x: channelFader.leftPadding + channelFader.availableWidth / 2 - width / 2
                        y: channelFader.topPadding
                        width: 7
                        height: channelFader.availableHeight
                        radius: 3
                        color: "#090C0B"
                        border.color: root.colors.border
                        Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: parent.height * channelFader.position; radius: 3; color: root.modelData.muted ? root.colors.amber : root.colors.acid; opacity: 0.72 }
                    }
                    handle: Rectangle {
                        x: channelFader.leftPadding + channelFader.availableWidth / 2 - width / 2
                        y: channelFader.topPadding + (1 - channelFader.position) * (channelFader.availableHeight - height)
                        implicitWidth: 44
                        implicitHeight: 18
                        radius: 4
                        color: channelFader.pressed ? root.colors.acid : root.colors.textPrimary
                        border.color: "#0B0E0D"
                        border.width: 2
                        Rectangle { anchors.horizontalCenter: parent.horizontalCenter; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 8; height: 2; color: "#222A26" }
                    }
                }
                Rectangle {
                    Layout.preferredWidth: 13
                    Layout.fillHeight: true
                    Layout.topMargin: 7
                    Layout.bottomMargin: 7
                    radius: 4
                    color: "#070908"
                    border.color: root.colors.border
                    Rectangle {
                        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 2
                        height: Math.max(2, (parent.height - 4) * root.previewLevel)
                        radius: 2
                        Behavior on height { NumberAnimation { duration: 70 } }
                        gradient: Gradient {
                            GradientStop { position: 0.0; color: root.colors.acid }
                            GradientStop { position: 0.72; color: root.colors.amber }
                            GradientStop { position: 1.0; color: root.colors.danger }
                        }
                    }
                }
            }
        }

        Text {
            Layout.fillWidth: true
            text: channelFader.value <= -59.9 ? "−∞ dB" : (channelFader.value >= 0 ? "+" : "") + channelFader.value.toFixed(1) + " dB"
            color: root.colors.textPrimary; font.family: "Cascadia Mono"; font.pixelSize: 13; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter
        }
        Text { Layout.fillWidth: true; text: Math.round(Number(root.modelData.volume_percent)) + "%"; color: root.colors.textMuted; font.family: "Cascadia Mono"; font.pixelSize: 9; horizontalAlignment: Text.AlignHCenter }
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            Button {
                id: channelMuteButton
                objectName: "mixerMuteButton"
                Layout.fillWidth: true; Layout.preferredHeight: 34
                text: "M"
                enabled: !root.running
                onClicked: root.changeRequested({"muted": !root.modelData.muted})
                contentItem: Text { text: channelMuteButton.text; color: root.modelData.muted ? "#10140F" : root.colors.textPrimary; font.family: "Bahnschrift"; font.pixelSize: 13; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { radius: 6; color: root.modelData.muted ? root.colors.amber : root.colors.raised; border.color: root.modelData.muted ? root.colors.amber : root.colors.border }
                ToolTip.visible: hovered
                ToolTip.text: "ミュート"
            }
            Button {
                id: channelSoloButton
                objectName: "mixerSoloButton"
                Layout.fillWidth: true; Layout.preferredHeight: 34
                text: "S"
                enabled: !root.running
                onClicked: root.changeRequested({"solo": !root.modelData.solo})
                contentItem: Text { text: channelSoloButton.text; color: root.modelData.solo ? "#10140F" : root.colors.textPrimary; font.family: "Bahnschrift"; font.pixelSize: 13; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { radius: 6; color: root.modelData.solo ? root.colors.acid : root.colors.raised; border.color: root.modelData.solo ? root.colors.acid : root.colors.border }
                ToolTip.visible: hovered
                ToolTip.text: "ソロ"
            }
        }
        CheckBox {
            objectName: "mixerChannelEnabledCheck"
            Layout.alignment: Qt.AlignHCenter
            text: "使用する"
            checked: Boolean(root.modelData.enabled)
            enabled: !root.running
            onToggled: root.changeRequested({"enabled": checked})
        }
    }
}
