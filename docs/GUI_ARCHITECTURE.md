# GUI architecture split

Issue #2 is being handled in small compatibility-preserving steps. The first steps separate focused Python GUI helpers from the broad `gui_state_base.py` compatibility surface without changing the public imports used by the existing backend.

## Current split

- `src/gui_source_state.py`
  - source file extension constants
  - one-shot source config keys
  - `SourceSelection`
  - speaker source entry generation
- `src/gui_runtime_state.py`
  - GUI runtime config writing
  - GUI pipeline command construction
- `src/gui_state_base.py`
  - compatibility re-exports for existing callers
- `src/ui/Main.qml`
  - thin QML entrypoint
- `src/ui/screens/MainWorkflowScreen.qml`
  - current full workflow screen implementation
- `src/ui/components/PanelTitle.qml`
- `src/ui/components/SmallButton.qml`
- `src/ui/components/CompactSpinBox.qml`
- `src/ui/components/TimeField.qml`
  - standalone shared controls ready for gradual replacement inside workflow screens

`gui_state_base` still re-exports the source-selection and runtime-state symbols so existing imports from `src.gui_state` and `src.gui_base` continue to work.

`Main.qml` is now only an entrypoint. The current workflow screen remains behavior-compatible in `screens/MainWorkflowScreen.qml`, so later QML PRs can extract large pieces without also changing app bootstrapping.

The first shared controls now exist as standalone QML files and are covered by the static QML lint tests. Replacing the remaining inline component definitions in `MainWorkflowScreen.qml` should be done in a separate PR so behavior review is limited to usage replacement.

## Next QML steps

QML should be split after the Python source/runtime-state boundaries and top-level QML entrypoint are stable. Recommended order:

1. Replace common visual controls in `screens/MainWorkflowScreen.qml` with `components/*` controls.
2. Extract subtitle overlay and timeline components.
3. Extract editor and mixer screens.
4. Keep `Main.qml` focused on backend injection and entrypoint loading.

Each step should keep existing GUI behavior unchanged and rely on the Windows GUI smoke tests for regression coverage.

## 編集確定の共通境界

字幕、通常動画カット、シーケンス、音量、ショート編集は
`ProjectEditorController` の `commit_*` メソッドから確定する。
手動操作とAI提案は同じ確定処理を使う。GUIは編集候補の作成とプレビュー等の
画面固有の処理を担当し、履歴の追加・dirty更新・保存予約を個別に呼ばない。

1. 変更候補を正本と切り離し、字幕の正規化・ID重複検査・レイアウト計算、
   タイムライン／シーケンス／ショートのモデル検証を完了する。
   音量は既存の音量更新関数またはAI提案の検証を通した候補を渡す。
2. `_commit_edit` が変更の有無を判定する。変更なしなら履歴、redo、revision、通知を変えない。
3. 文書、選択、履歴、revision、dirtyを確定してから通知し、最後に自動保存を予約する。
4. Undo/Redoも同じ確定処理を通す。復元の準備に失敗した場合も文書と履歴スタックを変更しない。

字幕のレイアウトはコピー上で計算し、変化しなかった字幕辞書を再利用する。
変更履歴は対象字幕だけを保持するため、文字修正のたびに全プロジェクトを複製しない。
保存中の字幕リストに含まれる辞書を後続のレイアウト計算で変更しない。
字幕変更では専用のsegments通知を使い、入力中の編集画面を不要に再構築しない。

音量のUndo/Redoは、現在も存在するチャンネルの音量・有効状態・ミュート・ソロだけを
復元する。素材再リンク後のパスや追加／削除済みチャンネルを古い履歴で置き換えない。

ショート設定の読み取りはプロジェクトに既定値を書き込まない。編集時だけコピーを作り、
検証後に確定する。手動音量変更、音量リセット、ショート編集もUndo/Redoの対象となる。

### 今回の移行範囲と残る境界

この変更は主要な編集操作の確定経路を揃える第一段階。
素材の再リンク、出力先変更、話者色、全体字幕設定、プロジェクト読み込み・
文字起こし結果の採用／復元は既存経路を維持する。
互換用の可変辞書アクセスも残るため、プロジェクト全体の読み取り専用化は未完了。
今後これらを移す際も、読み込み・復元とユーザーのUndo可能な編集を区別し、
外部設定ファイルへの保存や素材キャッシュ更新まで含む契約を先に定義する。

確認コマンド:

```sh
python -m unittest tests.test_gui_project_editor_controller tests.test_gui_editor tests.test_gui_large_project_performance
python scripts/check_quality.py --lint-only
```
