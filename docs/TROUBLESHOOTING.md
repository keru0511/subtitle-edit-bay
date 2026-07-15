# トラブルシューティング

## `No videos found in video_import`

`src.batch` は `video_import/` 直下の動画だけを検索し、サブフォルダを再帰検索しません。

- `video_import/input.mkv` を処理する: `python -m src.batch --run`
- `video_import/game_session_01/input.mkv` を処理する: `python -m src.craig_pipeline game_session_01 --run`

## `ffprobe ... returned non-zero exit status 1`

多くは入力パスまたはファイル名の不一致です。

~~~powershell
Get-ChildItem .\video_import\game_session_01
ffprobe -v error ".\video_import\game_session_01\実際のファイル名.mkv"
~~~

コマンドを複数行で入力した際、閉じ引用符の前に改行が入っていないかも確認します。対象名だけを渡す `craig_pipeline` なら複雑なパス指定を避けられます。

## `No video file found` / `Multiple video files found`

対象フォルダ直下に対応動画がない、または複数あります。複数ある場合:

~~~powershell
python -m src.craig_pipeline game_session_01 --video ".\video_import\game_session_01\対象.mkv"
~~~

## `No Craig audio directory found` / `Multiple Craig audio directories found`

`craig-` で始まり、対応音声ファイルを含むフォルダが必要です。複数ある場合は `--audio-dir` で明示します。

## `No reference audio file found`

音声フォルダに `1-` で始まるファイルを置くか、基準音声名を指定します。

~~~powershell
python -m src.craig_pipeline game_session_01 --reference-audio 1-speaker-a.flac
~~~

## オフセットや同期先トラックがおかしい

1. dry-runで `Matched video track`, `Offset seconds`, `Alignment score` を確認する
2. `1-*` 音声と同じ声が動画内の音声トラックに入っていることを確認する
3. 自動選択が誤る場合は `--reference-track 0:a:1` のように固定する
4. Craig音声と動画が別録画回でないことを確認する

## BudouX / Janomeの依存エラー

読みやすい日本語分割に両方必要です。未導入時は粗い文字数分割へフォールバックせず、明示的に停止します。

~~~powershell
python -m pip install -r requirements.txt
python -c "import budoux; import janome; print('ok')"
~~~

## WhisperXが見つからない

~~~powershell
python -m pip show whisperx
python -m pip install whisperx
python -c "import whisperx; print('ok')"
~~~

GUIを起動する `python` と同じ環境へインストールしてください。WhisperXの `Scripts` ディレクトリをPATHへ追加する必要はありません。

## GPUを使わない / GPU負荷が低い

処理工程ごとに使う資源が異なります。

- WhisperX推論: 主にGPU
- 音声同期と字幕レイアウト: 主にCPU
- 最終焼き込み: `h264_nvenc`ならGPU、`libx264`ならCPU
- Craig話者ごとのWhisperX: GPUメモリ競合を避けるため基本的に順番に実行
- CPU後処理: 次の話者のWhisperXと並行実行

~~~powershell
python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu')"
ffmpeg -hide_banner -encoders | Select-String nvenc
~~~

## CUDA out of memory

- `compute_type` が `float16` か確認する
- 他のGPU利用アプリを閉じる
- より小さいWhisperモデルを一時指定する
- GPUが使えない場合は `device=cpu`, `compute_type=int8` に切り替える

~~~powershell
python -m src.craig_pipeline game_session_01 --model medium --run
~~~

## `Cache hit` になり文字起こしが変わらない

標準動作です。既存transcript JSONを再利用しています。認識モデルやVADを変更して再文字起こしする場合:

~~~powershell
python -m src.craig_pipeline game_session_01 --no-skip-existing-transcripts --run
~~~

色、改行、字幕タイミングの後処理だけを変更した場合はキャッシュ再利用のままで構いません。

## 字幕が細かく切れすぎる

`subtitle_max_gap_seconds` を少し大きくします。

~~~powershell
python -m src.craig_pipeline game_session_01 --subtitle-max-gap-seconds 0.2 --run
~~~

現在の設定値は `0.1` です。まず `0.15`〜`0.25` 程度で比較します。

## しゃべり終わっても字幕が残る

`subtitle_end_padding_seconds` を確認します。現在値は `0.08` です。

~~~powershell
python -m src.craig_pipeline game_session_01 --subtitle-end-padding-seconds 0.04 --run
~~~

WhisperXが無音を直前の1文字へ含めた場合は後段で補正します。それでも認識セグメント自体が不正確なら、`--no-skip-existing-transcripts` を付けてVAD設定を変えた比較が必要です。

## 字幕が重なる

同時発話は最大3段の下部レイアウトへ割り当てます。2行字幕は2段分を予約します。4件以上の同時発話や極端に短い反応は、画面の可読性を優先して除外される場合があります。

## 会話なしカットが効かない

`craig_pipeline` に `--cut-no-speech` を付けたか、`runtime_config.json` の `cut_no_speech` が `true` か確認します。標準では無効です。

`*.craig.no_speech.json` の `no_speech_ranges` が空なら、Craig音声のノイズが発話扱いになっている可能性があります。閾値を少し上げます。

~~~powershell
python -m src.craig_pipeline game_session_01 --cut-no-speech --speech-threshold-db -35 --run
~~~

## 会話の頭や末尾まで切れる

前後余白を増やすか、カット対象の最短秒数を長くします。

~~~powershell
python -m src.craig_pipeline game_session_01 --cut-no-speech --speech-padding-seconds 0.4 --no-speech-min-seconds 2.0 --run
~~~

`-35dB` のように閾値を上げすぎると小さい声を無音と判定しやすくなります。まず `speech_padding_seconds` を調整し、その後に閾値を変更します。

## 音量が大きすぎる / 小さすぎる

標準は `-16 LUFS` です。より負の値にすると小さく、0に近づけると大きくなります。

~~~powershell
python -m src.craig_pipeline game_session_01 --audio-target-lufs -18 --run
~~~

正規化そのものを止める場合は `--no-audio-normalize` を使います。

## ASSは直ったが動画が変わらない

既存動画には古い字幕が焼き込まれています。ASSを変更した後は `craig_pipeline --run` または `src.burn_subs --run` で再度焼き込みます。

## 生成動画の画質が低い / ジャギーが目立つ

`ffprobe` で元動画と生成動画の解像度・fps・ビットレート・profileを比較します。標準はNVENC `CQ 18` とHigh profileです。

容量を許容してさらに画質を上げる場合:

~~~powershell
python -m src.craig_pipeline game_session_01 --nvenc-cq 16 --run
~~~

`CQ` を2下げると容量が大きく増える場合があります。まず標準の `18` で確認してください。会話なしカット時も現在は1回だけエンコードします。

## `WinError 206` ファイル名または拡張子が長すぎます

多数のカット区間をFFmpegのコマンドラインへ直接渡すと、Windowsの引数長上限を超えます。現在はフィルタグラフを一時ファイル経由で渡すため、区間数が多くてもこの制限を受けません。

現在のCraigパイプラインは元動画から区間抽出・ASS描画・音量正規化を1回で行うため、raw中間動画は新規作成しません。旧版が生成した `*.craig.subtitled.raw.mp4` は処理には使用されません。

## 生成動画はGit管理されるか

`video_import/`, `video_export/`, `out/` は `.gitignore` 対象です。元動画、生成動画、transcriptキャッシュはコミットされません。
