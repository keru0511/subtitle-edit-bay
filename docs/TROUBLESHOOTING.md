# トラブルシューティング

## `update.bat` が失敗する

`update.bat` はGit clone版とZIP版を自動判定します。ZIP版ではGitHubから最新版を取得するため、インターネット接続が必要です。ダウンロードに失敗する場合は、ブラウザでリポジトリへアクセスできることを確認してください。

`Tracked files have local changes` と表示された場合は、Git clone版の追跡対象ファイルに編集があります。内容をコミットまたは退避してから再実行します。動画、生成物、`.gui/runtime_config.json` はGit管理外なので判定対象になりません。

`git pull failed` と表示された場合は、remoteと現在のブランチを確認します。

~~~powershell
git remote -v
git branch --show-current
git pull --ff-only
~~~

ZIP版は更新前のアプリファイルを `.local/update_backups/<日時>/` に保存します。更新後に問題が出た場合の確認用として利用できます。

## `setup.bat` が失敗する

最初に、エラーが出た状態でも `setup.bat` をもう一度実行します。途中まで導入済みの項目は再利用されます。

### wingetが見つからない

Microsoft Storeの「アプリ インストーラー」を更新し、新しいPowerShellで次を確認します。

~~~powershell
winget --version
~~~

### PythonまたはFFmpegを導入した直後に見つからない

セットアップ画面を閉じ、`setup.bat` を再実行します。FFmpegは検出した場所を `.local/ffmpeg_path.txt` に保存し、`start.bat` が起動時にPATHへ追加します。

CLIを同じPowerShellで実行してFFmpegが見つからない場合:

~~~powershell
$env:Path = "$(Get-Content .\.local\ffmpeg_path.txt);$env:Path"
ffmpeg -version
ffprobe -version
~~~

### WhisperXまたはPyTorchの導入で止まる

- 通信が安定した状態で再実行する
- 十分な空き容量を確保する
- 処理中にセットアップ画面を閉じない

依存環境だけを最初から作り直す場合は `.venv` を削除して再実行します。動画、音声、GUI設定、話者色設定は削除されません。

~~~powershell
Remove-Item -Recurse -Force .\.venv
.\setup.bat
~~~

## `start.bat` で「not set up yet」と表示される

`.venv/Scripts/python.exe` がありません。`setup.bat` を実行し、`Setup verification passed.` まで完了させてください。

## GUIに依存ツール不足が表示される

`素材設定` の `再確認` を押します。解消しない場合はGUIを閉じて `setup.bat` を再実行し、その後 `start.bat` から起動してください。

## GUIのボタンが無効、または押しても処理されない

右側の `次の操作` に表示される理由を確認します。

- `文字起こしを開始`: 動画、1つ以上の話者音声、出力先、実行ツールがすべて必要
- `字幕を編集する` / `字幕を焼き付ける`: 文字起こし後の編集プロジェクトが必要
- `分割`: 字幕を選択し、再生位置をその字幕の開始・終了から0.05秒以上内側へ置く
- `出力先を開く`: 出力先フォルダの指定が必要

文字起こしまたは動画出力の処理中は、入力の食い違いを防ぐためソース変更と字幕編集がロックされます。編集画面で保存やASS生成に失敗した場合は、画面上部に `CHECK` または `ERROR` と理由が表示されます。

## `No videos found in video_import`

`src.batch` は `video_import/` 直下の動画だけを検索し、サブフォルダを再帰検索しません。

- `video_import/input.mkv` を処理する: `.\.venv\Scripts\python.exe -m src.batch --run`
- `video_import/game_session_01/input.mkv` を処理する: `.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --run`

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
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --video ".\video_import\game_session_01\対象.mkv"
~~~

## `No Craig audio directory found` / `Multiple Craig audio directories found`

`craig-` で始まり、対応音声ファイルを含むフォルダが必要です。複数ある場合は `--audio-dir` で明示します。

## `No reference audio file found`

音声フォルダに `1-` で始まるファイルを置くか、基準音声名を指定します。

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --reference-audio 1-speaker-a.flac
~~~

## オフセットや同期先トラックがおかしい

1. dry-runで `Matched video track`, `Offset seconds`, `Alignment score` を確認する
2. `1-*` 音声と同じ声が動画内の音声トラックに入っていることを確認する
3. 自動選択が誤る場合は `--reference-track 0:a:1` のように固定する
4. Craig音声と動画が別録画回でないことを確認する

## BudouX / Janomeの依存エラー

読みやすい日本語分割に両方必要です。未導入時は粗い文字数分割へフォールバックせず、明示的に停止します。

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -c "import budoux; import janome; print('ok')"
~~~

## WhisperXが見つからない

~~~powershell
.\.venv\Scripts\python.exe -m pip show whisperx
.\.venv\Scripts\python.exe -m pip install whisperx
.\.venv\Scripts\python.exe -c "import whisperx; print('ok')"
~~~

