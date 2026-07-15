# 利用ガイド

## Windowsかんたんセットアップ

### 必要なもの

- Windowsの「アプリ インストーラー」に含まれるwinget
- インターネット接続
- WhisperXとPyTorchを保存できる空き容量
- GPUを使う場合は対応するNVIDIAドライバー

### 初回のみ

1. GitHubからZIPをダウンロードして展開する
2. `setup.bat` をダブルクリックする
3. `Setup verification passed.` が表示されるまで待つ
4. セットアップ画面を閉じる

PowerShellから実行する場合:

~~~powershell
.\setup.bat
~~~

### 通常起動

`start.bat` をダブルクリックするか、PowerShellから実行します。

~~~powershell
.\start.bat
~~~

### セットアップで作成されるもの

| パス | 内容 | Git管理 |
|---|---|---|
| `.venv/` | WhisperX、PyTorch、PySide6などの専用Python環境 | 対象外 |
| `.local/ffmpeg_path.txt` | 検出したFFmpegの場所 | 対象外 |
| `.gui/runtime_config.json` | GPU/CPU判定を反映したGUI設定 | 対象外 |
| `assets/speaker_colors.json` | 個人用の話者色設定 | 対象外 |

`setup.bat` は不足しているPython 3.10とFFmpegをwingetで導入し、`.venv` に必要なPython依存をインストールします。CUDAを利用できない初回環境では、GUI設定を `device=cpu`、`compute_type=int8`、`video_codec=libx264` にします。

セットアップは再実行可能です。既存の動画、音声、話者色、`.gui/runtime_config.json` は上書きしません。
## 1. 手動セットアップ

自動セットアップを使わない場合は、次を手動で用意します。

- Python 3.10系
- `ffmpeg` と `ffprobe` がPATH上にあること
- 以下の手順でプロジェクト専用 `.venv` を作成できること
- GPU実行時は、PyTorchからCUDAが利用できること
- `requirements.txt` のBudouXとJanome

OpenAI APIキーは不要です。文字起こしはローカルのWhisperXを使います。Hugging Faceトークンは、`batch` / `pipeline` でWhisperXの話者分離を有効にする場合だけ `HF_TOKEN` 環境変数へ設定します。CLI引数やJSON設定へトークンを書かないでください。

現在のローカル検証環境は Python 3.10.6、WhisperX 3.8.6、BudouX 0.8.4、Janome 0.5.0です。

プロジェクト専用の `.venv` を作成して確認します:

~~~powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install whisperx
.\.venv\Scripts\python.exe --version
ffmpeg -version
ffprobe -version
.\.venv\Scripts\python.exe -m whisperx --help
.\.venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available()); print(torch.version.cuda)"
~~~

WhisperXの話者分離を使う場合は、実行するPowerShellで入力をマスクして環境変数へ設定します。Craig分離音声だけを使う通常運用では不要です。

~~~powershell
$secureToken = Read-Host "Hugging Face token" -AsSecureString
$env:HF_TOKEN = [Net.NetworkCredential]::new("", $secureToken).Password
~~~

トークンは子のWhisperXプロセスへ環境変数として継承され、dry-runやコマンド表示には出力されません。

`assets/runtime_config.json` の標準設定はCUDAです。`setup.bat` はCUDAを利用できない初回環境だけ、GUI用の `.gui/runtime_config.json` をCPU設定で作成します。手動セットアップで最後の確認が `False` なら、[設定ガイド](CONFIGURATION.md)を見てCPU設定へ変更してください。CUDA対応PyTorchの導入方法はGPU・CUDAバージョンに依存するため、既存環境に合うものを使用します。

## GUI: Subtitle Edit Bay

通常は `start.bat` でデスクトップGUIを起動します。

~~~powershell
.\start.bat
~~~

手動起動する場合:

~~~powershell
.\.venv\Scripts\python.exe -m src.gui
~~~

起動時に `ffmpeg`・`ffprobe`・現在のPython環境のWhisperXを検査します。不足中は同期解析とレンダーを開始できません。導入後に `SOURCE SETUP` の `RECHECK` を押すと、GUIを再起動せず再検査できます。

Edit Bayでは次の操作ができます。

- 起動ごとに空の入力状態から開始
- `SOURCE SETUP` から動画・複数の話者音声・出力先を個別指定
- 動画と音声を各ドロップ領域へドラッグ＆ドロップ
- 基準にする話者音声と、照合する動画音声トラックを選択
- `ANALYZE SYNC` で自動オフセット、最終オフセット、照合スコアを事前確認
- 元動画のプレビュー、シークバーによる再生位置の移動、話者別音声ファイルの確認
- 話者ごとの字幕枠色をパレットから変更
- GPU/CPU、Whisperモデル、画質、字幕タイミングを設定
- Inspectorの設定へマウスを重ねて、日本語の説明と値変更の影響を確認
- 音量正規化と会話なし区間カットを切り替え
- パイプラインの工程、経過時間、ログを確認
- 処理の停止と出力フォルダの表示

