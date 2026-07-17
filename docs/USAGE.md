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

### アップデート

`git clone` した環境では `update.bat` を実行します。

~~~powershell
.\update.bat
~~~

Git clone環境では、追跡対象ファイルにローカル変更がないことを確認し、`git pull --ff-only` で更新します。ZIP環境ではGitHubから最新の `main.zip` をダウンロードしてアプリファイルを更新します。ZIP更新前のファイルは `.local/update_backups/` に保存されます。どちらも動画、生成物、GUI設定、仮想環境、話者色を変更せず、最後に依存関係を再確認します。

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

通常は `start.bat` で起動します。

~~~powershell
.\start.bat
~~~

メイン画面上部には次の4工程が表示されます。右側の `次の操作` には、現在実行できる主要操作だけが表示されます。

1. `素材`: 動画、話者音声、出力先を指定
2. `文字起こし`: 話者音声の同期、WhisperX文字起こし、実波形生成、編集プロジェクト作成までを実行
3. `字幕編集`: 動画と同期した表・タイムラインで字幕を編集
4. `書き出し`: 保存済みプロジェクトからASSを再生成し、動画へ焼き付け

`詳細設定` を押すと、処理デバイス、Whisperモデル、字幕サイズ、音量連動、動画・音声設定を開閉できます。通常は閉じたまま、工程とプレビューを確認できます。

文字起こし完了時に `<動画名>.subtitle-project.json` が作成されます。`字幕を編集する` を押すと専用の編集画面へ切り替わります。raw WhisperX JSONと自動生成した `craig.merged.json` は入力記録として残り、ユーザー編集はプロジェクトJSONだけへ保存されます。このため、編集後に `動画を書き出す` を何度実行してもWhisperXは起動しません。

### 字幕エディター

表では字幕ごとに次を変更できます。

- 字幕本文
- 開始時刻と終了時刻（秒、小数第3位まで）
- 話者
- 自動算出された文字サイズ倍率（50〜200%）

タイムラインは話者ごとの実音声波形と字幕ブロックを表示します。ブロック本体をドラッグすると区間全体を移動でき、選択中ブロックの白い左右端をドラッグすると開始・終了時刻を変更できます。`表示倍率` で時間軸を拡大縮小し、`スナップ` で10ms〜1000msのグリッドを指定します。移動先が別字幕の開始・終了端に近い場合は、その端へもスナップします。`スナップ=0` はスナップ無効です。

`+ 字幕追加` は現在の再生位置へ字幕を追加し、`分割` は選択字幕を再生位置で分割、`削除` は削除します。`元に戻す` / `やり直す` は最大100操作を保持します。キーボードでは `Ctrl+Z`、`Ctrl+Y`、`Ctrl+S`、`Delete` を使用できます。

編集は変更後700msでプロジェクトへ自動保存されます。ヘッダーの `● 編集あり` は保存待ち、`✓ 保存済み` は保存済みです。`ASSを更新` は動画を書き出さず `<動画名>.edited.ass` を作り、画面内の字幕プレビューにも本文・話者色・字幕単位のサイズ倍率を反映します。

### プロジェクトを開き直す

`プロジェクトを開く` から `*.subtitle-project.json` を選択すると、保存済みの動画、存在する話者音声、出力先、字幕、波形を復元します。素材を移動・削除した場合も字幕本文は開けますが、動画プレビューや再レンダーには元動画が必要です。

プロジェクトの主要項目:

~~~text
schema_version / project_type
video / audio_sources / speakers
transcription        # raw transcript、同期オフセット、モデル情報
subtitle_settings    # 基準サイズ、音量連動幅、字幕タイミング
segments             # 編集の正本。ID、本文、時刻、話者、手動上書き状態
waveforms            # GUI表示用に縮約した実音声ピーク
render_settings      # 最後に使った書き出し設定
~~~

プロジェクト内の時刻は常に元動画基準です。`無音部分をカット` を有効にした場合だけ、レンダー時に字幕と動画を同じkeep rangeで再配置します。これにより、カット設定を変えても編集済みの元タイムラインは失われません。

### CLIで段階実行

GUIと同じ処理は `src.subtitle_workflow` から独立実行できます。`--audio-file` は話者数だけ繰り返します。

~~~powershell
.\.venv\Scripts\python.exe -m src.subtitle_workflow transcribe `
  --video ".\video_import\game.mkv" `
  --audio-file ".\audio\1-alice.flac" `
  --audio-file ".\audio\2-bob.flac" `
  --reference-audio ".\audio\1-alice.flac" `
  --output-dir ".\video_export\game" `
  --config ".\assets\runtime_config.json" `
  --run
~~~

既存の `*.subtitle-project.json` がある場合、文字起こしフェーズは手動編集を守るため上書きを拒否します。意図的に作り直す場合だけCLIへ `--overwrite-project` を追加してください。GUIでは開いているプロジェクトがある間 `TRANSCRIBE` は無効になり、別の動画・出力先を選ぶと新しい処理へ切り替わります。

編集済みプロジェクトからASSだけを再生成:

~~~powershell
.\.venv\Scripts\python.exe -m src.subtitle_workflow ass `
  --project ".\video_export\game\game.subtitle-project.json"
~~~

編集済みプロジェクトから動画を書き出し:

~~~powershell
.\.venv\Scripts\python.exe -m src.subtitle_workflow render `
  --project ".\video_export\game\game.subtitle-project.json" `
  --config ".\assets\runtime_config.json" `
  --run
~~~

主な出力:

~~~text
video_export/game/
├─ transcripts/
│  ├─ 1-alice.json
│  └─ 1-alice.whisperx.log
├─ game.craig.merged.json
├─ game.craig.filtered.json
├─ game.subtitle-project.json       # ユーザー編集の正本
├─ game.edited.ass                  # ASSを更新 / renderで再生成
└─ game.edited.subtitled.mp4        # 動画を書き出す / renderの出力
~~~

GUIの処理設定だけは `.gui/runtime_config.json` に保存されます。素材と字幕の復元にはGUI設定ではなくプロジェクトJSONを使用します。

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
