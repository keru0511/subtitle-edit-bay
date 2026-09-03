# 通常動画編集ワークスペース

通常動画編集は、中央の動画プレビューと再生位置を字幕・カット・音量の各モードで共有します。モードを切り替えても別のプレイヤーは生成せず、`currentEditMode` と `editorPlayhead` だけを更新します。

## 状態契約

`EditBayBackend` はQMLへ次の状態を公開します。

- `currentEditMode`: `subtitle`、`cut`、`audio` のいずれか
- `editorModeCapabilities`: `canPreview`、`canEditSubtitles`、`canCut`、`canMixAudio` と利用不可理由
- `editorPlayhead`: 基準時間軸と素材／出力それぞれのミリ秒位置
- `selectEditMode(mode)`: 利用可能なモードだけを選択
- `setEditorPlayhead(positionMs, basis)`: `source` または `output` の位置を更新

各 capability は独立しています。文字起こし環境が不足していても既存プロジェクトの字幕編集・プレビューは利用でき、音声トラックがない場合は音量モードだけが無効になります。

## 後続機能の接続点

画面下部の `modeEditorSlot` はモード固有のタイムライン／一覧、右側の `modeSettingsSlot` は設定と操作の配置先です。具体的な字幕・音量UIの統合は Issue #274 が担当します。

カット適用前は素材時間と出力時間を同一として扱います。Issue #254 は `TimeMapping` を実装し、`set_editor_time_mapping()` へ渡すことで両時間軸を対応付けます。カット機能が接続されるまでは `canCut` を `false` とし、空のカット編集UIを有効化しません。

既存の全画面字幕編集・音量調整は移行中の機能入口として残しています。これらの表示状態は単一の `activeOverlay` で排他的に管理します。