GUIを起動する `python` と同じ環境へインストールしてください。WhisperXの `Scripts` ディレクトリをPATHへ追加する必要はありません。

## WhisperX実行が途中で失敗する

出力先の `*.whisperx.log` を確認します。失敗時も標準出力・標準エラーと終了コードをUTF-8で保存します。


## CUDAを選ぶと文字起こしがすぐ終了する

WhisperXログに `Torch not compiled with CUDA enabled` と出る場合、NVIDIA GPUではなく `.venv` のPyTorchがCPU版になっています。

1. GUIを閉じる
2. `setup.bat` を再実行する
3. `Setup verification passed.` と `CUDA: available` を確認する
4. `start.bat` からGUIを起動し直す

~~~powershell
.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
~~~

最後が `True` なら修復済みです。NVIDIA GPU検出時はPyTorch 2.8.0 CUDA 12.8版とWhisperX 3.8.6をセットアップが自動導入します。

GUIもCUDA設定とPyTorchの不一致を開始前に検出します。処理中のWhisperX出力は画面と `transcripts/*.whisperx.log` へ同時に記録され、同期解析の遅延通知がエラー表示を上書きすることはありません。

## GPUを使わない / GPU負荷が低い

処理工程ごとに使う資源が異なります。

- WhisperX推論: 主にGPU
- 音声同期と字幕レイアウト: 主にCPU
- 最終焼き込み: `h264_nvenc`ならGPU、`libx264`ならCPU
- Craig話者ごとのWhisperX: GPUメモリ競合を避けるため基本的に順番に実行
- CPU後処理: 次の話者のWhisperXと並行実行

~~~powershell
.\.venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu')"
ffmpeg -hide_banner -encoders | Select-String nvenc
~~~

## CUDA out of memory

- `compute_type` が `float16` か確認する
- 他のGPU利用アプリを閉じる
- より小さいWhisperモデルを一時指定する
- GPUが使えない場合は `device=cpu`, `compute_type=int8` に切り替える

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --model medium --run
~~~

## `Cache hit` になり文字起こしが変わらない

標準動作です。既存transcript JSONを再利用しています。認識モデルやVADを変更して再文字起こしする場合:

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --no-skip-existing-transcripts --run
~~~

色、改行、字幕タイミングの後処理だけを変更した場合はキャッシュ再利用のままで構いません。

## 字幕が細かく切れすぎる

`subtitle_max_gap_seconds` を少し大きくします。

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --subtitle-max-gap-seconds 0.2 --run
~~~

現在の設定値は `0.1` です。まず `0.15`〜`0.25` 程度で比較します。

## しゃべり終わっても字幕が残る

`subtitle_end_padding_seconds` を確認します。現在値は `0.08` です。

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --subtitle-end-padding-seconds 0.04 --run
~~~

WhisperXが無音を直前の1文字へ含めた場合は後段で補正します。それでも認識セグメント自体が不正確なら、`--no-skip-existing-transcripts` を付けてVAD設定を変えた比較が必要です。

## 字幕が重なる

同時発話は最大3段の下部レイアウトへ割り当てます。2行字幕は2段分を予約します。4件以上の同時発話や極端に短い反応は、画面の可読性を優先して除外される場合があります。

## 会話なしカットが効かない

`craig_pipeline` に `--cut-no-speech` を付けたか、`runtime_config.json` の `cut_no_speech` が `true` か確認します。標準では無効です。

`*.craig.no_speech.json` の `no_speech_ranges` が空なら、Craig音声のノイズが発話扱いになっている可能性があります。閾値を少し上げます。

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --cut-no-speech --speech-threshold-db -35 --run
~~~

## 会話の頭や末尾まで切れる

前後余白を増やすか、カット対象の最短秒数を長くします。

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --cut-no-speech --speech-padding-seconds 0.4 --no-speech-min-seconds 2.0 --run
~~~

`-35dB` のように閾値を上げすぎると小さい声を無音と判定しやすくなります。まず `speech_padding_seconds` を調整し、その後に閾値を変更します。

## 音量が大きすぎる / 小さすぎる

標準は `-16 LUFS` です。より負の値にすると小さく、0に近づけると大きくなります。

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --audio-target-lufs -18 --run
~~~

正規化そのものを止める場合は `--no-audio-normalize` を使います。

## ASSは直ったが動画が変わらない

既存動画には古い字幕が焼き込まれています。ASSを変更した後は `craig_pipeline --run` または `src.burn_subs --run` で再度焼き込みます。

## 生成動画の画質が低い / ジャギーが目立つ

`ffprobe` で元動画と生成動画の解像度・fps・ビットレート・profileを比較します。標準はNVENC `CQ 18` とHigh profileです。

容量を許容してさらに画質を上げる場合:

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --nvenc-cq 16 --run
~~~

`CQ` を2下げると容量が大きく増える場合があります。まず標準の `18` で確認してください。会話なしカット時も現在は1回だけエンコードします。

## `WinError 206` ファイル名または拡張子が長すぎます

