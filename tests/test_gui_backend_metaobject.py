from __future__ import annotations

import unittest

from src.gui import EditBayBackend
from src.gui_base import EditBayBackend as LegacyEditBayBackendAlias
from src.gui_base import LegacyEditBayBackend


class GuiBackendMetaObjectTests(unittest.TestCase):
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
        ):
            with self.subTest(method=signature):
                self.assertGreaterEqual(meta_object.indexOfMethod(signature), 0)

        for name in (
            "cutTimeline",
            "editorModeCapabilities",
            "editorPlayhead",
            "actionCapabilities",
        ):
            with self.subTest(property=name):
                self.assertGreaterEqual(meta_object.indexOfProperty(name), 0)

        for signature in (
            "editorPlayheadChanged()",
            "cutTimelineChanged()",
            "codexChatChanged()",
        ):
            with self.subTest(signal=signature):
                self.assertGreaterEqual(meta_object.indexOfSignal(signature), 0)


if __name__ == "__main__":
    unittest.main()
