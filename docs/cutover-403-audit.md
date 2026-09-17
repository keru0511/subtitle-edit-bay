# #403 新UI cutover 最終監査記録

## 1. 監査の目的と境界

この文書は、親Issue #403「新UIモックをproduction UIへ移行する」の完了条件を、基準main上の実装・テスト・CI証跡へ対応付ける最終監査記録である。実装の追加仕様やproduction codeの設計書ではない。

監査スナップショットは 2026-09-17 UTC とし、基準は #424 merge 後の次のmain commitに固定する。

| 項目 | 値 |
| --- | --- |
| repository | keru0511/subtitle-edit-bay |
| 基準main SHA | 80ba9a557223939e96f1e04a5ae6ff00bd09077c |
| 基準mainの意味 | PR #424（#421）のmerge commit |
| 監査対象 | #403の完了条件、#422の監査条件 |
| 監査PR | #422をClosesする本PR |
| production code変更 | なし |

監査の証跡は、同じcommitを参照するファイル・テスト・CI runを優先する。#424のPR時点のCIと、#424 merge後のmain push CI、#422監査PR自身のCIは別の検証対象として記録し、相互に代用しない。

## 2. #403完了条件と実装・テスト・CIの対応表

### 2.1 production導線と新UI構成

| #403完了条件 | 実装の正本 | テスト証跡 | CIの所有 |
| --- | --- | --- | --- |
| production entrypointが新UIのみを起動する | src/ui/Main.qml、src/ui/screens/MainWorkflowScreenWithContext.qml、src/ui/screens/MainWorkflowScreen.qml、src/gui.py | tests/test_production_entrypoint.py::ProductionEntrypointContractTests.test_main_qml_directly_constructs_the_cutover_root、test_python_production_launchers_load_the_canonical_qml_path、test_production_launch_chain_has_no_legacy_screen_selector_or_fallback | CI / portable-unit（tests/test_production_entrypoint.py） |
| 新UIモックの主要画面構成がproduction QMLで成立する | src/ui/components/WorkspaceHeader.qml、EditorModeRail.qml、SequenceEditorPanel.qml、src/ui/screens/MainWorkflowScreen.qml | tests/test_qml_static.py::test_workspace_header_is_a_backend_bound_action_boundary、test_sequence_editor_uses_backend_view_and_mutation_boundary、test_editor_workspace_has_one_overlay_and_one_shared_preview | CI / qt-gui（tests/test_qml_static.py） |
| 通常動画のmedia bin・mode rail・SequenceEditorPanelがproduction導線にある | src/ui/components/WorkspaceHeader.qml、EditorModeRail.qml、SequenceEditorPanel.qml、src/ui/screens/MainWorkflowScreen.qml | tests/test_production_integration.py::ProductionIntegrationContractTests.test_production_workflow_routes_all_cross_workspace_actions_to_backend、tests/test_qml_static.py::test_sequence_editor_uses_backend_view_and_mutation_boundary | CI / portable-unit（tests/test_production_integration.py）、qt-gui（tests/test_qml_static.py、Windows runtime testsのStart GUI on Windows） |

### 2.2 sequence、mapping、render

