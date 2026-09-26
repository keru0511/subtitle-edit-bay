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

## 字幕編集画面の境界

`MainWorkflowScreen.qml`は画面の切り替え、共有プレイヤーの所有、
保存・書き出し・色選択ダイアログとの接続を担当する。
字幕編集の実装は次のQMLへ分離する。

| ファイル | 担当 |
| --- | --- |
| `components/SubtitleEditorScreen.qml` | 専用字幕編集画面、プレビュー、字幕一覧、編集操作 |
| `components/SubtitleWorkspaceEditor.qml` | 通常ワークスペースの字幕操作とタイムライン |
| `components/SubtitleTimeline.qml` | 可視区間の字幕・波形描画、移動・リサイズ、再生追従。音量画面でも共有 |
| `components/SubtitleEditorState.qml` | 再生位置、表示倍率、スナップ、スクロール位置、編集中の字幕とプレビュー判定 |
| `components/SubtitleEditorButton.qml` | 既存の色・寸法を保つ字幕編集用ボタン |

各画面はバックエンド、プレイヤー、編集状態、配色などをプロパティで受け取る。
親画面のIDや暗黙のコンテキスト変数を参照しない。
画面をまたぐシーク、色選択、プレビュー更新、書き出し、閉じる操作はシグナルで親へ伝える。
`SubtitleEditorState`は親画面に1個だけ置き、Loaderで字幕画面を破棄・再生成しても
両画面の表示倍率・スナップ・スクロール位置を維持する。
既存の親画面の状態プロパティは同じ状態へのaliasとして残す。

専用字幕編集画面は新たなMediaPlayerを作らない。生成時にVideoOutputを通知し、
親画面が共有プレイヤーの出力先を切り替える。破棄時は親のプレビューへ戻す。
字幕の保存データとUndo/Redoは引き続き`backend.subtitles`を通じて操作する。

確認は既存の編集・画面切替・仮想化テストに加え、親画面のないWindowへ
字幕画面を読み込む回帰テストで行う。独立した状態での編集・Undo、編集中プレビュー、
画面再生成後の設定保持、プレイヤー表示先の通知を確認する。
`test_qml_static.py`のqmllint対象にもすべての分離先を含める。

## 音量ミキサー画面の境界

`components/AudioMixerScreen.qml`は専用ミキサーの表示、音量・ミュート・ソロ操作、
プレビュー用MediaPlayerと音声トラックの再生同期、横スクロールの復元を担当する。
バックエンド、配色、時刻表示関数、話者、表示倍率、開始位置、書き出し可否を
プロパティで受け取り、親画面のIDや暗黙のコンテキスト変数を参照しない。
再生位置の更新、字幕編集への移動、保存、書き出し、閉じる操作はシグナルで親へ通知する。

`MainWorkflowScreen.qml`はLoaderの有効化、共有プレイヤーからの位置受け渡し、
ミキサー終了時の音声停止、書き出し設定との接続を維持する。
通常ワークスペースの音量設定は`AudioModeSettings.qml`、音声タイムラインは
`AudioWorkspaceEditor.qml`が担当する。後者は共有プレイヤー、音声プレビューの状態、
配色、開始時の横スクロール位置を明示的に受け取る。シークとスクロール位置の変更は
シグナルで親画面へ通知し、画面を切り替えて再生成しても位置を復元する。
親画面のないWindowでも音声タイムラインを生成し、表示状態、シークと横スクロール復元を確認する。
ミキサーは親画面のないWindowで生成・破棄するテストに加え、既存の音量変更、
横スクロール保持、画面終了、音声付き動画への書き出しテストで境界を確認する。

`components/AudioMixerChannelStrip.qml`はチャンネル1本分の音量フェーダー、
レベル表示、ミュート・ソロ・使用状態の表示と操作を担当する。
チャンネルデータ、プレビュー音量、処理中状態、配色を明示的に受け取り、
変更要求だけを親画面へ通知する。音量のdB変換は表示部品内で管理する。
`AudioMixerScreen.qml`は横スクロール位置の復元、バックエンドへの編集確定、
再生同期を担当する。単独Windowでの操作通知と処理中の操作制限、
通常画面での編集・再生成・横スクロール保持を確認する。

共通コントロールの置き換えは別PRとして扱う。

## 素材設定ポップアップの境界