多数のカット区間をFFmpegのコマンドラインへ直接渡すと、Windowsの引数長上限を超えます。現在はフィルタグラフを一時ファイル経由で渡すため、区間数が多くてもこの制限を受けません。

現在のCraigパイプラインは元動画から区間抽出・ASS描画・音量正規化を1回で行うため、raw中間動画は新規作成しません。旧版が生成した `*.craig.subtitled.raw.mp4` は処理には使用されません。

## 生成動画はGit管理されるか

`video_import/`, `video_export/`, `out/` は `.gitignore` 対象です。元動画、生成動画、transcriptキャッシュはコミットされません。

## `字幕を編集する` が押せない

編集用の `*.subtitle-project.json` がまだ開かれていません。`文字起こしを開始` が正常終了するまで待つか、ヘッダーの `プロジェクトを開く` から既存プロジェクトを選択します。rawの `transcripts/*.json` や `*.craig.merged.json` は編集プロジェクトではありません。

## 編集した字幕が動画へ反映されない

編集後はGUIの `字幕を焼き付ける`、または `src.subtitle_workflow render --project ... --run` を使用します。従来の `src.craig_pipeline --run` はraw transcriptから従来の `*.craig.ass` を再生成する互換経路で、`*.subtitle-project.json` の手動編集を読みません。

`ASSを更新` は `*.edited.ass` を更新するだけで動画は変更しません。動画まで更新する場合は、その後に `字幕を焼き付ける` を実行してください。

## プロジェクトは開くが動画・波形が表示されない

プロジェクト内の `video.path` / `audio_sources[].path` が指す素材を移動または削除していないか確認します。字幕本文と時刻は引き続き編集できますが、動画プレビューには元動画、波形の再作成と無音カットには元の話者音声が必要です。素材を元の場所へ戻すか、同じ素材で再度 `TRANSCRIBE` して新しいプロジェクトを作成します。

## ミキサーでAACが数時間と表示される / `Read error` が出る
生AACには正確な長さやシーク用インデックスがないため、FFmpegがビットレートから誤った長さを推定することがあります。現在のミキサーは初回起動時に、動画内トラックと個別音声を正しい時間情報付きのMKAへキャッシュしてから再生します。準備中は再生ボタンが無効になり、完了すると自動的に有効になります。

古いバージョンで作られたプレビューキャッシュを作り直す場合は、アプリを終了してから `%LOCALAPPDATA%\Subtitle Edit Bay\audio-preview` を削除してください。元動画、個別音声、プロジェクトは削除されません。

## ショート動画を書き出せない

次を順番に確認します。

1. `素材設定` の検査で `ffmpeg` と `ffprobe` が利用可能になっているか確認する
2. プロジェクトに動画と字幕セグメントがあり、各クリップの開始・終了が参照元セグメント内にあるか確認する
3. クロスフェード時間がクリップの長さを超えていないか確認し、必要なら `cut` または短い時間にする
4. 出力フォルダに書き込み権限と十分な空き容量があるか確認する
5. 詳細ログに表示されたFFmpegのエラーを確認する

FFmpeg 6より古い環境や配布元が異なる環境では、`xfade`、`acrossfade`、ASSフィルターの互換性が不足することがあります。FFmpegを更新してから素材設定の `再確認` を実行してください。GPUエンコードに失敗する場合は、セットアップが自動選択するCPUエンコードへ切り替わるか確認します。

## BGMが聞こえない / BGM未検出

- BGMファイルが移動・削除されていないか確認する
- `IN` がファイル長より後ろ、または `OUT` が `IN` 以下になっていないか確認する
- `START` がショート動画の合計時間以上になっていないか確認する
- `volume` が0になっていないか確認する

BGMは指定したIN/OUT区間をループし、START後から元音声とミックスします。入力ファイルのパスに日本語や空白がある場合も利用できますが、ファイル自体を別フォルダへ移動したときはプロジェクトを開き直して再設定してください。

## ショート動画の字幕がずれる / 表示されない

字幕は元動画の時刻から選択したクリップのショートタイムラインへリマップされます。次を確認してください。

- クリップの開始・終了が意図した字幕セグメントを含んでいる
- クリップの並び順を変更した後に一度保存している
- `subtitle_scale_percent` が0になっていない
- 字幕が画面下端の安全領域外へ手動配置されていない
- 出力したMP4を古いプレイヤーでなく、別のプレイヤーでも確認する

プレビューと書き出しの字幕が異なる場合は、プロジェクトを保存してからショートモードを開き直し、ASSを再生成してください。詳細ログと生成された `.short.ass` のDialogue時刻を比較すると、元時刻とショート時刻のどちらでずれているかを切り分けられます。

## fit設定の見た目が期待と違う

`cover` はクロップ、`contain` は背景色付きの余白、`blur` はぼかし背景です。クリップに個別fitが保存されている場合はグローバルfitより優先されます。グローバル設定を使いたい場合はクリップ側の個別設定を解除して保存し直してください。