`SOURCE SETUP` は起動直後に自動で開きます。動画と音声は別フォルダにあっても構いません。話者音声は複数ファイルを一度にドロップでき、不要なファイルは一覧右端の `×` で除外できます。出力先も毎回、存在するフォルダを指定します。

同期先が不明なら動画音声トラックは `自動検出（推奨）` のままにします。固定したい場合は `0:a:0` などを選びます。`手動オフセット補正` は自動検出値に加算され、正の値で字幕を後ろ、負の値で前へ移動します。通常は `0.000` のままで構いません。

`SAVE PRESET` または `START RENDER` を押すと、GPU・字幕・画質などの処理設定だけが `.gui/runtime_config.json` に保存されます。動画・音声・出力先・同期基準のパスは保存されず、次回起動時は再指定が必要です。このファイルはGit管理されません。

## 2. 推奨: Craig分離音声

### 入力配置

`craig_pipeline` は対象名から次を自動解決します。

~~~text
video_import/
└─ <target>/
   ├─ <動画を1本>.mkv
   └─ craig-<任意の名前>/
      ├─ 1-<話者名>.flac
      ├─ 2-<話者名>.flac
      └─ ...
~~~

対応動画は `.mkv`, `.mp4`, `.mov`, `.webm`、Craig音声は `.aac`, `.flac`, `.wav`, `.m4a` です。

対象フォルダ直下の動画は原則1本、`craig-` で始まる音声フォルダも原則1個にします。複数ある場合は `--video` または `--audio-dir` で明示します。

`1-` で始まる音声が基準音声です。この音声と動画内の各音声トラックを比較して、最も近いトラックと時刻差を求めます。別ファイルを基準にする場合は `--reference-audio` を指定します。

### dry-run

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01
~~~

dry-runでは次を確認できます。

- 選ばれた基準音声
- 一致した動画音声トラック
- 推定オフセット秒とアラインメントスコア
- 話者とASSスタイルの割り当て
- transcript、ASS、最終動画の出力先

### 実行

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --run
~~~

標準では `assets/runtime_config.json` を自動で読みます。別設定を使う場合:

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --config .\assets\runtime_config.json --run
~~~

### 画質調整

標準の `h264_nvenc` は固定品質方式の `CQ 18`、High profile、空間・時間AQ有効で出力します。値を小さくすると高画質・大容量になり、大きくすると低画質・小容量になります。

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --nvenc-cq 20 --run
~~~

通常は `18` を推奨します。容量を抑えたい場合は `19`〜`21` で比較します。CPUの `libx264` を使う場合は `--x264-crf` で同様に調整できます。

### 音量調整

Craigパイプラインは標準で最終音声を `-16 LUFS` に正規化します。配信先や好みに合わせて変更できます。

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --audio-target-lufs -14 --run
~~~

無効化する場合は `--no-audio-normalize` を指定します。音声フィルタを使うため、正規化時の最終音声はAACへ再エンコードされます。

### 誰も話していない区間をカット

標準では無効です。有効にする場合:

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --cut-no-speech --run
~~~

ゲーム音を含む動画トラックではなく、分離済みの全Craig音声を合算して発話を判定します。いずれか1人が話していればその区間は残ります。標準では `1.2` 秒以上誰も話していない区間だけを対象にし、発話の前後を `0.25` 秒残します。

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --cut-no-speech --no-speech-min-seconds 2.0 --speech-padding-seconds 0.35 --run
~~~

カット有効時は、元動画に対して区間抽出・ASS描画・音量正規化を1回のFFmpeg処理で行います。字幕時刻を維持しながら、二重エンコードによる画質低下を防ぎます。

動画が複数ある場合の上書き例:

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --video ".\video_import\game_session_01\recording.mkv" --run
~~~

同期先の音声トラックを固定する例:

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --reference-track 0:a:1 --run
~~~

音声ファイルをフォルダ単位ではなく個別指定する例:

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline --video ".\video_import\game.mkv" --audio-file ".\audio\1-speaker-a.flac" --audio-file ".\audio\2-speaker-b.flac" --output-dir ".\video_export\manual" --reference-audio ".\audio\1-speaker-a.flac" --run
~~~

自動同期結果を `0.125` 秒後ろへ補正する場合は `--alignment-offset-adjustment 0.125` を追加します。

### 出力