`components/SourceSettingsPopup.qml`は動画・話者音声の選択、ドロップ領域、
文字起こし対象トラック、音声同期、保存先の表示と操作を担当する。
開いたときの`beginSourceRelink`と閉じたときの`finishSourceRelink`を同じポップアップで管理する。
設定保存に必要な基準音声・動画音声トラック・手動補正値は公開プロパティから親画面へ渡す。
ドロップ、話者色選択、別名保存はシグナルで親画面へ伝え、共通の素材取り込み処理や
入力確定を伴う別名保存処理へ接続する。

親画面なしでの開閉・再指定状態・設定値・操作通知に加え、通常画面での手動補正値の保存と
ポップアップ内のスクロール・警告・ボタン配置を回帰テストで確認する。

## 処理設定ポップアップの境界

`components/AdvancedSettingsPopup.qml`は文字起こし、字幕、動画・音声の設定欄を所有し、
保存値の適用と現在の入力値の収集を担当する。バックエンド、配色、字幕の基準文字サイズを
明示的に受け取り、親画面のIDや暗黙のコンテキストには依存しない。
処理デバイスの未保存の選択、文字サイズ・縁取り色・太さは公開プロパティとして親画面へ渡し、
実行可否の判定と字幕プレビューへ即時反映する。保存操作と縁取り色ダイアログの要求は
シグナルで親へ通知する。

`MainWorkflowScreen.qml`はポップアップの配置・開閉、素材設定の値との合成、
バックエンドへの保存と色選択ダイアログを担当する。
単独Windowでの設定値の往復・操作通知に加え、通常画面の保存、未保存デバイスでの
文字起こし判定、字幕プレビュー、最小画面幅でのスクロールと開閉を回帰テストで確認する。

## ワークスペース右側インスペクターの境界

`components/WorkspaceInspectorPanel.qml`は編集プロパティとAIのタブ表示、
編集モードごとの設定コンポーネントを読み込むLoaderを担当する。
選択中のタブ、プロジェクトの有無、設定コンポーネント、配色を明示的に受け取り、
タブ操作をシグナルで親画面へ伝える。親画面のIDや暗黙のコンテキストには依存しない。

`MainWorkflowScreen.qml`はタブ選択状態を保持し、ヘッダー操作とAI認証状態に応じて
切り替える。AIサイドバーとログイン操作は画面外でも使うため親画面に残し、
インスペクターが公開するタブの高さに合わせて配置する。
単独Windowでのタブ切替・Loaderの寿命と、通常画面での認証・画面遷移・
最小幅と標準幅の配置を回帰テストで確認する。

## 共有動画プレビューの境界

`components/WorkspacePreviewPanel.qml`は通常ワークスペースの映像、字幕オーバーレイ、
再生・シーク操作と時刻表示を担当する。バックエンド、共有プレイヤー、字幕設定、配色を
明示的に受け取り、親画面のIDや暗黙のコンテキストには依存しない。
シークと字幕選択の要求はシグナルで親画面へ伝え、プレイヤーの映像出力先と
シークバーの再生位置・長さを公開する。

`MainWorkflowScreen.qml`は共有MediaPlayerとAudioOutputを1組だけ所有し、
カット区間のスキップ、素材時刻と出力時刻の変換、字幕編集画面との映像出力先の切替を担う。
専用字幕編集画面を閉じると、共有プレイヤーの出力先を通常プレビューへ戻す。
単独Windowでのコンポーネント生成・シーク通知と、通常画面での編集画面切替、
カット表示・字幕表示・最小幅の配置を回帰テストで確認する。

## 開始画面の境界

`components/ProjectStartScreen.qml`は、プロジェクト未読み込み時の案内、
状態表示、選択中の動画、開始操作を表示する。バックエンド、配色、文字起こしの
制限理由をプロパティで受け取り、親画面のIDや暗黙のコンテキストに依存しない。
新規編集、既存プロジェクトを開く、文字起こし、素材設定、辞書、処理設定の操作は
シグナルで通知する。

`components/ProjectStartFlow.qml`は新規編集と文字起こし開始の条件分岐、
音声素材の有無、開始画面の文字起こし制限理由を担当する。
親画面は現在の設定値を渡し、素材設定と上書き確認ダイアログを開く。
上書き確認の保留状態は、他の文字起こし経路とも共用する親画面に残す。

単独Windowでの表示と各操作通知・開始判断に加え、通常画面での新規作成、
既存プロジェクトを開く、素材設定、文字起こし開始、警告表示をGUI回帰テストで確認する。

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
