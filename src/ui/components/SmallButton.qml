import QtQuick
import QtQuick.Controls

Button {
    id: smallControl

    property color textPrimary: "#F4F1E8"
    property color disabledText: "#59635D"
    property color borderColor: "#2A3530"
    property color focusColor: "#C8FF3D"
    property color defaultBackground: "#1A211E"
    property color hoverBackground: "#27312C"
    property color pressedBackground: "#303B35"

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
