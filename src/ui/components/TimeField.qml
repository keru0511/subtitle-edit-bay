import QtQuick
import QtQuick.Controls

TextField {
    id: timeControl

    property color textPrimary: "#F4F1E8"
    property color borderColor: "#2A3530"
    property color focusColor: "#C8FF3D"
    property color inputBackground: "#101512"

    horizontalAlignment: TextInput.AlignRight
    color: textPrimary
    selectionColor: focusColor
    font.family: "Cascadia Mono"
    font.pixelSize: 11
    validator: DoubleValidator { bottom: 0; decimals: 3 }
    background: Rectangle {
        radius: 6
        color: timeControl.inputBackground
        border.color: timeControl.activeFocus ? timeControl.focusColor : timeControl.borderColor
    }
}
