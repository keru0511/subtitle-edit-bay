import QtQuick
import QtQuick.Controls

Button {
    id: smallControl

    property color textPrimary: "#F8FAFC"
    property color disabledText: "#64748B"
    property color borderColor: "#243044"
    property color focusColor: "#6366F1"
    property color defaultBackground: "#1A2332"
    property color hoverBackground: "#222E42"
    property color pressedBackground: "#2A374D"

    implicitHeight: 32
    contentItem: Text {
        text: smallControl.text
        color: smallControl.enabled ? smallControl.textPrimary : smallControl.disabledText
        font.family: "Yu Gothic UI"
        font.pixelSize: 10
        font.weight: Font.DemiBold
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        radius: 7
        color: smallControl.down ? smallControl.pressedBackground : (smallControl.hovered ? smallControl.hoverBackground : smallControl.defaultBackground)
        border.color: smallControl.activeFocus ? smallControl.focusColor : smallControl.borderColor
    }
}
