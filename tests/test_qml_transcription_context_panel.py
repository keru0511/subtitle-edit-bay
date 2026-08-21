import unittest
from pathlib import Path


class TranscriptionContextPanelQmlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.qml = Path("src/ui/components/TranscriptionContextPanel.qml").read_text(encoding="utf-8")

    def test_panel_exposes_expected_controls_for_qml_wiring(self) -> None:
        expected_object_names = [
            'objectName: "transcriptionContextPanel"',
            'objectName: "transcriptionGameTitleField"',
            'objectName: "transcriptionCreatorTermsField"',
            'objectName: "transcriptionGameNotesField"',
            'objectName: "transcriptionDictionaryPathField"',
            'objectName: "transcriptionDictionaryConfirmedSwitch"',
            'objectName: "transcriptionWebDictionarySwitch"',
            'objectName: "transcriptionWebDictionaryRefreshButton"',
            'objectName: "transcriptionWebDictionaryCandidateList"',
            'objectName: "transcriptionWebDictionaryCandidateItem"',
            'objectName: "transcriptionWebDictionaryUrlField"',
            'objectName: "transcriptionWebDictionarySnippetField"',
            'objectName: "transcriptionWebDictionaryManualTermField"',
            'objectName: "transcriptionWebDictionaryAddButton"',
            'objectName: "transcriptionWebDictionaryRemoveButton"',
            'objectName: "transcriptionWebDictionarySelectAllButton"',
            'objectName: "transcriptionWebDictionaryClearAllButton"',
        ]
        for object_name in expected_object_names:
            with self.subTest(object_name=object_name):
                self.assertIn(object_name, self.qml)

    def test_panel_emits_normalized_gui_payload_keys(self) -> None:
        expected_keys = [
            '"game_title"',
            '"game_notes"',
            '"creator_terms_text"',
            '"dictionary_path"',
            '"dictionary_confirmed"',
            '"web_dictionary_enabled"',
            '"web_dictionary_candidates"',
            '"web_dictionary_terms"',
        ]
        self.assertIn("signal transcriptionContextEdited(var context)", self.qml)
        self.assertIn("function contextPayload()", self.qml)
        for key in expected_keys:
            with self.subTest(key=key):
                self.assertIn(key, self.qml)

    def test_panel_exposes_user_facing_dictionary_guidance(self) -> None:
        self.assertIn("この辞書を文字起こしに使用", self.qml)
        self.assertIn("候補は選択したものだけ文字起こしに使用されます", self.qml)

    def test_panel_exposes_source_label_and_refresh_signal(self) -> None:
        self.assertIn("signal webDictionaryRefreshRequested(string url, string snippet)", self.qml)
        self.assertIn("panelRoot.sourceLabel(model.source)", self.qml)


if __name__ == "__main__":
    unittest.main()
