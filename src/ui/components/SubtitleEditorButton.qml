import QtQuick

SmallButton {
    required property var colors
    focusPolicy: Qt.TabFocus
    textPrimary: colors.textPrimary
    disabledText: "#59635D"
    borderColor: colors.border
    focusColor: colors.acid
    defaultBackground: "#1A211E"
    hoverBackground: "#27312C"
    pressedBackground: "#303B35"
}