| #403完了条件 | 実装の正本 | テスト証跡 | CIの所有 |
| --- | --- | --- | --- |
| 通常動画で複数video clip sequenceを保存・再読込できる | src/video_sequence.py、src/gui_project_editor_controller.py、src/gui.py | tests/test_video_sequence.py::test_project_persistence_migrates_legacy_and_saves_sequence、test_round_trip_preserves_unknown_sequence_fields_and_clip_state、tests/test_production_integration.py::test_sequence_and_short_artifact_survive_autosave_reload_as_one_project_state | CI / portable-unit（tests/test_video_sequence.py、tests/test_production_integration.py） |
| clip追加・reorder・trim・transition・audio stateがdomain stateへ反映される | src/video_sequence.py、src/sequence_render.py、src/ui/components/SequenceEditorPanel.qml | tests/test_video_sequence.py::test_add_trim_reorder_and_audio_mutations_preserve_clip_ids、test_crossfade_mapping_is_deterministic_at_overlap_boundary、tests/test_video_sequence.py::VideoSequenceTests.test_controller_sequence_mutation_is_one_undoable_transaction | CI / portable-unit（tests/test_video_sequence.py）、qt-gui（tests/test_gui_editor.py） |
| source/output mappingが一箇所のdomain contractから提供される | src/video_sequence.py、src/editor_workspace.py、src/ui/screens/MainWorkflowScreen.qml | tests/test_video_sequence.py::test_crossfade_mapping_is_deterministic_at_overlap_boundary、tests/test_editor_workspace.py::test_source_and_output_positions_use_the_mapping_boundary、tests/test_qml_static.py::test_cut_mode_uses_the_shared_player_and_backend_time_mapping | CI / portable-unit（tests/test_video_sequence.py、tests/test_editor_workspace.py）、qt-gui（tests/test_qml_static.py） |
| sequenceをproduction render/exportできる | src/sequence_render.py、src/short_video.py、src/assemble_video.py | tests/test_sequence_render.py::test_project_render_routes_only_multi_clip_sequences、tests/test_sequence_render_semantic_e2e.py::test_trim_transition_duration_matches_sequence_contract、test_clip_order_trim_boundaries_and_crossfade_are_visible、test_clip_audio_settings_survive_cut_crossfade_and_negative_offset | CI / portable-unit（tests/test_sequence_render.py）、ffmpeg-runtime（tests/test_sequence_render_semantic_e2e.py）、windows-ffmpeg-runtime（selectors） |
| mixed resolution等のunsupported stateをfail-closedにする | src/sequence_render.py | tests/test_sequence_render.py::test_mixed_video_resolution_or_sar_fails_preflight、test_mixed_video_parameters_fail_before_render_execution、test_explicit_single_or_empty_sequence_fails_closed_without_legacy_render、test_legacy_singleton_audio_and_transition_edits_fail_closed | CI / portable-unit（tests/test_sequence_render.py） |

### 2.3 編集、short workspace、AI、永続化

| #403完了条件 | 実装の正本 | テスト証跡 | CIの所有 |
| --- | --- | --- | --- |
| 字幕・cut・audio editがsequence modelと矛盾なく動作する | src/gui_project_editor_controller.py、src/editor_workspace.py、src/ui/components/SequenceEditorPanel.qml、src/ui/screens/MainWorkflowScreen.qml | tests/test_gui_editor.py::test_workspace_cut_mode_adds_and_undoes_a_selected_range、test_workspace_subtitle_mode_adds_first_caption_at_shared_playhead、test_workspace_audio_channel_update_preserves_vertical_scroll、test_audio_mixer_gui_settings_are_saved_and_applied_to_rendered_output | CI / qt-gui（tests/test_gui_editor.py） |
| short workspaceが通常動画stateと独立して動作する | src/gui_workspace_controller.py、src/ui/screens/ShortModeScreen.qml、src/short_video.py、src/short_video_schema.py | tests/test_gui_workspace_controller.py::test_round_trip_restores_normal_position_and_keeps_short_position_separate、tests/test_short_video_semantic_e2e.py::test_clip_order_boundary_duration_and_unselected_media、test_source_time_basis_uses_original_source_ranges_in_final_media、tests/test_gui_editor.py::test_short_workspace_is_separate_from_normal_edit_mode_and_chat_state | CI / portable-unit（tests/test_gui_workspace_controller.py）、qt-gui（tests/test_gui_editor.py）、ffmpeg-runtime（tests/test_short_video_semantic_e2e.py）、windows-ffmpeg-runtime（selectors） |
| AI sidebar / provider stateがworkspace横断で維持される | src/gui_ai_chat_state.py、src/gui_codex_chat_state.py、src/ui/components/CodexSidebarContainer.qml、src/ui/components/CodexChatPanel.qml | tests/test_gui_ai_chat_state.py::test_codex_is_default_and_provider_state_is_scoped、test_unauthenticated_gemini_with_login_action_exposes_generic_login_route、tests/test_gui_codex_chat_state.py::test_reconnect_resumes_thread_before_starting_the_next_turn、tests/test_gui_editor.py::test_codex_sidebar_follows_authentication_and_survives_workspace_changes | CI / portable-unit（tests/test_gui_ai_chat_state.py、tests/test_gui_codex_chat_state.py）、qt-gui（tests/test_gui_editor.py） |
| undo/redo、dirty/autosave、project persistenceへ統合される | src/gui_project_editor_controller.py、src/video_sequence.py、src/project_snapshots.py | tests/test_gui_project_editor_controller.py::test_dirty_autosave_persists_snapshot_and_clears_dirty_state、test_segment_crud_move_selection_and_undo_redo、tests/test_video_sequence.py::test_controller_sequence_mutation_is_one_undoable_transaction、tests/test_gui_editor.py::test_project_save_restart_reload_e2e_preserves_edits_and_unsaved_switch | CI / portable-unit（tests/test_gui_project_editor_controller.py、tests/test_video_sequence.py）、qt-gui（tests/test_gui_editor.py） |
| 既存single-video projectを壊さない | src/video_sequence.py、src/subtitle_project.py、src/sequence_render.py | tests/test_video_sequence.py::test_legacy_single_video_maps_to_stable_sequence_identity、test_project_persistence_migrates_legacy_and_saves_sequence、tests/test_gui_editor.py::test_loading_legacy_project_resolves_duration_for_cut_editor、tests/test_sequence_render.py::test_full_length_legacy_singleton_uses_direct_render_path、test_legacy_trimmed_singleton_uses_compatibility_render_path | CI / portable-unit（tests/test_video_sequence.py、tests/test_sequence_render.py）、qt-gui（tests/test_gui_editor.py） |

