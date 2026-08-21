import QtQuick
import QtMultimedia

Item {
    id: overlayRoot

    property var appBackend: null
    property MediaPlayer player: null
    property string captionObjectPrefix: "subtitleOverlayCaption"
    property int baseFontSize: 50
    property int defaultSubtitleFontSize: 50
    property color outlineColor: "#000000"
    property int outlineThickness: 3
    property var speakerColors: []
    property var subtitleTextResolver: null
    property var activeSegments: []
    property string activeSignature: ""
    property int rowMarginBase: 34
    property int rowMarginStepBase: 156

    function previewPixelSize(fontScale) {
        var normalizedScale = Math.max(0.1, Number(fontScale) || 1)
        var outputFontSize = Math.max(3, Math.round(overlayRoot.baseFontSize * normalizedScale))
        return Math.max(1, Math.round(22 * outputFontSize / overlayRoot.defaultSubtitleFontSize))
    }

    function maxSubtitleFontScale() {
        var maxScale = 1
        var allSegments = overlayRoot.appBackend ? overlayRoot.appBackend.subtitleSegments : []
        for (var index = 0; index < allSegments.length; index++) {
            var candidate = Number(allSegments[index].subtitle_font_scale)
            if (candidate > 0)
                maxScale = Math.max(maxScale, Math.max(0.1, candidate))
        }
        return maxScale
    }

    function maxSubtitlePixelSize() {
        return Math.max(
            3,
            Math.round(overlayRoot.baseFontSize * overlayRoot.maxSubtitleFontScale())
        )
    }

    function maxLayoutRow() {
        var maxRow = 0
        var allSegments = overlayRoot.appBackend ? overlayRoot.appBackend.subtitleSegments : []
        for (var index = 0; index < allSegments.length; index++)
            maxRow = Math.max(maxRow, Number(allSegments[index].layout_row || 0))
        return maxRow
    }

    function previewRowMarginStep() {
        var scale = Math.max(
            1,
            overlayRoot.baseFontSize / overlayRoot.defaultSubtitleFontSize * overlayRoot.maxSubtitleFontScale()
        )
        var scaledStep = Math.max(1, Math.round(overlayRoot.rowMarginStepBase * scale))
        var maxRow = overlayRoot.maxLayoutRow()
        if (maxRow <= 0)
            return scaledStep
        var available = Math.max(1, overlayRoot.height - overlayRoot.rowMarginBase - overlayRoot.maxSubtitlePixelSize())
        return Math.max(1, Math.min(scaledStep, Math.floor(available / maxRow)))
    }

    function outlineOffsets(thickness) {
        var outputThickness = Math.max(0, Math.min(20, Math.round(Number(thickness) || 0)))
        if (outputThickness === 0)
            return []
        var previewThickness = Math.max(
            1,
            Math.round(22 * outputThickness / overlayRoot.defaultSubtitleFontSize)
        )
        var offsets = []
        for (var radius = 1; radius <= previewThickness; radius++) {
            offsets.push({"x": -radius, "y": 0}, {"x": radius, "y": 0})
            offsets.push({"x": 0, "y": -radius}, {"x": 0, "y": radius})
            offsets.push({"x": -radius, "y": -radius}, {"x": radius, "y": -radius})
            offsets.push({"x": -radius, "y": radius}, {"x": radius, "y": radius})
        }
        return offsets
    }

    function speakerColor(style) {
        var speakers = overlayRoot.speakerColors || []
        for (var index = 0; index < speakers.length; ++index) {
            if (speakers[index].style === style)
                return speakers[index].color
        }
        return "#FFB547"
    }

    function subtitleText(segmentData) {
        if (overlayRoot.subtitleTextResolver)
            return overlayRoot.subtitleTextResolver(segmentData)
        if (segmentData.preview_text !== undefined)
            return String(segmentData.preview_text)
        return String(segmentData.text || "")
    }

    function refreshActiveSegments() {
        var candidates = overlayRoot.appBackend && overlayRoot.player
            ? overlayRoot.appBackend.activeSubtitleSegments(overlayRoot.player.position / 1000)
            : []
        var signature = JSON.stringify(candidates)
        if (signature !== overlayRoot.activeSignature) {
            overlayRoot.activeSignature = signature
            overlayRoot.activeSegments = candidates
        }
    }

    onPlayerChanged: overlayRoot.refreshActiveSegments()
    Connections {
        target: overlayRoot.player
        function onPositionChanged() { overlayRoot.refreshActiveSegments() }
    }
    Connections {
        target: overlayRoot.appBackend
        function onSegmentsChanged() { overlayRoot.refreshActiveSegments() }
    }

    Repeater {
        model: overlayRoot.activeSegments
        delegate: Text {
            id: overlayCaption
            required property int index
            required property var modelData
            property var segmentData: modelData || ({})
            objectName: overlayRoot.captionObjectPrefix + "-" + index
            width: Math.min(implicitWidth + 30, Math.max(1, parent.width - 30))
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: overlayRoot.rowMarginBase + Number(segmentData.layout_row || 0) * overlayRoot.previewRowMarginStep()
            text: overlayRoot.subtitleText(segmentData)
            color: overlayRoot.speakerColor(segmentData.speaker || "")
            font.family: segmentData.subtitle_font_family || "Yu Gothic UI"
            font.pixelSize: overlayRoot.previewPixelSize(segmentData.subtitle_font_scale)
            font.weight: Font.Bold
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            wrapMode: Text.Wrap
            Repeater {
                model: overlayRoot.outlineOffsets(overlayRoot.outlineThickness)
                delegate: Text {
                    required property var modelData
                    x: modelData.x
                    y: modelData.y
                    width: overlayCaption.width
                    height: overlayCaption.height
                    text: overlayCaption.text
                    color: overlayRoot.outlineColor
                    font.family: overlayCaption.font.family
                    font.pixelSize: overlayCaption.font.pixelSize
                    font.weight: overlayCaption.font.weight
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    wrapMode: Text.Wrap
                    z: -1
                }
            }
        }
    }
}
