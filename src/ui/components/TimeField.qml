import QtQuick
import QtQuick.Controls

TextField {
    id: timeControl

    property color textPrimary: "#F0F6FC"
    property color borderColor: "#30363D"
    property color focusColor: "#6366F1"
    property color inputBackground: "#161B22"

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