### 2.4 cutover不変条件

| 不変条件 | 実装・静的証跡 | テスト証跡 | CI owner |
| --- | --- | --- | --- |
| QMLからfilesystem / FFmpeg / project JSONを直接操作しない | src/ui/Main.qml、src/ui/screens/MainWorkflowScreen.qml、src/ui/components/SequenceEditorPanel.qml、EditBayBackend facade | tests/test_production_integration.py::test_production_workflow_routes_all_cross_workspace_actions_to_backend、tests/test_qml_static.py::test_sequence_editor_uses_backend_view_and_mutation_boundary | portable-unit（tests/test_production_integration.py）、qt-gui（tests/test_qml_static.py） |
| QML内のmutable arrayをsequenceの正本にしない | src/video_sequence.py、src/gui.py、src/ui/components/SequenceEditorPanel.qml | tests/test_qml_static.py::test_sequence_editor_uses_backend_view_and_mutation_boundary、tests/test_production_integration.py::test_sequence_and_short_artifact_survive_autosave_reload_as_one_project_state | portable-unit（tests/test_production_integration.py）、qt-gui（tests/test_qml_static.py） |
| backend facadeとcontrollerの境界を維持する | src/gui.py、src/gui_project_editor_controller.py、src/gui_workspace_controller.py | tests/test_workspace_contract.py::test_main_workflow_uses_backend_workspace_as_the_only_navigation_boundary、test_workspace_cutover_keeps_ai_state_on_backend_boundary | portable-unit（tests/test_workspace_contract.py） |
| shortをcurrentEditModeへ混ぜず、AI stateをworkspaceごとに複製しない | src/ui/screens/ShortModeScreen.qml、src/gui_workspace_controller.py、src/gui_ai_chat_state.py | tests/test_workspace_contract.py::test_workspace_cutover_keeps_ai_state_on_backend_boundary、tests/test_gui_editor.py::test_short_workspace_is_separate_from_normal_edit_mode_and_chat_state | portable-unit（tests/test_workspace_contract.py）、qt-gui（tests/test_gui_editor.py） |
| legacy UI fallbackとworkspace canonical mirrorをproduction経路へ戻さない | src/ui/screens/MainWorkflowScreen.qml、src/ui/screens/MainWorkflowScreenWithContext.qml | tests/test_qml_static.py::test_editor_workspace_has_one_overlay_and_one_shared_preview、tests/test_workspace_contract.py::test_qml_has_no_local_workspace_compatibility_mirror、tests/test_production_entrypoint.py::test_production_launch_chain_has_no_legacy_screen_selector_or_fallback | qt-gui（tests/test_qml_static.py）、portable-unit（tests/test_workspace_contract.py、tests/test_production_entrypoint.py） |

## 3. CI検証の所有権と実績

正本は docs/validation-ownership.md と docs/CI_TEST_GROUPS.md である。テストモジュールの分類は tests/ci_test_groups.json、実行入口は scripts/run_ci_tests.py とする。

QML lintの具体的な証跡は tests/test_qml_static.py::test_qml_files_pass_qmllint_without_warnings であり、CIではPortable, Qt, and FFmpeg testsのqt-guiグループが所有する。

### 3.1 基準mainで確認したCI

次のrunは、#424 merge後main SHA 80ba9a557223939e96f1e04a5ae6ff00bd09077c を直接検証した実績である。

