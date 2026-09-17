from __future__ import annotations

import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
AUDIT_DOCUMENT = REPOSITORY_ROOT / "docs" / "cutover-403-audit.md"


class CutoverAuditContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = AUDIT_DOCUMENT.read_text(encoding="utf-8")

    def test_audit_is_pinned_to_main_and_has_explicit_blocker_state(self) -> None:
        for marker in (
            "基準main SHA",
            "80ba9a557223939e96f1e04a5ae6ff00bd09077c",
            "open cutover blockers: 0",
            "#403をcompletedでcloseできる条件",
            "production code変更 | なし",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.document)

    def test_audit_separates_historical_and_current_ci_evidence(self) -> None:
        for marker in (
            "#424 PR時点のCI",
            "35217369704",
            "35217369664",
            "35217369952",
            "35217369647",
            "基準mainで確認したCI",
            "35223701918",
            "35223701962",
            "#422監査PR自身のCI",
            "未実行",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.document)

    def test_audit_maps_each_cutover_contract_to_test_evidence(self) -> None:
        for marker in (
            "test_main_qml_directly_constructs_the_cutover_root",
            "test_sequence_and_short_artifact_survive_autosave_reload_as_one_project_state",
            "test_add_trim_reorder_and_audio_mutations_preserve_clip_ids",
            "test_crossfade_mapping_is_deterministic_at_overlap_boundary",
            "test_project_render_routes_only_multi_clip_sequences",
            "test_mixed_video_resolution_or_sar_fails_preflight",
            "test_workspace_cut_mode_adds_and_undoes_a_selected_range",
            "test_round_trip_restores_normal_position_and_keeps_short_position_separate",
            "test_unauthenticated_gemini_with_login_action_exposes_generic_login_route",
            "test_dirty_autosave_persists_snapshot_and_clears_dirty_state",
            "test_legacy_single_video_maps_to_stable_sequence_identity",
            "test_qml_has_no_local_workspace_compatibility_mirror",
            "test_editor_workspace_has_one_overlay_and_one_shared_preview",
            "test_qml_files_pass_qmllint_without_warnings",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.document)

    def test_audit_preserves_non_scope_and_new_issue_boundary(self) -> None:
        for marker in (
            "production codeの機能修正、visual polish、architecture refactor、新機能はこのPRへ追加しない。",
            "別Issueとして記録する",
            "tests/ci_test_groups.json",
            "docs/validation-ownership.md",
            "docs/CI_TEST_GROUPS.md",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.document)


if __name__ == "__main__":
    unittest.main()
