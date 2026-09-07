# ショート用ワークスペース

ショートは通常動画の編集モードではなく、通常動画プロジェクトを参照して作る別成果物です。通常動画の `currentEditMode` は `subtitle`、`cut`、`audio` のみに限定し、ショート画面への遷移は `currentWorkspace` で管理します。

## 状態境界

- 通常動画編集は共通プレイヤーと `editorPlayhead` を所有します。
- ショート用ワークスペースは独立した縦長プレビュー、選択クリップ、clip/range/settingsを所有します。
- ワークスペースを開く際に通常動画の再生位置を明示的に保存し、戻る際に復元します。ショートのプレビュー位置から通常動画のplayheadを更新しません。
- 字幕と見どころ候補はクリップ作成の補助情報です。文字起こし済みであることは開始条件ではなく、字幕0件でも元動画の範囲を直接追加できます。

## 時刻基準

`short_video.time_basis` の正本は `source` です。各clipの `start` / `end` は元ソース動画上の秒を表し、通常動画の非破壊カット後のoutput timelineとは混在させません。

通常動画のoutput timelineを将来ショート素材として選べるようにする場合は、#254のsource/output mappingで範囲を明示変換してから保存します。`time_basis: output` を暗黙に読み替えることはせず、現時点のloaderと書き出しpreflightは拒否します。

## 保存と書き出し

clip、transition、縦横比等のショート構成は編集プロジェクトに保存し、完成動画の出力先とは分離します。`ショート動画を書き出す` を実行した時点で出力先を検証し、通常動画と共通のFFmpeg依存確認、NVENC/CPU選択、progress/停止/失敗境界を利用します。書き出し中も `currentEditMode` やclip編集状態を別のrender modeへ変更しません。

Codexチャットの認証・thread・streaming状態は既存の単一backendを共有します。ワークスペース横断でサイドバーを常設する最終レイアウトは #249 が所有します。

