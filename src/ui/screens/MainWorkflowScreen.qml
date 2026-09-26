pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import "../components"
import QtQuick.Layouts
import QtMultimedia

ApplicationWindow {
    id: root
    // qmllint disable unqualified
    property var appBackend: backend
    // qmllint enable unqualified
    readonly property var workflowCapabilities: {
        // Depend on backend notifications as well as the unsaved device choice.
        var snapshot = root.appBackend.workflow.actionCapabilities
        return snapshot ? root.appBackend.workflow.actionCapabilitiesForDevice(advancedSettingsPopup.selectedDevice) : ({})
    }
    property real timelinePixelsPerSecond: 34
    property alias editorPixelsPerSecond: subtitleEditorState.pixelsPerSecond
    property alias snapMilliseconds: subtitleEditorState.snapMilliseconds
    readonly property int defaultSubtitleFontSize: 50
    property alias subtitleFontSizePercent: advancedSettingsPopup.fontSizePercent
    property alias subtitleOutlineColor: advancedSettingsPopup.outlineColor
    property alias subtitleOutlineThickness: advancedSettingsPopup.outlineThickness
    readonly property int selectedSubtitleFontSize: advancedSettingsPopup.selectedFontSize
    readonly property string selectedSubtitleOutlineColor: root.subtitleOutlineColor
    readonly property int selectedSubtitleOutlineThickness: root.subtitleOutlineThickness
    property var projectSpeakerCache: root.appBackend.subtitles.projectSpeakers
    property var subtitleWaveformCache: root.appBackend.subtitles.subtitleWaveforms
    property var subtitleLayoutMetricsCache: root.appBackend.subtitles.subtitleLayoutMetrics
    property alias editorPositionCache: subtitleEditorState.positionMs
    property alias editorTimelineScrollX: subtitleEditorState.timelineScrollX
    property alias editorCaptionScrollY: subtitleEditorState.captionScrollY
    property real workspaceAudioTimelineScrollX: 0
    property real workspaceAudioSettingsScrollY: 0
    property int sharedSeekRevision: 0
    property bool applyingSharedSeek: false
    property real pendingSharedSourcePosition: -1
    property var pendingWelcomeTranscriptionRequest: null
    property alias editorDraftSegmentIndex: subtitleEditorState.draftSegmentIndex
    property alias editorDraftText: subtitleEditorState.draftText
    property string activeOverlay: ""
    property real cutSelectionStartMs: 0
    property real cutSelectionEndMs: 0
    property string selectedCutId: ""
    // Cut supplies only mode-specific content. The common player, mode state,
    // and source/output playhead remain owned by this workspace.
    property Component cutModeEditorContent: cutWorkspaceEditorComponent
    property Component cutModeSettingsContent: cutWorkspaceSettingsComponent
    property Component modeEditorContent: root.appBackend.workspace.currentEditMode === "subtitle"
        ? subtitleWorkspaceEditorComponent
        : (root.appBackend.workspace.currentEditMode === "audio"
            ? audioWorkspaceEditorComponent
            : root.cutModeEditorContent)
    property Component modeSettingsContent: root.appBackend.workspace.currentEditMode === "subtitle"
        ? subtitleWorkspaceSettingsComponent
        : (root.appBackend.workspace.currentEditMode === "audio"
            ? audioWorkspaceSettingsComponent
            : root.cutModeSettingsContent)
    readonly property bool editorMode: root.activeOverlay === "editor"
    readonly property bool mixerMode: root.activeOverlay === "mixer"
    readonly property bool dictionaryMode: root.activeOverlay === "dictionary"
    readonly property bool shortWorkspaceActive: root.appBackend
        && root.appBackend.workspace.currentWorkspace === "short-artifact"
    readonly property bool codexAuthenticated: root.appBackend
        && root.appBackend.ai.codexAuthState === "authenticated"
    readonly property string aiProviderLoginLabel: {
        var providerName = root.appBackend
            ? String(root.appBackend.ai.aiChatProviderName || "")
            : ""
        return providerName ? providerName + "ログイン" : "ログイン"
    }
    readonly property bool codexSidebarOverlay: root.width < 1400
    readonly property int codexSidebarWidth: root.codexAuthenticated && root.codexDrawerOpen
        && !root.loginInInspector ? 300 : 0
    readonly property int codexDrawerHeaderInset: root.codexAuthenticated && root.codexSidebarOverlay
        && !root.loginInInspector
        ? (root.codexDrawerOpen ? 310 : 104) : 0
    readonly property int codexDrawerBodyInset: root.codexAuthenticated && root.codexSidebarOverlay
        && root.codexDrawerOpen && !root.loginInInspector ? 310 : 0
    readonly property int codexWorkspaceRightInset: root.codexSidebarWidth > 0 && !root.codexSidebarOverlay
        ? root.codexSidebarWidth + 10 : 0
    readonly property int codexInteractiveRightInset: root.codexWorkspaceRightInset
        + root.codexDrawerHeaderInset
    // The provider-neutral login controls remain available while the short
    // workspace is open. Reserve their small top strip so they do not cover
    // the short workspace header; the common authenticated sidebar keeps its
    // existing right-side boundary.
    readonly property int shortWorkspaceAiAccessInset: root.shortWorkspaceActive
        && !root.codexAuthenticated ? 58 : 0
    property bool codexDrawerOpen: true
    property string inspectorTab: "settings"
    property bool previousCodexAuthenticated: false
    onCodexAuthenticatedChanged: {
        if (root.codexAuthenticated && !root.previousCodexAuthenticated) {
            root.codexDrawerOpen = true
            if (root.loginInInspector)
                root.inspectorTab = "codex"
        }
        root.previousCodexAuthenticated = root.codexAuthenticated
    }
    onEditorModeChanged: {
        if (root.editorMode)
            root.syncEditorPlayhead(root.editorPositionCache, true)
    }
    readonly property bool loginInInspector: mainWorkspace.visible && root.appBackend.projectLoaded
    property string editTool: "cut"
    property bool settingsExpanded: false
    property string colorTarget: ""
    property int colorTargetIndex: -1
    property bool acceptingSourceDrop: false

    width: 1520
    height: 940
    minimumWidth: 1220
    minimumHeight: 760
    visible: true
    title: "Subtitle Edit Bay"
    color: "#0B0F17"
    palette.window: "#131A26"
    palette.windowText: "#F8FAFC"
    palette.base: "#131A26"
    palette.text: "#F8FAFC"
    palette.button: "#1A2332"
    palette.buttonText: "#F8FAFC"
    palette.highlight: "#6366F1"
    palette.highlightedText: "#FFFFFF"

    readonly property color panel: "#131A26"
    readonly property color raised: "#1A2332"
    readonly property color border: "#243044"
    readonly property color textPrimary: "#F8FAFC"
    readonly property color textMuted: "#94A3B8"
    readonly property color acid: "#6366F1"
    readonly property color amber: "#F59E0B"
    readonly property color danger: "#EF4444"

    function openSpeakerColorPicker(target, index, currentColor) {
        root.colorTarget = target
        root.colorTargetIndex = index
        speakerColorDialog.selectedColor = currentColor || "#FFFFFF"
        speakerColorDialog.open()
    }

    ColorDialog {
        id: speakerColorDialog
        objectName: "speakerColorDialog"
        title: "話者の字幕色を選択"
        onAccepted: {
            var colorValue = selectedColor.toString()
            if (root.colorTarget === "source")
                root.appBackend.updateSpeakerColor(root.colorTargetIndex, colorValue)
            else if (root.colorTarget === "project")
                root.appBackend.subtitles.updateProjectSpeakerColor(root.colorTargetIndex, colorValue)
            root.colorTarget = ""
            root.colorTargetIndex = -1
        }
        onRejected: {
            root.colorTarget = ""
            root.colorTargetIndex = -1
        }
    }

    ColorDialog {
        id: outlineColorDialog
        objectName: "outlineColorDialog"
        title: "字幕の縁取り色を選択"
        onAccepted: advancedSettingsPopup.outlineColor = selectedColor.toString()
    }

    function openOutlineColorPicker(currentColor) {
        outlineColorDialog.selectedColor = currentColor
        outlineColorDialog.open()
    }

    function importSourceDrop(drop) {
        root.acceptingSourceDrop = false
        if (!drop.hasUrls)
            return
        root.appBackend.importDroppedSourceFiles(drop.urls)
        drop.acceptProposedAction()
    }

    function currentSettings() {
        var values = advancedSettingsPopup.settingsValues()
        values.reference_audio = root.appBackend.speakers.length > 0 ? sourcePopup.referenceAudioValue : ""
        values.reference_track = root.appBackend.audioTracks.length > 0 ? sourcePopup.referenceTrackValue : ""
        values.alignment_offset_adjustment = Number(sourcePopup.manualOffsetText || 0)
        return values
    }

    function coalesceSetting(value, fallback) {
        return value === undefined || value === null ? fallback : value
    }

    function userFacingStateLabel(value) {
        var text = String(value || "")
        if (text.length === 0)
            return ""
        var labels = {
            "starting": "準備中",
            "authenticating": "接続中",
            "running": "処理中",
            "cancelling": "キャンセル中",
            "cancelled": "キャンセル済み",
            "idle": "待機中",
            "ready": "準備完了",
            "complete": "完了",
            "completed": "完了",
            "success": "完了",
            "error": "エラー",
            "disabled": "利用できません"
        }
        var key = text.toLowerCase()
        if (labels[key] !== undefined)
            return labels[key]
        return /[\u3040-\u30ff\u3400-\u9fff]/.test(text) ? text : "処理状況を確認中"
    }

    function userFacingStatusLabel(stage, status) {
        var stageLabels = {
            "READY": "準備完了",
            "STARTING": "準備中",
            "CHECK": "確認中",
            "BUSY": "処理中",
            "TRANSCRIBE": "文字起こし中",
            "COMPLETE": "完了",
            "ERROR": "エラー",
            "UPDATE": "更新中",
            "RENDER": "書き出し中"
        }
        var stageLabel = stageLabels[String(stage || "").toUpperCase()] || ""
        var statusLabel = root.userFacingStateLabel(status)
        if (!stageLabel)
            return statusLabel
        if (!statusLabel || statusLabel === stageLabel || statusLabel === "処理状況を確認中")
            return stageLabel
        return stageLabel + " · " + statusLabel
    }

    function formatBytes(value) {
        var bytes = Number(value || 0)
        if (!isFinite(bytes) || bytes <= 0)
            return "0 KB"
        if (bytes < 1024 * 1024)
            return (bytes / 1024).toFixed(1) + " KB"
        return (bytes / (1024 * 1024)).toFixed(1) + " MB"
    }

    function syncSettings() {
        var value = root.appBackend.settings
        advancedSettingsPopup.applySettings(value)
        sourcePopup.manualOffsetText = Number(coalesceSetting(value.alignment_offset_adjustment, 0)).toFixed(3)
    }
    function toggleSettingsPopup() {
        if (advancedSettingsPopup.opened)
            advancedSettingsPopup.close()
        else
            advancedSettingsPopup.open()
    }

    function transcriptionBlockReason() {
        return String(root.workflowCapabilities.transcriptionReason || "")
    }

    // Shared entry for the workspace transcription tool.
    function requestTranscription() {
        if (!root.workflowCapabilities.canTranscribe)
            return
        if (!root.commitPendingEdits())
            return
        if (root.appBackend.projectLoaded)
            transcriptionMergeDialog.open()
        else if (root.appBackend.transcriptionProjectExists())
            overwriteProjectDialog.open()
        else
            root.appBackend.workflow.startTranscription(root.currentSettings(), false)
    }

    ProjectStartFlow {
        id: projectStartFlow
        appBackend: root.appBackend
        workflowCapabilities: root.workflowCapabilities
        settingsProvider: root.currentSettings
        onSourceSettingsRequested: sourcePopup.open()
        onOverwriteConfirmationRequested: function(request) {
            root.pendingWelcomeTranscriptionRequest = request
            overwriteProjectDialog.open()
        }
    }

    function performSubtitleEdit(action, atSeconds) {
        if (root.appBackend.running)
            return
        if (!root.commitPendingEdits())
            return
        switch (action) {
        case "add": root.appBackend.subtitles.addSegment(atSeconds); break
        case "delete": root.appBackend.subtitles.deleteSelectedSegment(); break
        case "split": root.appBackend.subtitles.splitSelectedSegment(atSeconds); break
        case "undo": root.appBackend.subtitles.undoSubtitleEdit(); break
        case "redo": root.appBackend.subtitles.redoSubtitleEdit(); break
        }
    }

    SubtitleEditorState {
        id: subtitleEditorState
        subtitles: root.appBackend.subtitles
        running: root.appBackend.running
        projectPath: root.appBackend.projectPath
        previewEnabled: root.editorMode || root.appBackend.workspace.currentEditMode === "subtitle"
    }
    readonly property var subtitleEditorColors: ({
        panel: root.panel,
        raised: root.raised,
        border: root.border,
        textPrimary: root.textPrimary,
        textMuted: root.textMuted,
        acid: root.acid,
        amber: root.amber,
        danger: root.danger
    })

    function editModeTitle(mode) {
        return {"subtitle": "字幕", "cut": "カット", "audio": "音量"}[mode] || "編集"
    }

    function editModeDescription(mode) {
        if (mode === "subtitle")
            return "字幕の内容とタイミングを編集します"
        if (mode === "audio")
            return "動画と各音声トラックのバランスを調整します"
        return "出力動画に残す範囲を編集します"
    }

    function selectWorkspaceMode(mode) {
        var changed = root.appBackend.workspace.selectEditMode(mode)
        return changed || root.appBackend.workspace.currentEditMode === mode
    }

    function syncSharedPlayerToPlayhead() {
        // The shared player always renders source media. Output-timeline
        // positions are converted once by the backend's shared mapping.
        var sourcePosition = Number(root.appBackend.workspace.editorPlayhead.sourcePositionMs || 0)
        if (Math.abs(mainPlayer.position - sourcePosition) <= 1)
            return false
        root.pendingSharedSourcePosition = sourcePosition
        sharedSeekGuardTimer.restart()
        root.applyingSharedSeek = true
        mainPlayer.position = sourcePosition
        root.applyingSharedSeek = false
        root.sharedSeekRevision += 1
        return true
    }

    function seekSharedPlayer(positionMilliseconds, basis) {
        var position = Math.max(0, Math.round(Number(positionMilliseconds) || 0))
        var timelineBasis = basis || String(root.appBackend.workspace.editorPlayhead.basis || "source")
        var changed = root.appBackend.workspace.setEditorPlayhead(position, timelineBasis)
        if (!changed)
            root.syncSharedPlayerToPlayhead()
    }

    function enforceCutPreview(positionMilliseconds) {
        if (!root.appBackend.workspace.cutTimeline.hasCuts
                || mainPlayer.playbackState !== MediaPlayer.PlayingState)
            return false
        var current = Math.max(0, Math.round(Number(positionMilliseconds) || 0))
        var next = root.appBackend.workspace.nextCutPreviewSourceMs(current)
        if (next <= current + 1)
            return false
        root.seekSharedPlayer(next, "source")
        if (next >= Number(root.appBackend.workspace.cutTimeline.sourceDuration || 0) * 1000)
            mainPlayer.pause()
        return true
    }

    function setCutSelection(cutId, sourceStartMs, sourceEndMs) {
        root.selectedCutId = String(cutId || "")
        root.cutSelectionStartMs = Math.max(0, Number(sourceStartMs || 0))
        root.cutSelectionEndMs = Math.max(root.cutSelectionStartMs, Number(sourceEndMs || 0))
    }

    function closeSettingsPopup() {
        if (advancedSettingsPopup.opened)
            advancedSettingsPopup.close()
    }

    function syncEditorPlayhead(positionMs, syncSelection) {
        var resolvedPosition = Math.max(0, Math.round(Number(positionMs) || 0))
        root.appBackend.workspace.setWorkspacePlayerState(
            "normal-video",
            resolvedPosition,
            mainPlayer.playbackState === MediaPlayer.PlayingState
        )
        if (root.pendingSharedSourcePosition >= 0) {
            if (Math.abs(resolvedPosition - root.pendingSharedSourcePosition) > 80)
                return
            root.pendingSharedSourcePosition = -1
            sharedSeekGuardTimer.stop()
        } else if (!root.applyingSharedSeek) {
            // The shared player always reports source-media positions. Keeping
            // that basis here avoids applying an output-to-source mapping twice.
            root.appBackend.workspace.setEditorPlayhead(resolvedPosition, "source")
        }
        if (syncSelection
                && root.editorDraftSegmentIndex < 0
                && (root.editorMode
                    || (root.activeOverlay === ""
                        && root.appBackend.workspace.currentEditMode === "subtitle")))
            root.appBackend.subtitles.selectSegmentAtTime(
                Number(root.appBackend.workspace.editorPlayhead.sourcePositionMs) / 1000
            )
    }

    function syncEditorSelectionFromActiveSegments(activeSegments) {
        var subtitleSelectionActive = root.editorMode
            || (root.activeOverlay === "" && root.appBackend.workspace.currentEditMode === "subtitle")
        if (!subtitleSelectionActive
                || root.editorDraftSegmentIndex >= 0
                || !activeSegments
                || activeSegments.length === 0)
            return
        var sourceIndex = Number(activeSegments[activeSegments.length - 1].sourceIndex)
        if (isFinite(sourceIndex) && sourceIndex >= 0)
            root.appBackend.subtitles.selectSegment(Math.floor(sourceIndex))
    }

    function openEditorScreen() {
        root.closeSettingsPopup()
        root.appBackend.workspace.selectEditMode("subtitle")
        if (!root.mixerMode) {
            root.editorPositionCache = mainPlayer.position
            mainPlayer.pause()
        } else
            root.appBackend.audio.stopAudioMixerPreview()
        root.activeOverlay = "editor"
    }

    function closeEditorScreen() {
        if (!root.commitPendingEdits())
            return false
        root.editorPositionCache = mainPlayer.position
        mainPlayer.pause()
        mainPlayer.videoOutput = mainVideo
        root.activeOverlay = ""
        return true
    }

    function openMixerScreen() {
        root.closeSettingsPopup()
        if (!root.appBackend.workspace.selectEditMode("audio") && root.appBackend.workspace.currentEditMode !== "audio")
            return
        root.editorPositionCache = mainPlayer.position
        mainPlayer.pause()
        root.appBackend.audio.prepareAudioMixerPreview()
        root.activeOverlay = "mixer"
    }

    function closeMixerScreen() {
        root.appBackend.audio.stopAudioMixerPreview()
        mainPlayer.position = root.editorPositionCache
        root.activeOverlay = ""
    }

    function openDictionaryScreen() {
        if (root.appBackend.running)
            return
        root.closeSettingsPopup()
        root.editorPositionCache = mainPlayer.position
        mainPlayer.pause()
        root.appBackend.audio.stopAudioMixerPreview()
        root.activeOverlay = "dictionary"
    }

    function closeDictionaryScreen() {
        mainPlayer.position = root.editorPositionCache
        root.activeOverlay = ""
    }

    function openShortWorkspace() {
        if (root.appBackend.running)
            return
        root.closeSettingsPopup()
        root.editorPositionCache = mainPlayer.position
        mainPlayer.pause()
        root.appBackend.audio.stopAudioMixerPreview()
        root.appBackend.workspace.setWorkspacePlayerState("normal-video", mainPlayer.position, false)
        root.appBackend.workspace.switchWorkspace("short-artifact")
    }

    function closeShortWorkspace() {
        if (!root.appBackend.workspace.switchWorkspace("normal-video"))
            return
        var playerState = root.appBackend.workspace.workspacePlayerState
        mainPlayer.position = playerState
            ? Number(playerState.positionMs || 0)
            : root.editorPositionCache
    }

    function commitInputMethod() {
        // 変換中に保存するとOSごとの確定結果が異なるため、先に利用者の確定を待つ。
        var focusedInput = root.activeFocusItem
        // qmllint disable missing-property
        var composing = focusedInput && focusedInput.inputMethodComposing === true
        // qmllint enable missing-property
        if (composing) {
            root.appBackend.reportPendingInputMethod()
            return false
        }
        // Qt.inputMethodは型情報上QObjectだが、実体のQInputMethodはcommit()を公開する。
        // qmllint disable missing-property
        Qt.inputMethod.commit()
        composing = focusedInput && focusedInput.inputMethodComposing === true
        // qmllint enable missing-property
        if (composing) {
            root.appBackend.reportPendingInputMethod()
            return false
        }
        return true
    }

    function commitPendingEdits() {
        if (root.appBackend.running)
            return true
        // OSによるクリック時の差を避け、フォーカス終了による入力反映を完了する。
        if (!root.commitInputMethod())
            return false
        root.contentItem.forceActiveFocus()
        subtitleEditorState.commitSubtitleDraft()
        subtitleEditorState.commitTimeDraft()
        return true
    }

    // Item参照は、保存に伴って入力欄が破棄された場合にnullになる。
    property Item saveShortcutFocusTarget: null

    function textSelection(item: var): var {
        if (!item || item.cursorPosition === undefined || item.select === undefined)
            return null
        return {start: item.selectionStart, end: item.selectionEnd, cursor: item.cursorPosition}
    }

    function restoreTextSelection(item: var, selection: var) {
        if (!item || !selection)
            return
        if (selection.cursor === selection.start)
            item.select(selection.end, selection.start)
        else
            item.select(selection.start, selection.end)
    }

    function saveProjectFromShortcut() {
        if (!root.commitInputMethod())
            return false
        root.saveShortcutFocusTarget = root.activeFocusItem
        var selection = root.textSelection(root.saveShortcutFocusTarget)
        var saved = root.saveProject()
        if (root.saveShortcutFocusTarget
                && root.saveShortcutFocusTarget.visible
                && root.saveShortcutFocusTarget.enabled) {
            root.saveShortcutFocusTarget.forceActiveFocus()
            root.restoreTextSelection(root.saveShortcutFocusTarget, selection)
        }
        root.saveShortcutFocusTarget = null
        return saved
    }

    function saveProject() {
        if (root.appBackend.running)
            return false
        if (!root.commitPendingEdits())
            return false
        return root.appBackend.saveProject()
    }

    function browseProjectFile() {
        if (!root.commitPendingEdits())
            return
        root.appBackend.browseProjectFile()
    }

    function browseProjectSaveAs() {
        if (!root.commitPendingEdits())
            return
        root.appBackend.browseProjectSaveAs()
    }

    function renderVideo() {
        if (!root.commitPendingEdits())
            return
        root.appBackend.workflow.renderVideo(root.currentSettings())
    }

    function buildSubtitlePreview() {
        if (!root.commitPendingEdits())
            return
        root.appBackend.subtitles.buildSubtitlePreview(root.currentSettings())
    }

    function renderFromEditor() {
        if (!root.closeEditorScreen())
            return
        root.renderVideo()
    }

    function stamp(seconds) {
        var safe = Math.max(0, Number(seconds) || 0)
        var hours = Math.floor(safe / 3600)
        var minutes = Math.floor((safe % 3600) / 60)
        var remainder = (safe % 60).toFixed(2)
        return (hours > 0 ? String(hours).padStart(2, "0") + ":" : "")
            + String(minutes).padStart(2, "0") + ":" + String(remainder).padStart(5, "0")
    }

    component PanelTitle: Text {
        color: root.textMuted
        font.family: "Yu Gothic UI"
        font.pixelSize: 10
        font.weight: Font.Bold
        font.letterSpacing: 1.0
    }

    component SmallButton: Button {
        id: smallControl
        implicitHeight: 32
        contentItem: Text {
            text: smallControl.text
            color: smallControl.enabled ? root.textPrimary : "#59635D"
            font.family: "Yu Gothic UI"
            font.pixelSize: 10
            font.weight: Font.DemiBold
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 7
            color: smallControl.down ? "#303B35" : (smallControl.hovered ? "#27312C" : "#1A211E")
            border.color: smallControl.activeFocus ? root.acid : root.border
        }
    }

    component CompactSpinBox: SpinBox {
        id: compactSpin
        implicitWidth: 106
        implicitHeight: 34
        editable: true
        font.family: "Cascadia Mono"
        font.pixelSize: 11
        contentItem: TextInput {
            z: 1
            text: compactSpin.textFromValue(compactSpin.value, compactSpin.locale)
            color: root.textPrimary
            selectionColor: root.acid
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
            color: compactSpin.up.pressed ? "#303B35" : "transparent"
            border.color: root.border
            Text { anchors.centerIn: parent; text: "+"; color: root.textPrimary; font.pixelSize: 18 }
        }
        down.indicator: Rectangle {
            width: 30
            height: compactSpin.height
            color: compactSpin.down.pressed ? "#303B35" : "transparent"
            border.color: root.border
            Text { anchors.centerIn: parent; text: "−"; color: root.textPrimary; font.pixelSize: 18 }
        }
        background: Rectangle {
            radius: 6
            color: "#101512"
            border.color: compactSpin.activeFocus ? root.acid : root.border
        }
    }

    component TimeField: TextField {
        id: timeControl
        horizontalAlignment: TextInput.AlignRight
        color: root.textPrimary
        selectionColor: root.acid
        font.family: "Cascadia Mono"
        font.pixelSize: 11
        validator: DoubleValidator { bottom: 0; decimals: 3 }
        background: Rectangle {
            radius: 6
            color: "#101512"
            border.color: timeControl.activeFocus ? root.acid : root.border
        }
    }

    header: WorkspaceHeader {
        objectName: "workspaceHeader"
        visible: !root.editorMode && !root.mixerMode && !root.dictionaryMode && !root.shortWorkspaceActive
        projectLoaded: root.appBackend.projectLoaded
        projectName: root.appBackend.projectName
        projectDirty: root.appBackend.projectDirty
        sourcePath: root.appBackend.sourceSelection.video
        workspaceKind: root.appBackend.workspace.currentWorkspace
        currentEditMode: root.appBackend.workspace.currentEditMode
        activityText: root.userFacingStatusLabel(root.appBackend.stage, root.appBackend.status)
        applicationVersion: root.appBackend.applicationInfo.version
        running: root.appBackend.running
        updateBusy: root.appBackend.updates.updateBusy
        rightInset: root.codexDrawerHeaderInset
        outputFolderAvailable: Boolean(root.appBackend.videoOutputDirectory)
        canRender: Boolean(root.workflowCapabilities.canRenderNormal || root.workflowCapabilities.normalRenderNeedsOutput)
        renderNeedsOutput: Boolean(root.workflowCapabilities.normalRenderNeedsOutput)
        renderBlockReason: String(root.workflowCapabilities.normalRenderReason || "")
        aiAuthenticated: root.codexAuthenticated
        aiLoginAvailable: Boolean(root.appBackend.ai.aiChatLoginAvailable)
        aiAuthState: root.appBackend.ai.codexAuthState
        aiConnectionState: root.appBackend.ai.codexConnectionState
        panelColor: root.panel
        raisedColor: root.raised
        borderColor: root.border
        textColor: root.textPrimary
        mutedColor: root.textMuted
        accentColor: root.acid
        warningColor: root.amber
        onUpdateCheckRequested: root.appBackend.updates.checkForUpdates()
        onProjectOpenRequested: root.browseProjectFile()
        onSourceSettingsRequested: sourcePopup.open()
        onSaveRequested: root.saveProject()
        onOutputFolderRequested: root.appBackend.openOutputFolder()
        onAiAssistantRequested: {
            if (root.loginInInspector) {
                root.inspectorTab = root.inspectorTab === "codex" ? "settings" : "codex"
            } else if (root.codexAuthenticated) {
                root.codexDrawerOpen = !root.codexDrawerOpen
            } else if (root.appBackend.ai.codexAuthState === "login_pending") {
                root.appBackend.ai.openAIProviderLoginPage()
            } else if (["error", "disconnected"].indexOf(root.appBackend.ai.codexConnectionState) >= 0) {
                root.appBackend.ai.reconnectAIChat()
            } else {
                root.appBackend.ai.startAIProviderLogin()
            }
        }
        onShortWorkspaceRequested: root.openShortWorkspace()
        onRenderRequested: root.renderVideo()
    }

    Dialog {
        id: updateDialog
        objectName: "updateDialog"
        anchors.centerIn: parent
        modal: true
        title: "更新の確認"
        visible: root.appBackend.updates.updateAvailable && (!root.appBackend.updates.updateBusy || root.appBackend.updates.updateDownloadActive)
        standardButtons: Dialog.NoButton
        width: 500
        height: 320
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 8
            Text { text: "現在のバージョン: " + root.appBackend.updates.updateCurrentVersion; color: root.textPrimary; font.family: "Yu Gothic UI"; font.pixelSize: 12 }
            Text { text: "最新バージョン: " + root.appBackend.updates.updateLatestVersion; color: root.acid; font.family: "Yu Gothic UI"; font.pixelSize: 12 }
            Text {
                text: root.appBackend.updates.updateReleaseNotes
                color: root.textMuted
                font.family: "Yu Gothic UI"
                font.pixelSize: 10
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                Layout.fillHeight: true
            }
            Text {
                visible: root.appBackend.updates.updatePackageSize > 0
                text: "ダウンロードサイズ: " + root.formatBytes(root.appBackend.updates.updatePackageSize)
                color: root.textMuted
                font.family: "Yu Gothic UI"
                font.pixelSize: 11
                Layout.fillWidth: true
            }
            ProgressBar {
                objectName: "updateDownloadProgressBar"
                visible: root.appBackend.updates.updateDownloadActive
                from: 0
                to: root.appBackend.updates.updateDownloadTotal > 0 ? root.appBackend.updates.updateDownloadTotal : 1
                value: root.appBackend.updates.updateDownloadBytes
                Layout.fillWidth: true
            }
            RowLayout {
                Button { objectName: "applyUpdateButton"; text: root.appBackend.updates.updatePackageReady ? "再起動して更新" : "アップデート"; visible: root.appBackend.stage !== "UPDATE"; enabled: !root.appBackend.running && !root.appBackend.projectDirty && !root.appBackend.updates.updateBusy; onClicked: root.appBackend.updates.applyUpdate() }
                Button { objectName: "cancelUpdateDownloadButton"; text: "ダウンロードをキャンセル"; visible: root.appBackend.updates.updateDownloadActive; enabled: true; onClicked: root.appBackend.updates.cancelUpdateDownload() }
                Button { objectName: "restartApplicationButton"; text: "再起動"; visible: root.appBackend.stage === "UPDATE" && !root.appBackend.running; enabled: !root.appBackend.running; onClicked: root.appBackend.updates.restartApplication() }
                Button { objectName: "dismissUpdateDialogButton"; text: "閉じる"; enabled: !root.appBackend.running && !root.appBackend.updates.updateBusy; onClicked: { root.appBackend.updates.dismissUpdateInfo(); updateDialog.close(); } }
            }
        }
    }

    AdvancedSettingsPopup {
        id: advancedSettingsPopup
        appBackend: root.appBackend
        colors: root.subtitleEditorColors
        defaultSubtitleFontSize: root.defaultSubtitleFontSize
        // Keep the popup clear of the action bar's right-aligned toggle,
        // including at the 1220px minimum window width.
        x: Math.max(12, root.width - width - 430)
        y: contextActionBar.y + contextActionBar.height + 10
        width: Math.min(360, root.width - 24)
        height: Math.max(0, Math.min(620, root.contentItem.height - y - 12))
        onOpened: root.settingsExpanded = true
        onClosed: root.settingsExpanded = false
        onSaveRequested: root.appBackend.saveSettings(root.currentSettings())
        onOutlineColorRequested: function(currentColor) {
            root.openOutlineColorPicker(currentColor)
        }
    }

    Component {
        id: subtitleWorkspaceEditorComponent
        SubtitleWorkspaceEditor {
            onEditRequested: function(action, atSeconds) { root.performSubtitleEdit(action, atSeconds) }
            onSaveRequested: root.saveProject()
            appBackend: root.appBackend
            player: mainPlayer
            editorState: subtitleEditorState
            colors: root.subtitleEditorColors
            formatTimestamp: root.stamp
            onSeekRequested: function(positionMs) { root.seekSharedPlayer(positionMs, "source") }
            onPreviewRequested: root.buildSubtitlePreview()
        }
    }

    Component {
        id: audioWorkspaceEditorComponent

        AudioWorkspaceEditor {
            appBackend: root.appBackend
            player: mainPlayer
            previewBridge: workspaceAudioBridge
            colors: root.subtitleEditorColors
            formatTimestamp: root.stamp
            speakers: root.projectSpeakerCache
            pixelsPerSecond: root.timelinePixelsPerSecond
            savedViewportX: root.workspaceAudioTimelineScrollX
            onSeekRequested: function(positionMs) { root.seekSharedPlayer(positionMs, "source") }
            onViewportChangedByUser: function(viewportX) { root.workspaceAudioTimelineScrollX = viewportX }
        }
    }

    Component {
        id: subtitleWorkspaceSettingsComponent

        SubtitleModeSettings {
            objectName: "workspaceSubtitleSettings"
            backend: root.appBackend
            editorState: subtitleEditorState
            speakers: root.projectSpeakerCache
            fontChoices: root.appBackend.subtitles.fontChoices
            panelColor: root.panel
            raisedColor: root.raised
            borderColor: root.border
            textColor: root.textPrimary
            mutedColor: root.textMuted
            accentColor: root.acid
            savedContentY: root.editorCaptionScrollY
            beginDraft: subtitleEditorState.beginSubtitleDraft
            updateDraft: subtitleEditorState.updateSubtitleDraft
            commitDraft: subtitleEditorState.commitSubtitleDraft
            pendingDraftTextForSegment: subtitleEditorState.pendingTextForSegment
            onSeekRequested: function(positionMilliseconds) {
                root.seekSharedPlayer(positionMilliseconds, "source")
            }
            onSpeakerColorRequested: function(speakerIndex, currentColor) {
                root.openSpeakerColorPicker("project", speakerIndex, currentColor)
            }
            onContentYChangedByUser: function(value) {
                root.editorCaptionScrollY = value
            }
        }
    }

    Component {
        id: audioWorkspaceSettingsComponent

        AudioModeSettings {
            objectName: "workspaceAudioSettings"
            backend: root.appBackend
            panelColor: root.panel
            raisedColor: root.raised
            borderColor: root.border
            textColor: root.textPrimary
            mutedColor: root.textMuted
            accentColor: root.acid
            warningColor: root.amber
            savedContentY: root.workspaceAudioSettingsScrollY
            onContentYChangedByUser: function(value) {
                root.workspaceAudioSettingsScrollY = value
            }
        }
    }

    Component {
        id: cutWorkspaceEditorComponent

        CutModeTimeline {
            objectName: "workspaceCutEditor"
            backend: root.appBackend
            player: mainPlayer
            timeline: root.appBackend.workspace.cutTimeline
            selectionStartMs: root.cutSelectionStartMs
            selectionEndMs: root.cutSelectionEndMs
            selectedCutId: root.selectedCutId
            panelColor: root.panel
            raisedColor: root.raised
            borderColor: root.border
            textColor: root.textPrimary
            mutedColor: root.textMuted
            accentColor: root.acid
            warningColor: root.amber
            cutColor: root.danger
            onRangeSelected: function(sourceStartMs, sourceEndMs) {
                root.setCutSelection("", sourceStartMs, sourceEndMs)
            }
            onCutSelected: function(cutId, sourceStartMs, sourceEndMs) {
                root.setCutSelection(cutId, sourceStartMs, sourceEndMs)
            }
            onSeekRequested: function(sourcePositionMs) {
                root.seekSharedPlayer(sourcePositionMs, "source")
            }
        }
    }

    Component {
        id: cutWorkspaceSettingsComponent

        CutModeSettings {
            objectName: "workspaceCutSettings"
            backend: root.appBackend
            timeline: root.appBackend.workspace.cutTimeline
            selectionStartMs: root.cutSelectionStartMs
            selectionEndMs: root.cutSelectionEndMs
            selectedCutId: root.selectedCutId
            panelColor: root.panel
            raisedColor: root.raised
            borderColor: root.border
            textColor: root.textPrimary
            mutedColor: root.textMuted
            accentColor: root.acid
            warningColor: root.amber
            cutColor: root.danger
            onSelectionChanged: function(sourceStartMs, sourceEndMs) {
                root.setCutSelection(root.selectedCutId, sourceStartMs, sourceEndMs)
            }
            onCutSelected: function(cutId, sourceStartMs, sourceEndMs) {
                root.setCutSelection(cutId, sourceStartMs, sourceEndMs)
            }
        }
    }

    AudioPreviewBridge {
        id: workspaceAudioBridge
        width: 0
        height: 0
        backend: root.appBackend
        player: mainPlayer
        active: root.appBackend.workspace.currentEditMode === "audio" && mainWorkspace.visible
        seekRevision: root.sharedSeekRevision
    }

    Connections {
        target: root.appBackend.workspace

        function onEditorPlayheadChanged() {
            root.syncSharedPlayerToPlayhead()
        }

    }

    Timer {
        id: sharedSeekGuardTimer
        interval: 400
        repeat: false
        onTriggered: root.pendingSharedSourcePosition = -1
    }

    RowLayout {
        id: mainWorkspace
        objectName: "mainWorkspace"
        visible: !root.editorMode && !root.mixerMode && !root.dictionaryMode && !root.shortWorkspaceActive
        anchors.fill: parent
        anchors.leftMargin: 12
        anchors.topMargin: 12
        anchors.bottomMargin: 12
        anchors.rightMargin: root.codexWorkspaceRightInset + 12
        spacing: 10

        ProjectStartScreen {
            visible: !root.appBackend.projectLoaded
            Layout.fillWidth: true
            Layout.fillHeight: true
            appBackend: root.appBackend
            colors: root.subtitleEditorColors
            transcriptionBlockReason: projectStartFlow.transcriptionBlockReason
            onNewVideoEditRequested: projectStartFlow.startNewVideoEdit()
            onOpenProjectRequested: root.browseProjectFile()
            onStartTranscriptionRequested: projectStartFlow.startTranscription()
            onSourceSettingsRequested: sourcePopup.open()
            onDictionaryRequested: root.openDictionaryScreen()
            onProcessingSettingsRequested: root.toggleSettingsPopup()
        }

        EditorModeRail {
            id: editorModeRail
            objectName: "editorModeRail"
            visible: root.appBackend.projectLoaded
            Layout.preferredWidth: 70
            Layout.minimumWidth: 86
            Layout.minimumHeight: 0
            Layout.maximumHeight: mainWorkspace.height
            Layout.fillHeight: true
            Layout.alignment: Qt.AlignTop
            currentMode: root.appBackend.workspace.currentEditMode
            capabilities: root.appBackend.workspace.editorModeCapabilities
            panelColor: root.panel
            raisedColor: root.raised
            borderColor: root.border
            textColor: root.textPrimary
            mutedColor: root.textMuted
            accentColor: root.acid
            onModeRequested: function(mode) { root.selectWorkspaceMode(mode) }
        }

        ColumnLayout {
            id: workspaceContent
            objectName: "workspaceContent"
            visible: root.appBackend.projectLoaded
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 10

            RowLayout {
                id: workspaceUpperArea
                objectName: "workspaceUpperArea"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: root.height <= 800 ? 190 : 300
                spacing: 10

                MediaBinPanel {
                    objectName: "workspaceMediaBin"
                    Layout.preferredWidth: root.width >= 1400 ? 280 : 230
                    Layout.minimumWidth: 210
                    Layout.fillHeight: true
                    backend: root.appBackend
                    panelColor: root.panel
                    raisedColor: root.raised
                    borderColor: root.border
                    textColor: root.textPrimary
                    mutedColor: root.textMuted
                    accentColor: root.acid
                    warningColor: root.amber
                    dangerColor: root.danger
                    onSourceSettingsRequested: sourcePopup.open()
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 6

                    ContextActionBar {
                        id: contextActionBar
                        compact: true
                        objectName: "contextActionBar"
                        Layout.fillWidth: true
                        projectLoaded: root.appBackend.projectLoaded
                        running: root.appBackend.running
                        activeJob: root.appBackend.workflow.activeJob
                        canCreateProject: Boolean(root.appBackend.sourceSelection.video)
                        canStartTranscription: Boolean(root.workflowCapabilities.canTranscribe)
                        canRenderNormal: Boolean(root.workflowCapabilities.canRenderNormal || root.workflowCapabilities.normalRenderNeedsOutput)
                        renderNeedsOutput: Boolean(root.workflowCapabilities.normalRenderNeedsOutput)
                        renderBlockReason: String(root.workflowCapabilities.normalRenderReason || "")
                        blockReason: root.transcriptionBlockReason()
                        audioMixerAvailable: root.appBackend.audio.audioMixerAvailable
                        mixerBlockReason: root.appBackend.projectLoaded && !root.appBackend.audio.audioMixerAvailable ? "音声トラックがないため音量を調整できません" : ""
                        subtitleAvailable: root.appBackend.subtitles.segmentCount > 0
                        outputFolderAvailable: Boolean(root.appBackend.videoOutputDirectory)
                        settingsExpanded: root.settingsExpanded
                        onSettingsRequested: root.toggleSettingsPopup()
                        onDictionaryRequested: root.openDictionaryScreen()
                        onCreateProjectRequested: root.appBackend.createEmptyProject()
                        onStartTranscriptionRequested: root.requestTranscription()
                        onEditorRequested: root.openEditorScreen()
                        onMixerRequested: root.openMixerScreen()
                        onShortModeRequested: root.openShortWorkspace()
                        onRenderRequested: root.renderVideo()
                        onSaveOrStopRequested: {
                            if (!root.appBackend.running)
                                root.appBackend.saveSettings(root.currentSettings())
                            else if (root.appBackend.workflow.activeJob !== "update")
                                root.appBackend.workflow.cancelProcessing()
                        }
                        onOutputFolderRequested: root.appBackend.openOutputFolder()
                    }
                    Rectangle {
                        objectName: "mainVideoPanel"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        radius: 12
                        color: "#06080D"
                        border.color: root.border
                        clip: true
                        MediaPlayer {
                            id: mainPlayer
                            objectName: "mainWorkspacePlayer"
                            source: root.appBackend.previewUrl
                            videoOutput: mainVideo
                            audioOutput: AudioOutput {
                                objectName: "mainWorkspaceAudioOutput"
                                volume: 0.7
                                muted: workspaceAudioBridge.muteSourceAudio
                            }
                            onPositionChanged: {
                                if (!mainSeek.pressed)
                                    mainSeek.value = mainPlayer.position
                                if (root.enforceCutPreview(mainPlayer.position))
                                    return
                                var completedPendingSeek = false
                                if (root.pendingSharedSourcePosition >= 0
                                        && Math.abs(mainPlayer.position - root.pendingSharedSourcePosition) <= 80) {
                                    root.pendingSharedSourcePosition = -1
                                    sharedSeekGuardTimer.stop()
                                    completedPendingSeek = true
                                }
                                if (!completedPendingSeek
                                        && mainPlayer.playbackState !== MediaPlayer.PlayingState)
                                    root.syncEditorPlayhead(mainPlayer.position, true)
                            }
                            onDurationChanged: mainSeek.to = Math.max(1, mainPlayer.duration)
                            onPlaybackStateChanged: root.syncEditorPlayhead(mainPlayer.position, true)
                        }
                        Timer {
                            // Keep the shared playhead current; the overlay handles exact subtitle changes.
                            interval: 100
                            repeat: true
                            running: mainPlayer.playbackState === MediaPlayer.PlayingState
                            onTriggered: {
                                if (!root.enforceCutPreview(mainPlayer.position))
                                    root.syncEditorPlayhead(mainPlayer.position, false)
                            }
                        }
                        VideoOutput { id: mainVideo; anchors.fill: parent; anchors.bottomMargin: 58; fillMode: VideoOutput.PreserveAspectFit }
                        SubtitleOverlay {
                            id: mainSubtitleOverlay
                            anchors.fill: mainVideo
                            appBackend: root.appBackend
                            player: mainPlayer
                            layoutMetrics: root.subtitleLayoutMetricsCache
                            active: mainWorkspace.visible
                            captionObjectPrefix: "mainSubtitleOverlayCaption"
                            baseFontSize: root.selectedSubtitleFontSize
                            defaultSubtitleFontSize: root.defaultSubtitleFontSize
                            outlineColor: root.selectedSubtitleOutlineColor
                            outlineThickness: root.selectedSubtitleOutlineThickness
                            speakerColors: root.projectSpeakerCache
                            subtitleTextResolver: function(segmentData) { return subtitleEditorState.subtitlePreviewText(segmentData) }
                            onActiveSegmentsChanged: root.syncEditorSelectionFromActiveSegments(
                                mainSubtitleOverlay.activeSegments
                            )
                        }
                        ColumnLayout {
                            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                            anchors.margins: 12; spacing: 2
                            Slider { id: mainSeek; Layout.fillWidth: true; from: 0; to: 1; onMoved: root.seekSharedPlayer(value, "source") }
                            RowLayout { Layout.fillWidth: true
                                ToolButton { objectName: "mainPreviewPlayButton"; text: mainPlayer.playbackState === MediaPlayer.PlayingState ? "Ⅱ" : "▶"; onClicked: mainPlayer.playbackState === MediaPlayer.PlayingState ? mainPlayer.pause() : mainPlayer.play() }
                                Text { Layout.fillWidth: true; text: root.appBackend.sourceSelection.video ? root.appBackend.sourceSelection.video.split(/[\\/]/).pop() : "動画未選択"; color: root.textPrimary; font.pixelSize: 11; font.family: "Yu Gothic UI"; elide: Text.ElideMiddle }
                                Text {
                                    text: root.appBackend.workspace.cutTimeline.hasCuts
                                        ? ("素材 " + root.stamp(mainPlayer.position / 1000)
                                            + "  出力 " + root.stamp(Number(root.appBackend.workspace.editorPlayhead.outputPositionMs) / 1000)
                                            + " / " + root.stamp(root.appBackend.workspace.cutOutputDuration))
                                        : root.stamp(mainPlayer.position / 1000) + " / " + root.stamp(mainPlayer.duration / 1000)
                                    color: root.textMuted
                                    font.pixelSize: 10
                                    font.family: "Cascadia Mono"
                                }
                            }
                        }
                    }
                }
            }


            RowLayout {
                visible: root.appBackend.workspace.currentEditMode === "cut"
                Layout.fillWidth: true
                SmallButton {
                    objectName: "cutToolButton"
                    text: root.editTool === "cut" ? "● カット" : "カット"
                    onClicked: root.editTool = "cut"
                }
                SmallButton {
                    objectName: "sequenceToolButton"
                    text: root.editTool === "sequence" ? "● 動画結合・並べ替え" : "動画結合・並べ替え"
                    onClicked: root.editTool = "sequence"
                }
                Item { Layout.fillWidth: true }
            }
            SequenceEditorPanel {
                id: sequenceEditorPanel
                objectName: "workspaceSequenceEditor"
                visible: root.appBackend.workspace.currentWorkspace === "normal-video"
                    && root.appBackend.projectLoaded
                    && root.appBackend.workspace.currentEditMode === "cut" && root.editTool === "sequence"
                Layout.fillWidth: true
                Layout.preferredHeight: 335
                Layout.minimumHeight: 315
                backend: root.appBackend
                panelColor: root.panel
                raisedColor: root.raised
                borderColor: root.border
                textColor: root.textPrimary
                mutedColor: root.textMuted
                accentColor: root.acid
                warningColor: root.amber
                dangerColor: root.danger
            }

            Rectangle {
                id: modeEditorSlot
                objectName: "modeEditorSlot"
                visible: root.appBackend.projectLoaded && (root.appBackend.workspace.currentEditMode !== "cut" || root.editTool === "cut")
                Layout.fillWidth: true
                Layout.preferredHeight: visible
                    ? (root.appBackend.workspace.currentEditMode === "cut" ? 122 : 156)
                    : 0
                Layout.minimumHeight: visible
                    ? 92
                    : 0
                radius: 12
                color: root.panel
                border.color: root.border

                Loader {
                    id: modeEditorContentLoader
                    objectName: "modeEditorContentLoader"
                    anchors.fill: parent
                    active: root.appBackend.projectLoaded && root.modeEditorContent !== null
                    sourceComponent: root.modeEditorContent
                }

            }

            Rectangle {
                id: processingProgressOverlay
                objectName: "processingProgressOverlay"
                Layout.fillWidth: true
                Layout.rightMargin: root.codexDrawerBodyInset
                Layout.preferredHeight: visible ? progressPanel.implicitHeight : 0
                Layout.minimumHeight: visible ? progressPanel.implicitHeight : 0
                implicitHeight: progressPanel.implicitHeight
                z: 700
                color: "transparent"
                visible: root.appBackend && root.appBackend.workflow.progressVisible

                ProcessingProgressPanel {
                    id: progressPanel
                    objectName: "processingProgressPanel"
                    anchors.fill: parent
                    backend: root.appBackend
                    panelColor: root.panel
                    raisedColor: root.raised
                    borderColor: root.border
                    textColor: root.textPrimary
                    mutedColor: root.textMuted
                    accentColor: root.acid
                    warningColor: root.amber
                    errorColor: root.danger
                }
            }

            ApplicationLogPanel {
                id: applicationLogPanel
                compact: true
                objectName: "applicationLogPanel"
                Layout.fillWidth: true
                Layout.fillHeight: root.appBackend.workflow.progressVisible
                Layout.preferredHeight: implicitHeight
                // The progress panel reserves 126px in this column.  At the
                // 1220x760 minimum window, allow an expanded log to shrink
                // to its collapsed minimum so its header remains reachable.
                Layout.minimumHeight: root.appBackend.workflow.progressVisible ? 56 : implicitHeight
                backend: root.appBackend
            }
        }


        WorkspaceInspectorPanel {
            id: modeSettingsSlot
            projectLoaded: root.appBackend.projectLoaded
            selectedTab: root.inspectorTab
            settingsContent: root.modeSettingsContent
            colors: root.subtitleEditorColors
            Layout.preferredWidth: root.width >= 1400 ? 300 : 250
            Layout.minimumWidth: 200
            Layout.minimumHeight: 0
            Layout.maximumHeight: mainWorkspace.height
            Layout.fillHeight: true
            Layout.alignment: Qt.AlignTop
            onTabRequested: function(tab) { root.inspectorTab = tab }
        }

    }

  Dialog {
      id: overwriteProjectDialog
      objectName: "overwriteProjectDialog"
      anchors.centerIn: Overlay.overlay
      width: 480
      modal: true
      title: "既存プロジェクトの上書き"
      standardButtons: Dialog.Yes | Dialog.No

      contentItem: Text {
          text: "同じ動画の編集プロジェクトが既に存在します。\n既存プロジェクトを上書きして文字起こしを再実行しますか？"
          color: root.textPrimary
          font.family: "Yu Gothic UI"
          font.pixelSize: 12
          wrapMode: Text.Wrap
      }

      onAccepted: {
          var request = root.pendingWelcomeTranscriptionRequest
          root.pendingWelcomeTranscriptionRequest = null
          if (request) {
              if (root.appBackend.loadProjectWithSelectedSources(
                      request.projectPath, request.sources))
                  root.appBackend.workflow.transcribeProject(request.settings, "replace")
              return
          }
          var settings = JSON.parse(JSON.stringify(root.currentSettings()))
          if (root.appBackend.projectLoaded)
              root.appBackend.workflow.transcribeProject(settings, "replace")
          else
              root.appBackend.workflow.startTranscription(settings, true)
      }
      onRejected: root.pendingWelcomeTranscriptionRequest = null
  }

  Dialog {
      id: transcriptionMergeDialog
      objectName: "transcriptionMergeDialog"
      anchors.centerIn: Overlay.overlay
      width: 500
      modal: true
      title: "既存字幕の取り込み方法"
      standardButtons: Dialog.NoButton

      contentItem: ColumnLayout {
          spacing: 10
          Text {
              Layout.fillWidth: true
              text: "既存の字幕があります。文字起こし結果の取り込み方法を選択してください。既存字幕は確認なしに削除されません。"
              color: root.textPrimary
              font.family: "Yu Gothic UI"
              font.pixelSize: 12
              wrapMode: Text.Wrap
          }
          RowLayout {
              Layout.fillWidth: true
              Item { Layout.fillWidth: true }
              Button { objectName: "transcriptionMergeCancelButton"; text: "キャンセル"; onClicked: transcriptionMergeDialog.close() }
              Button { objectName: "transcriptionMergeAppendButton"; text: "追加・統合"; onClicked: { transcriptionMergeDialog.close(); root.appBackend.workflow.transcribeProject(root.currentSettings(), "merge") } }
              Button { objectName: "transcriptionMergeReplaceButton"; text: "置き換え"; onClicked: { transcriptionMergeDialog.close(); root.appBackend.workflow.transcribeProject(root.currentSettings(), "replace") } }
          }
      }
  }

    SourceSettingsPopup {
        id: sourcePopup
        appBackend: root.appBackend
        colors: root.subtitleEditorColors
        onSourceDropped: function(drop) { root.importSourceDrop(drop) }
        onSpeakerColorRequested: function(index, color) { root.openSpeakerColorPicker("source", index, color) }
        onSaveAsRequested: root.browseProjectSaveAs()
    }

    Rectangle {
        id: mixerPage
        objectName: "mixerPage"
        anchors.fill: parent
        anchors.rightMargin: root.codexWorkspaceRightInset
        visible: root.mixerMode
        z: 100
        color: "#0D1210"
        border.color: "#46564E"
        focus: visible
        Keys.onEscapePressed: root.closeMixerScreen()
        onVisibleChanged: if (visible) forceActiveFocus()

        Loader {
            id: mixerLoader
            anchors.fill: parent
            active: root.mixerMode
            sourceComponent: mixerContentComponent
        }

        Component {
            id: mixerContentComponent
            AudioMixerScreen {
                appBackend: root.appBackend
                colors: root.subtitleEditorColors
                formatTimestamp: root.stamp
                speakers: root.projectSpeakerCache
                timelinePixelsPerSecond: root.timelinePixelsPerSecond
                entryPosition: root.editorPositionCache
                headerRightInset: root.codexDrawerHeaderInset
                canRender: Boolean(root.workflowCapabilities.canRenderNormal || root.workflowCapabilities.normalRenderNeedsOutput)
                onPositionUpdated: function(positionMs) { root.editorPositionCache = positionMs }
                onSubtitleEditorRequested: root.openEditorScreen()
                onSaveRequested: root.saveProject()
                onRenderRequested: root.renderVideo()
                onCloseRequested: root.closeMixerScreen()
            }
        }
    }

    Rectangle {
        id: editorPage
        objectName: "editorPage"
        anchors.fill: parent
        anchors.rightMargin: root.codexWorkspaceRightInset
        visible: root.editorMode
        z: 100
        color: "#0D1210"
        border.color: "#46564E"
        focus: visible
        Keys.onEscapePressed: root.closeEditorScreen()
        onVisibleChanged: if (visible) forceActiveFocus()

        Loader {
            id: editorLoader
            anchors.fill: parent
            active: root.editorMode
            sourceComponent: editorContentComponent
        }

        Component {
            id: editorContentComponent
            SubtitleEditorScreen {
                onEditRequested: function(action, atSeconds) { root.performSubtitleEdit(action, atSeconds) }
                onSaveRequested: root.saveProject()
                appBackend: root.appBackend
                player: mainPlayer
                editorState: subtitleEditorState
                colors: root.subtitleEditorColors
                formatTimestamp: root.stamp
                projectSpeakerCache: root.projectSpeakerCache
                subtitleLayoutMetricsCache: root.subtitleLayoutMetricsCache
                selectedSubtitleFontSize: root.selectedSubtitleFontSize
                defaultSubtitleFontSize: root.defaultSubtitleFontSize
                selectedSubtitleOutlineColor: root.selectedSubtitleOutlineColor
                selectedSubtitleOutlineThickness: root.selectedSubtitleOutlineThickness
                statusText: root.userFacingStatusLabel(root.appBackend.stage, root.appBackend.status)
                active: root.editorMode
                codexDrawerHeaderInset: root.codexDrawerHeaderInset
                onPreviewAttached: function(output) { mainPlayer.videoOutput = output }
                onPreviewDetached: mainPlayer.videoOutput = mainVideo
                onPlayheadSyncRequested: function(positionMs) { root.syncEditorPlayhead(positionMs, true) }
                onSelectionSyncRequested: function(segments) { root.syncEditorSelectionFromActiveSegments(segments) }
                onSpeakerColorRequested: function(index, color) { root.openSpeakerColorPicker("project", index, color) }
                onPreviewRequested: root.buildSubtitlePreview()
                onRenderRequested: root.renderFromEditor()
                onCloseRequested: root.closeEditorScreen()
            }
        }
    }

    Rectangle {
        id: shortModePage
        objectName: "shortModePage"
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.topMargin: root.shortWorkspaceAiAccessInset
        property string workspaceKind: "short-artifact"
        anchors.rightMargin: root.codexWorkspaceRightInset
        visible: root.shortWorkspaceActive
        z: 100
        color: root.panel
        border.color: root.border
        focus: visible
        Keys.onEscapePressed: root.closeShortWorkspace()
        onVisibleChanged: if (visible) forceActiveFocus()

        Loader {
            id: shortModeLoader
            anchors.fill: parent
            active: root.shortWorkspaceActive
            source: "ShortModeScreen.qml"
            onLoaded: shortModeLoader.item.mainRoot = root
        }
    }

    Connections {
        target: root.appBackend.workspace
        function onWorkspaceChanged() {
            var nextWorkspace = String(root.appBackend.workspace.currentWorkspace || "normal-video")
            if (nextWorkspace === "normal-video") {
                var playerState = root.appBackend.workspace.workspacePlayerState
                if (playerState)
                    mainPlayer.position = Number(playerState.positionMs || 0)
            }
        }
    }

    CodexSidebarContainer {
        id: codexSidebar
        objectName: "commonCodexSidebar"
        parent: root.loginInInspector ? modeSettingsSlot : root.contentItem
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: root.loginInInspector ? 0 : 10
        anchors.topMargin: root.loginInInspector ? modeSettingsSlot.tabBarHeight : 10
        width: root.loginInInspector ? parent.width : root.codexSidebarWidth
        visible: root.codexAuthenticated
            && (root.loginInInspector ? root.inspectorTab === "codex" : root.codexDrawerOpen)
        z: 600
        backend: root.appBackend
        drawerMode: !root.loginInInspector && root.codexSidebarOverlay
        panelColor: root.panel
        raisedColor: root.raised
        borderColor: root.border
        textColor: root.textPrimary
        mutedColor: root.textMuted
        accentColor: root.acid
        onCloseRequested: root.codexDrawerOpen = false
    }

    SmallButton {
        objectName: "codexDrawerToggle"
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.margins: 12
        width: 86
        height: 34
        visible: !root.loginInInspector && root.codexAuthenticated
            && root.codexSidebarOverlay && !root.codexDrawerOpen
        z: 650
        text: root.codexDrawerOpen ? "Codexを閉じる" : "Codexを開く"
        onClicked: root.codexDrawerOpen = !root.codexDrawerOpen
    }

    ComboBox {
        id: aiProviderLoginCombo
        parent: root.loginInInspector ? modeSettingsSlot : root.contentItem
        objectName: "aiProviderLoginCombo"
        anchors.top: parent.top
        anchors.topMargin: root.loginInInspector ? modeSettingsSlot.tabBarHeight + 12 : 12
        anchors.left: parent.left
        anchors.margins: 12
        width: 108
        height: 34
        visible: !root.codexAuthenticated && root.appBackend
            && (!root.loginInInspector || root.inspectorTab === "codex")
            && root.appBackend.ai.aiChatProviders.length > 1
        model: root.appBackend ? root.appBackend.ai.aiChatProviders : []
        textRole: "label"
        valueRole: "id"
        enabled: root.appBackend && root.appBackend.ai.codexChatState !== "streaming"
            && root.appBackend.ai.codexChatState !== "sending"
            && root.appBackend.ai.codexChatState !== "stopping"
        Component.onCompleted: {
            for (var index = 0; index < count; ++index) {
                if (valueAt(index) === root.appBackend.ai.aiChatProviderId) {
                    currentIndex = index
                    break
                }
            }
        }
        onActivated: root.appBackend.ai.selectAIProvider(currentValue)
        Connections {
            target: root.appBackend.ai
            function onAiChatChanged() {
                for (var index = 0; index < aiProviderLoginCombo.count; ++index) {
                    if (aiProviderLoginCombo.valueAt(index) === root.appBackend.ai.aiChatProviderId) {
                        aiProviderLoginCombo.currentIndex = index
                        break
                    }
                }
            }
        }
    }

    Text {
        id: aiProviderAuthHint
        parent: root.loginInInspector ? modeSettingsSlot : root.contentItem
        objectName: "aiProviderAuthHint"
        anchors.top: parent.top
        anchors.topMargin: root.loginInInspector ? modeSettingsSlot.tabBarHeight + 12 : 12
        anchors.left: parent.left
        anchors.leftMargin: aiProviderLoginCombo.visible ? 132 : 12
        width: 240
        height: 34
        visible: !root.codexAuthenticated && root.appBackend
            && (!root.loginInInspector || root.inspectorTab === "codex")
            && root.appBackend.ai.aiChatAuthHint
            && !root.appBackend.ai.aiChatLoginAvailable
        z: 650
        text: root.appBackend ? root.appBackend.ai.aiChatAuthHint : ""
        textFormat: Text.PlainText
        color: root.textMuted
        font.family: "Yu Gothic UI"
        font.pixelSize: 10
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.Wrap
    }

    SmallButton {
        id: codexLoginRoute
        anchors.leftMargin: aiProviderLoginCombo.visible ? 132 : 12
        parent: root.loginInInspector ? modeSettingsSlot : root.contentItem
        objectName: "codexLoginRoute"
        anchors.top: parent.top
        anchors.topMargin: root.loginInInspector ? modeSettingsSlot.tabBarHeight + 12 : 12
        anchors.left: parent.left
        anchors.margins: 12
        width: text === "ブラウザを開く" ? 116 : 92
        height: 34
        visible: !root.codexAuthenticated
            && (!root.loginInInspector || root.inspectorTab === "codex")
            && (!root.appBackend || root.appBackend.ai.aiChatLoginAvailable)
        z: 650
        text: root.appBackend && root.appBackend.ai.aiChatAuthHint
            ? root.appBackend.ai.aiChatAuthHint
            : (root.appBackend && root.appBackend.ai.codexAuthState === "login_pending"
            ? "ブラウザを開く"
            : (root.appBackend && ["error", "disconnected"].indexOf(root.appBackend.ai.codexConnectionState) >= 0
                ? "再接続" : root.aiProviderLoginLabel))
        enabled: root.appBackend
            && root.appBackend.ai.codexConnectionState !== "connecting"
            && root.appBackend.ai.codexAuthState !== "checking"
            && root.appBackend.ai.codexAuthState !== "logging_in"
            && root.appBackend.ai.aiChatLoginAvailable
        onClicked: {
            if (root.appBackend.ai.codexAuthState === "login_pending")
                root.appBackend.ai.openAIProviderLoginPage()
            else if (["error", "disconnected"].indexOf(root.appBackend.ai.codexConnectionState) >= 0)
                root.appBackend.ai.reconnectAIChat()
            else
                root.appBackend.ai.startAIProviderLogin()
        }
    }

    Rectangle {
        id: processingProgressModeOverlay
        objectName: "processingProgressModeOverlay"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: 12
        anchors.rightMargin: root.codexInteractiveRightInset + 12
        anchors.bottomMargin: 12
        z: 700
        color: "transparent"
        visible: root.appBackend
            && root.appBackend.workflow.progressVisible
            && (root.editorMode || root.mixerMode || root.dictionaryMode || root.shortWorkspaceActive)
        height: modeProgressPanel.implicitHeight

        ProcessingProgressPanel {
            id: modeProgressPanel
            objectName: "processingProgressModePanel"
            anchors.fill: parent
            backend: root.appBackend
            panelColor: root.panel
            raisedColor: root.raised
            borderColor: root.border
            textColor: root.textPrimary
            mutedColor: root.textMuted
            accentColor: root.acid
            warningColor: root.amber
            errorColor: root.danger
        }
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: 22
        z: 999
        visible: root.acceptingSourceDrop
        radius: 16
        color: "#E6121715"
        border.color: root.acid
        border.width: 3
        Column {
            anchors.centerIn: parent
            spacing: 8
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: "素材をドロップして追加"; color: root.acid; font.family: "Yu Gothic UI"; font.pixelSize: 24; font.weight: Font.Bold }
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: "動画または話者音声"; color: root.textPrimary; font.family: "Yu Gothic UI"; font.pixelSize: 12 }
        }
    }
    DropArea {
        id: globalSourceDropArea
        objectName: "globalSourceDropArea"
        anchors.fill: parent
        z: 1000
        enabled: !root.appBackend.running
        onEntered: function(drag) {
            root.acceptingSourceDrop = drag.hasUrls
            drag.accepted = drag.hasUrls
        }
        onExited: root.acceptingSourceDrop = false
        onDropped: function(drop) { root.importSourceDrop(drop) }
    }

    Shortcut { sequences: [StandardKey.Undo]; enabled: root.editorMode; onActivated: root.performSubtitleEdit("undo") }
    Shortcut { sequences: [StandardKey.Redo]; enabled: root.editorMode; onActivated: root.performSubtitleEdit("redo") }
    Shortcut { sequences: [StandardKey.Save]; enabled: (root.editorMode || root.mixerMode) && !root.appBackend.running; onActivated: root.saveProjectFromShortcut() }
    Shortcut { sequence: "Delete"; enabled: root.editorMode && root.appBackend.subtitles.selectedSegmentIndex >= 0; onActivated: root.performSubtitleEdit("delete") }

    Connections {
        target: root.appBackend
        function onSettingsChanged() { root.syncSettings() }
    }

    Component.onCompleted: {
        root.previousCodexAuthenticated = root.codexAuthenticated
        root.syncSettings()
    }
    onClosing: function(close) {
        if (root.appBackend.running) {
            if (root.appBackend.workflow.activeJob === "update"
                    || root.appBackend.projectDirty
                    || root.dictionaryMode
                    || subtitleEditorState.hasPendingSubtitleText
                    || subtitleEditorState.hasPendingTimeEdit) {
                close.accepted = false
                return
            }
            mainPlayer.stop()
            return
        }
        if (root.appBackend.projectLoaded && !root.saveProject()) {
            close.accepted = false
            return
        }
        mainPlayer.stop()
    }
}
