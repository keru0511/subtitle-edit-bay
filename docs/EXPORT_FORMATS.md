# 字幕・タイムライン書き出し

編集プロジェクトの書き出しは、編集中のプロジェクトを変更せず、出力先への明示的な上書き確認を経て実行する。

## 字幕

- SRT: 時刻を `HH:MM:SS,mmm` に変換し、cueは開始時刻順に並べる
- WebVTT: `WEBVTT` ヘッダーと `HH:MM:SS.mmm` の時刻を使用する
- CSV: `id,start,end,speaker,text` の列を固定し、CSV標準の引用符・改行エスケープを使用する

改行と特殊文字は字幕テキストの意味を変えない。入力にCRLFが含まれる場合はLFへ正規化する。重複する時刻や同時発話の順序は入力順を維持し、同じ開始時刻で並びが変わらないようにする。

## タイムラインJSON

`schema_version` を持つJSONとして、source、clips、transitions、subtitles、audioと元のprojectを保存する。元のprojectを保持するため、読み戻し時に編集用データを欠落させない。新しいスキーマは旧バージョンを暗黙変換せず、ユーザーへ互換性エラーを返す。

## EDL

CMX 3600形式のnon-drop frame fixtureを出力する。source timeとtimeline timeをそれぞれ記録し、未対応のtransitionやeffectはファイル内のWARNINGと事前警告で知らせる。drop-frameを使う場合は、fpsと変換規則を追加してから対応する。

## 安全性

- 既存ファイルは `overwrite=True` の明示指定なしに変更しない
- 一時ファイルへ書き込んでから同一ディレクトリ内でatomic replaceする
- Windowsの非ASCII文字、空白、長いパスを文字列として保持する
- エクスポートは素材のコピーや移動を行わない

