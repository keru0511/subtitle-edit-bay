from __future__ import annotations

import unittest

from src.gui import EditBayBackend
from src.gui_ai_facade import AIChatFacade
from src.gui_audio_facade import AudioFacade
from src.gui_sequence_facade import SequenceFacade
from src.gui_short_video_facade import ShortVideoFacade
from src.gui_subtitles_facade import SubtitleFacade
from src.gui_updates_facade import UpdateFacade
from src.gui_workflow_facade import WorkflowFacade
from src.gui_workspace_facade import WorkspaceFacade
from src.gui_base import EditBayBackend as LegacyEditBayBackendAlias
from src.gui_base import LegacyEditBayBackend
from tests.typed_case import TypedTestCase


class GuiBackendMetaObjectTests(TypedTestCase):
    def test_feature_facades_expose_typed_properties_slots_and_notify_signals(self) -> None:
        backend_meta = EditBayBackend.staticMetaObject
        for name, facade_type in (
            ("subtitles", SubtitleFacade),
            ("audio", AudioFacade),
            ("ai", AIChatFacade),
            ("workflow", WorkflowFacade),
            ("workspace", WorkspaceFacade),
            ("sequence", SequenceFacade),
            ("shortVideo", ShortVideoFacade),
            ("updates", UpdateFacade),
        ):
            with self.subTest(feature=name):
                index = backend_meta.indexOfProperty(name)
                self.assertGreaterEqual(index, 0)
                self.assertTrue(backend_meta.property(index).isConstant())
                meta = facade_type.staticMetaObject
                self.assertEqual(meta.className(), facade_type.__name__)
                self.assertIsNot(meta, backend_meta)
                for index in range(meta.propertyOffset(), meta.propertyCount()):
                    prop = meta.property(index)
                    old_index = backend_meta.indexOfProperty(prop.name())
                    self.assertGreaterEqual(old_index, 0, prop.name())
                    old = backend_meta.property(old_index)
                    self.assertEqual(prop.typeName(), old.typeName(), prop.name())
                    self.assertEqual(prop.isConstant(), old.isConstant(), prop.name())
                    if not prop.isConstant():
                        self.assertTrue(prop.hasNotifySignal(), prop.name())
                for index in range(meta.methodOffset(), meta.methodCount()):
                    method = meta.method(index)
                    signature = bytes(method.methodSignature()).decode()
                    self.assertGreaterEqual(backend_meta.indexOfMethod(signature), 0, signature)

    def test_legacy_backend_alias_preserves_the_previous_import_surface(self) -> None:
        self.assertIs(LegacyEditBayBackendAlias, LegacyEditBayBackend)

    def test_extended_backend_has_a_distinct_qt_metaobject(self) -> None:
        meta_object = EditBayBackend.staticMetaObject

        self.assertEqual(meta_object.className(), "EditBayBackend")
        self.assertIsNot(meta_object, LegacyEditBayBackend.staticMetaObject)

        for signature in (
            "setEditorPlayhead(int,QString)",
            "activeSubtitleSegments(double)",
            "selectEditMode(QString)",
            "startCodexLogin()",
            "browseProjectFile()",
            "createEmptyProject()",
            "transcriptionProjectExists()",
            "saveSettings(QVariantMap)",
            "setTranscriptionContext(QVariantMap)",
            "switchWorkspace(QString)",
            "setWorkspacePlayerState(QString,int,bool)",
            "addSequenceAsset(QString)",
            "addSequenceAssets(QVariantList)",
            "browseSequenceAsset()",
            "addSequenceClip(QString)",
            "moveSequenceClip(QString,int)",
            "removeSequenceClip(QString)",
            "trimSequenceClip(QString,double,double)",
            "setSequenceTransition(QString,QString,double)",
            "setSequenceClipAudio(QString,bool,double,double,bool)",
            "setSequencePlayhead(int)",
        ):
            with self.subTest(method=signature):
                self.assertGreaterEqual(meta_object.indexOfMethod(signature), 0)

        for name in (
            "cutTimeline",
            "editorModeCapabilities",
            "editorPlayhead",
            "actionCapabilities",
            "settings",
            "transcriptionContext",
            "currentWorkspace",
            "workspacePlayerState",
            "workspacePlayerStates",
            "sequenceView",
            "mediaBinAssets",
            "sequenceClips",
            "sequenceOutputDuration",
            "sequencePlayhead",
            "sequenceError",
        ):
            with self.subTest(property=name):
                self.assertGreaterEqual(meta_object.indexOfProperty(name), 0)

        for signature in (
            "editorPlayheadChanged()",
            "cutTimelineChanged()",
            "codexChatChanged()",
            "workspaceChanged()",
            "workspacePlayerStateChanged()",
            "sequenceChanged()",
        ):
            with self.subTest(signal=signature):
                self.assertGreaterEqual(meta_object.indexOfSignal(signature), 0)


if __name__ == "__main__":
    unittest.main()
