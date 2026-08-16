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
            'objectName: "transcriptionWebDictionarySelectAllButton"',
            'objectName: "transcriptionWebDictionarySelectNoneButton"',
            'objectName: "transcriptionWebDictionaryCandidateList"',
            'objectName: "transcriptionWebDictionaryCandidateItem"',
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
            '"web_dictionary_candidate_metadata"',
        ]
        self.assertIn("signal transcriptionContextEdited(var context)", self.qml)
        self.assertIn("function contextPayload()", self.qml)
        for key in expected_keys:
            with self.subTest(key=key):
                self.assertIn(key, self.qml)

    def test_panel_preserves_confirmation_warning_copy(self) -> None:
        self.assertIn("未確認の辞書候補は文字起こしへ渡されません", self.qml)
        self.assertIn("transcript cache", self.qml)


if __name__ == "__main__":
    unittest.main()
