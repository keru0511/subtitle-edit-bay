import QtQuick
import QtQuick.Controls

Button {
    id: smallControl

    property color textPrimary: "#F0F6FC"
    property color disabledText: "#6E7681"
    property color borderColor: "#30363D"
    property color focusColor: "#6366F1"
    property color defaultBackground: "#21262D"
    property color hoverBackground: "#282E33"
    property color pressedBackground: "#30363D"

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
