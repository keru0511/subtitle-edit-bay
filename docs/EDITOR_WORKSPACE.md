# 通常動画編集ワークスペース

通常動画編集は、中央の動画プレビューと再生位置を字幕・カット・音量の各モードで共有します。モードを切り替えても別のプレイヤーは生成せず、`currentEditMode` と `editorPlayhead` だけを更新します。

## 状態契約

`EditBayBackend` はQMLへ次の状態を公開します。

- `currentEditMode`: `subtitle`、`cut`、`audio` のいずれか
- `editorModeCapabilities`: `canPreview`、`canEditSubtitles`、`canCut`、`canMixAudio` と利用不可理由
- `editorPlayhead`: 基準時間軸と素材／出力それぞれのミリ秒位置
- `selectEditMode(mode)`: 利用可能なモードだけを選択
- `setEditorPlayhead(positionMs, basis)`: `source` または `output` の位置を更新

各 capability は独立しています。文字起こし環境が不足していても既存プロジェクトの字幕編集・カット編集・プレビューは利用でき、音声トラックがない場合は音量モードだけが無効になります。

## 後続機能の接続点

画面下部の `modeEditorSlot` はモード固有のタイムライン／一覧、右側の `modeSettingsSlot` は設定と操作の配置先です。字幕・カット・音量モードはそれぞれ字幕モデル／非破壊timeline／音声ミックス状態を利用し、`appBackend.currentEditMode` と `appBackend.editorPlayhead` を共有します。カットUIも `cutModeEditorContent` と `cutModeSettingsContent` として同じslotへ読み込みます。

字幕モードの追加・分割・選択は `editorPlayhead.sourcePositionMs` を使用します。音量モードのPCMデコーダは `AudioPreviewBridge` から共通プレイヤーへ従属し、映像用の第二プレイヤーや独自playheadを持ちません。再生・一時停止・シークは中央プレビューを操作し、音声ミキサーだけがその位置へ追従します。

カット適用前は素材時間と出力時間を同一として扱います。プロジェクトの `VideoTimeline` が `TimeMapping` を実装し、両時間軸を対応付けます。マッピングの交換時は現在の基準時間軸とその位置を維持し、もう一方の位置だけを再計算します。通常動画プロジェクトが開いていればカットモードを利用でき、文字起こしやGPUの依存状態はcapabilityへ影響しません。詳細な保存形式と境界規則は[通常動画の非破壊カット](NON_DESTRUCTIVE_CUTS.md)を参照してください。

既存の全画面字幕編集・音量調整は移行中の機能入口として残しています。これらの表示状態は単一の `activeOverlay` で排他的に管理します。