| 検証 | 実run | 確認できた状態 |
| --- | --- | --- |
| 通常CI | [CI run 35223701918](https://github.com/keru0511/subtitle-edit-bay/actions/runs/35223701918) | success。Python quality、Portable/Qt/FFmpeg、Windows runtime、Windows launcher、FFmpeg 6、Windows installer smokeを含む |
| CodeQL | [CodeQL run 35223701962](https://github.com/keru0511/subtitle-edit-bay/actions/runs/35223701962) | success |

この2つを基準mainのCI証跡として使用する。runが後から再実行された場合は、GitHub上の同じhead SHAに対する最新attemptを優先する。

### 3.2 #424 PR時点のCI（main証跡とは別）

#424のhead 941836911c513e638a50e935b279a7c24400c529 に対して確認した事前merge runは、#424の変更レビュー用であり、基準mainの証跡には数えない。

- [CI run 35217369704](https://github.com/keru0511/subtitle-edit-bay/actions/runs/35217369704): success
- [GUI performance run 35217369664](https://github.com/keru0511/subtitle-edit-bay/actions/runs/35217369664): success
- [Release readiness run 35217369952](https://github.com/keru0511/subtitle-edit-bay/actions/runs/35217369952): success
- [CodeQL run 35217369647](https://github.com/keru0511/subtitle-edit-bay/actions/runs/35217369647): success

### 3.3 #422監査PR自身のCI

#422自身のHEAD dfa771ecf04e4898432ae5a26e463605d1726dbb に対して、次の実runを確認した。

| 検証 | 実run | 状態 |
| --- | --- | --- |
| 通常CI | [CI run 35227037445](https://github.com/keru0511/subtitle-edit-bay/actions/runs/35227037445) | success |
| CodeQL | [CodeQL run 35227037504](https://github.com/keru0511/subtitle-edit-bay/actions/runs/35227037504) | success |
| Release readiness | [Release readiness run 35227038463](https://github.com/keru0511/subtitle-edit-bay/actions/runs/35227038463) | success。Windows installer install/startを含む |
| GUI performance | このHEADに紐づくrunなし | 対象workflowの実runは確認されなかった |

上記は#422自身のPRに対する証跡であり、#424 PR時点およびmain pushのrunとは混同しない。

## 4. blocker判定

### 4.0 監査対象のIssue/PR状態

blocker=0の集計対象は、#403完了条件に対する未解決の実装欠落・不具合を持つIssue/PRである。親Epicのopen状態や、監査PRがレビュー中であること自体はcutover blockerに数えない。

| 対象 | 監査時点のGitHub状態 | blocker集計上の扱い |
| --- | --- | --- |
| #403 | open（親Epic、close待ち） | blockerではない |
| #418 / #419 / #420 / #421 | closed（cutover依存slice完了） | blockerではない |
| #422 | open（この監査PR、CI実績確認済み） | blockerではない |
| 別のcutover blocker Issue/PR | 確認されなかった | 0件 |

### 4.1 open cutover blocker

監査で確認した実装・テスト・CIの対応に、#403の完了を妨げる未起票の欠落や不具合はない。

~~~text
open cutover blockers: 0
~~~

この値は、既知のcutover欠落・不具合を表すblockerの件数である。#422監査PR自身のCIがsuccessであることは、CI gateの実績であって、実装blockerを1件と数えるという意味ではない。CIが失敗した場合は、失敗原因を確認し、production機能の欠落なら#422へ取り込まず別Issueとして記録する。

### 4.2 未達時の扱い

- production codeの機能修正、visual polish、architecture refactor、新機能はこのPRへ追加しない。
- 監査で欠落・不具合・新しいcutover blockerを見つけた場合は、原因・再現条件・対象テストを別Issueへ記録し、この監査PRの範囲を広げない。
- test moduleを追加するときは tests/ci_test_groups.json へ一度だけ登録し、scripts/check_unittest_discovery.py と scripts/run_ci_tests.py --validate を通す。
- CI結果は対象commit/head SHAとrun URLで記録し、未実行・in_progressをsuccessと書き換えない。

## 5. #403をcompletedでcloseできる条件

次の全条件を同時に満たした時だけ、#403をcompletedでcloseできる。

1. この監査記録がmainへmergeされ、#422がcompletedになる。
2. open cutover blockers: 0 が維持され、未達の実装修正が#422へ混入していない。
3. #403の完了条件表の各行に、実装の正本、具体的なテスト、CI ownerが記載されている。
4. 同一の検証対象commitについて、Qt GUI regression、QML lint、Windows GUI smoke、FFmpeg semantic E2Eがsuccessである。
5. Release readinessがsuccessであり、必要なWindows installer build/install/startを含む。
6. 最新mainのCIとCodeQLがsuccessである。古いPRのgreenだけを根拠にしない。
7. #422 PRの差分がdocs/testsとtests/ci_test_groups.jsonだけで、production code変更がないことを確認する。

条件を満たさない場合は#403をcloseせず、未達のgateまたは別IssueのURLを記録する。#403のcloseは、監査記録の存在だけで自動的に行わない。