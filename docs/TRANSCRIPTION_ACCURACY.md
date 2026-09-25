# 初回文字起こしの認識設定

初回の文字起こしで誤生成を抑えるため、GUI・Craig・通常動画・バッチの共通コマンドを `src.whisperx_runner` に接続しています。認識はWhisperXのPython APIで一度実行し、その結果を同じ音声に対して語単位で時刻合わせします。字幕の事後検出・文字列削除・再認識は行いません。

## 設定と狙い

| 設定 | 変更前 | 変更後 | 狙いと注意点 |
| --- | --- | --- | --- |
| 候補探索数 `beam_size` | 5 | 10 | 初回生成で探索する候補を増やす。処理時間は増加する可能性がある。 |
| 反復ペナルティ `repetition_penalty` | 1.0 | 1.1 | 生成時に繰り返したトークンの確率を下げる。自然な繰り返しにも影響しうる。 |
| 反復禁止 `no_repeat_ngram_size` | 0 | 0 | 実際の反復発言を禁止しない。 |
| 音声のまとめ単位 `chunk_size` | 30秒 | 15秒 | 一度に認識・時刻合わせする区間を短くする。文脈の減少による誤認識も比較対象。 |
| 発話開始 `vad_onset` の既定値 | 0.35 | 0.5 | WhisperX標準値に揃え、弱い雑音を発話として採用しにくくする。小声の取りこぼしに注意。 |
| 発話終了 `vad_offset` の既定値 | 0.2 | 0.363 | WhisperX標準値に揃える。語尾の取りこぼしに注意。 |

`large-v3`、日本語指定、CPU/GPU・計算精度の選択は既存設定を引き継ぎます。VAD値を既存の設定ファイルや引数で明示している場合は、その値を尊重します。新しいVAD値を既存環境へ適用する場合は `shared.vad_onset` を `0.5`、`shared.vad_offset` を `0.363` に設定します。

生成設定の正本は `src/transcription_profile.py` です。`condition_on_previous_text=False` は従来どおり維持します。語単位の時刻合わせ自体は従来から存在しており、新規の同期補正ではありません。時刻合わせには元音声とWhisperXが返す絶対時刻をそのまま渡します。動画と別録音の全体オフセットや録音時間の伸縮は、この変更では修正しません。

## WhisperX 3.8.6での実効性

[固定バージョンのASR実装](https://github.com/m-bain/whisperX/blob/v3.8.6/whisperx/asr.py)では、`beam_size` と `repetition_penalty` は生成器に渡されます。一方、このバッチ経路は `no_speech_threshold` や `compression_ratio_threshold` による判定を実施しないため、その値を追加しただけでは誤生成対策になりません。

[固定バージョンのCLI](https://github.com/m-bain/whisperX/blob/v3.8.6/whisperx/__main__.py)は反復ペナルティを受け取れないため、公開Python APIを使う実行入口を用意しています。[時刻合わせ](https://github.com/m-bain/whisperX/blob/v3.8.6/whisperx/alignment.py)は言語に対応した既定モデルを使用します。

## キャッシュ

辞書の有無にかかわらず、認識設定・WhisperXバージョン・音声の絶対パスとサイズと更新日時・文脈フィンガープリントが一致する結果だけ再利用します。旧形式のキャッシュは次回実行時に一度作り直します。処理が失敗した場合は成功メタデータを残しません。既に編集・保存したプロジェクトの字幕は自動で再生成しません。

## 実行と検証

```sh
python -m src.transcribe run --input sample.mkv --audio-track 0:a:0 --output-dir out/accuracy --run
python -m unittest tests.test_whisperx_runner tests.test_transcription_execution
```

単体テストでは生成設定の受け渡し、1回だけの認識実行、元音声・絶対時刻での時刻合わせ、無音結果、話者分離、キャッシュの更新を検証します。モデル推論はモックです。

今回の値は実装に基づく調整候補であり、実音声から求めた最適値ではありません。問題の録音を利用できる端末で、旧設定と新設定について、誤挿入・脱落・置換・字幕開始終了のずれ・処理時間を比較してください。素材とモデルがこの作業環境にないため、精度向上や時間ずれの解消は未検証です。