~~~text
video_export/<target>/
├─ transcripts/
│  ├─ 1-<話者名>.json
│  └─ 1-<話者名>.whisperx.log
├─ <動画名>.craig.merged.json
├─ <動画名>.craig.filtered.json
├─ <動画名>.craig.ass
├─ <動画名>.craig.no_speech.json    # カット有効時のみ
└─ <動画名>.craig.subtitled.mp4
~~~

`merged.json` は採用字幕、`filtered.json` は除外字幕、`ass` は焼き込み前の字幕ファイルです。データベースは使用せず、文字起こしと中間結果はJSONファイルとして保存します。

## 3. 再実行

### 字幕レイアウトや色だけを変更した場合

標準では `craig_pipeline.skip_existing_transcripts=true` なので、既存の `transcripts/*.json` を再利用します。同じコマンドを実行すればWhisperXを省略し、字幕再構成と焼き込みを行います。

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --run
~~~

ログの `Cache hit` は既存文字起こしを再利用している意味です。

### 文字起こし自体をやり直す場合

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --no-skip-existing-transcripts --run
~~~

### 既存ASSだけを焼き直す場合

~~~powershell
.\.venv\Scripts\python.exe -m src.burn_subs --video ".\video_import\game_session_01\recording.mkv" --subtitle ".\video_export\game_session_01\recording.craig.ass" --output ".\video_export\game_session_01\recording.craig.subtitled.mp4" --audio-track 0:a:0 --video-codec h264_nvenc --audio-codec copy --nvenc-preset p5 --run
~~~

## 4. MKV内トラックの一括処理

`batch` は `video_import/` 直下にある動画だけを処理します。対象サブフォルダは再帰検索しません。

~~~text
video_import/
├─ input01.mkv
├─ input02.mkv
├─ op.mp4
└─ ed.mp4
~~~

最初に音声トラックを確認します。

~~~powershell
.\.venv\Scripts\python.exe -m src.transcribe probe --input ".\video_import\input01.mkv"
~~~

dry-run:

~~~powershell
.\.venv\Scripts\python.exe -m src.batch --config .\assets\runtime_config.json
~~~

実行:

~~~powershell
.\.venv\Scripts\python.exe -m src.batch --config .\assets\runtime_config.json --run
~~~

`op.mp4` と `ed.mp4` が存在しない場合は自動的に無効扱いになります。素材のFPSが異なる場合も本編のFPSへ統一し、音声のないOP/EDには無音ステレオトラックを補ってから連結します。概要欄の見どころ時刻にはOPの実時間が自動加算されます。

完成動画の本編音声は標準で `0:a:0` です。別トラックを使う場合は `--output-audio-track 0:a:1` のように指定します。

主な出力:

~~~text
video_export/<動画名>/
├─ <動画名>.<track>.wav
├─ <動画名>.<track>.json
├─ <動画名>.merged.json
├─ <動画名>.filtered_segments.json
├─ <動画名>.merged.ass
├─ <動画名>.main.subtitled.mp4
├─ <動画名>.youtube_title.txt
├─ <動画名>.youtube_description.txt
└─ <動画名>.merged.subtitled.mp4
~~~

## 5. 単発パイプライン

動画1本から指定トラックのASSまでを作る場合:

~~~powershell
.\.venv\Scripts\python.exe -m src.pipeline --input ".\video_import\input.mkv" --audio-track 0:a:1 0:a:3 --output-dir ".\out" --run
~~~

既存WhisperX JSONからASSだけを作る場合:

~~~powershell
.\.venv\Scripts\python.exe -m src.pipeline --transcript ".\out\input.json" --output ".\out\input.ass"
~~~

## 6. 無音カット

`src.silence_cut` は動画のミックス音声そのものを基準にする独立処理です。ゲーム音が常時鳴る動画では、Craig話者音声を使う `craig_pipeline --cut-no-speech` を推奨します。単独で使う場合はdry-runで検出区間を確認してから `--run` を付けます。

~~~powershell
.\.venv\Scripts\python.exe -m src.silence_cut --input ".\video_import\input.mkv" --output ".\video_export\input.silence_cut.mp4" --noise -35dB --silence-duration 0.4 --padding 0.08
~~~

~~~powershell
.\.venv\Scripts\python.exe -m src.silence_cut --input ".\video_import\input.mkv" --output ".\video_export\input.silence_cut.mp4" --run
~~~

## 7. 実行後の確認

1. `.craig.ass` をテキストで開き、字幕文とタイムコードを確認する
2. 最終動画の冒頭、会話が重なる場所、長い無音の前後を再生する
3. 文字が残る場合は終了余白、細かすぎる場合は無音ギャップ設定を確認する
4. 認識そのものが誤っている場合だけWhisperXを再実行する

設定の意味は [CONFIGURATION.md](CONFIGURATION.md)、エラー対応は [TROUBLESHOOTING.md](TROUBLESHOOTING.md) を参照してください。
