import QtQuick
import QtQuick.Controls

SpinBox {
    id: compactSpin

    property color textPrimary: "#F4F1E8"
    property color borderColor: "#2A3530"
    property color focusColor: "#C8FF3D"
    property color inputBackground: "#101512"
    property color pressedBackground: "#303B35"

    implicitWidth: 106
    implicitHeight: 34
    editable: true
    font.family: "Cascadia Mono"
    font.pixelSize: 11
    contentItem: TextInput {
        z: 1
        text: compactSpin.textFromValue(compactSpin.value, compactSpin.locale)
        color: compactSpin.textPrimary
        selectionColor: compactSpin.focusColor
        selectedTextColor: "#10140F"
        horizontalAlignment: Qt.AlignHCenter
        verticalAlignment: Qt.AlignVCenter
        readOnly: !compactSpin.editable
        validator: compactSpin.validator
        inputMethodHints: Qt.ImhFormattedNumbersOnly
        leftPadding: 31
        rightPadding: 31
    }
    up.indicator: Rectangle {
        x: compactSpin.width - width
        width: 30
        height: compactSpin.height
        color: compactSpin.up.pressed ? compactSpin.pressedBackground : "transparent"
        border.color: compactSpin.borderColor
        Text { anchors.centerIn: parent; text: "+"; color: compactSpin.textPrimary; font.pixelSize: 18 }
    }
    down.indicator: Rectangle {
        width: 30
        height: compactSpin.height
        color: compactSpin.down.pressed ? compactSpin.pressedBackground : "transparent"
        border.color: compactSpin.borderColor
        Text { anchors.centerIn: parent; text: "−"; color: compactSpin.textPrimary; font.pixelSize: 18 }
    }
    background: Rectangle {
        radius: 6
        color: compactSpin.inputBackground
        border.color: compactSpin.activeFocus ? compactSpin.focusColor : compactSpin.borderColor
    }
}
