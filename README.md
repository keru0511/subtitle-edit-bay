# Subtitle Edit Bay

ゲーム実況動画から、WhisperXによる文字起こし、話者別字幕、読みやすい日本語レイアウト、ASS生成、FFmpegでの字幕焼き込みまでを行うPythonパイプラインです。

現在の推奨運用は、Craigで話者ごとに分離した音声を動画へ同期する `craig_pipeline` です。

## ドキュメント

- [利用ガイド](docs/USAGE.md): セットアップ、入力配置、実行、再実行、出力確認
- [設定ガイド](docs/CONFIGURATION.md): GPU、字幕タイミング、色、話者分離、codec
- [トラブルシューティング](docs/TROUBLESHOOTING.md): よくあるエラーと切り分け

## Windowsかんたんセットアップ

1. GitHubからZIPをダウンロードして展開する
2. 初回だけ `setup.bat` をダブルクリックする
3. `Setup verification passed.` が表示されたら画面を閉じる
4. 以後は `start.bat` をダブルクリックしてGUIを起動する

PowerShellから実行する場合もコマンドは同じです。

~~~powershell
.\setup.bat
.\start.bat
~~~

`setup.bat` はwingetで不足しているPython 3.10とFFmpegを導入し、WhisperX・PyTorch・GUI依存を `.venv` へインストールします。FFmpegの場所は `.local` に記録します。インターネット接続と十分な空き容量が必要で、WhisperXとPyTorchの導入には時間がかかります。

CUDAを利用できないPCでは、初回GUI設定をCPUと `libx264` へ自動で切り替えます。セットアップは再実行可能で、動画、音声、話者色、既存GUI設定は上書きしません。

## アップデート

GitHubから `git clone` した環境では、`update.bat` をダブルクリックすると最新版を取得し、依存関係も更新します。

~~~powershell
.\update.bat
~~~

Git clone環境では、追跡対象ファイルにローカル変更がある場合は安全のため停止します。ZIP環境ではGitHubから最新の `main.zip` を取得し、更新前のアプリファイルを `.local/update_backups/` に保存してから上書きします。どちらの方式でも `video_import/`、`video_export/`、`.gui/`、`.venv/`、話者色などのローカルデータは保持されます。

## 手動セットアップ

Python 3.10とFFmpeg/ffprobeを手動で用意し、プロジェクト専用の `.venv` にWhisperXと字幕レイアウト依存を導入します。

~~~powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install whisperx
.\.venv\Scripts\python.exe --version
ffmpeg -version
ffprobe -version
.\.venv\Scripts\python.exe -m whisperx --help
~~~

GUIは通常 `start.bat` で起動します。手動で起動する場合は次を実行します。

~~~powershell
.\.venv\Scripts\python.exe -m src.gui
~~~

起動時に `ffmpeg`・`ffprobe`・WhisperXを検査します。不足している場合は `SOURCE SETUP` に導入案内が表示され、インストール後は `RECHECK` で再検査できます。

GUIは処理を `1 TRANSCRIBE`、`2 EDIT SUBTITLES`、`3 RENDER VIDEO` の3段階に分けています。動画・複数の話者音声・出力先を指定して文字起こしすると、raw transcriptとは別に `*.subtitle-project.json` が作成されます。このプロジェクトがユーザー編集の正本で、ASSと完成動画は何度でも作り直せます。

字幕エディターでは、動画を再生しながらテキスト・開始時刻・終了時刻・話者・字幕ごとのサイズ倍率を表形式で変更できます。実波形付きタイムラインでは字幕ブロックの移動と左右端のリサイズができ、ズーム、グリッド／字幕端スナップ、追加、分割、削除、Undo/Redo、700ms後の自動保存に対応します。`BUILD ASS` は動画を書き出さず、編集済みASSと画面内プレビューだけを更新します。

設定欄では全話者共通の字幕基準サイズと、発話音量に応じた拡大・縮小幅を指定できます。音量連動を `0%` にすると固定サイズになります。文字起こし後は話者ごとの自動倍率を字幕単位で手動上書きできます。

Craig用の入力は次のように配置します。

~~~text
video_import/
└─ game_session_01/
   ├─ recording.mkv
   └─ craig-xxxxxxxx.flac/
      ├─ 1-speaker-a.flac
      ├─ 2-speaker-b.flac
      ├─ 3-speaker-c.flac
      └─ 4-speaker-d.flac
~~~

最初にdry-runで動画、基準音声、同期先トラック、出力先を確認します。

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01
~~~

問題なければ実行します。

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --run
~~~

最終音声は標準で `-16 LUFS` に正規化されます。全Craig話者が話していない区間もカットする場合:

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --cut-no-speech --run
~~~

最終動画は `video_export/game_session_01/*.craig.subtitled.mp4` に出力されます。カットを有効にした場合は判定区間も `*.craig.no_speech.json` に保存されます。

## 入力方式の違い

| コマンド | 入力配置 | 用途 |
|---|---|---|
| `.\.venv\Scripts\python.exe -m src.subtitle_workflow <phase>` | 動画・話者音声または編集プロジェクト | 文字起こし／編集／動画出力を独立実行 |
| `.\.venv\Scripts\python.exe -m src.craig_pipeline <target>` | `video_import/<target>/` のサブフォルダ | Craigの話者別音声を動画へ同期 |
| `.\.venv\Scripts\python.exe -m src.batch` | `video_import/` 直下の動画 | MKV内の複数音声トラックを一括処理 |
| `.\.venv\Scripts\python.exe -m src.pipeline` | `--input` で動画を1本指定 | 単発の音声トラック処理とASS生成 |
| `.\.venv\Scripts\python.exe -m src.silence_cut` | `--input` で動画を1本指定 | 字幕とは独立した無音カット |

`batch` はサブフォルダ内を再帰検索しません。`video_import/<target>/` 形式なら `craig_pipeline` を使用してください。`batch` のOP/本編/EDは本編FPSと48kHzステレオへ統一され、音声のないOP/EDにも無音トラックを補って安全に連結されます。完成動画の本編音声は標準で `0:a:0` です。

## 主な構成

- `src/craig_pipeline.py`: Craig音声の同期、文字起こし、字幕統合、焼き込み
- `src/subtitle_workflow.py`: 文字起こし・ASS生成・動画書き出しの独立フェーズ
- `src/subtitle_project.py`: 編集プロジェクトの検証、保存、波形データ
- `src/gui.py` / `src/ui/Main.qml`: 表形式編集、動画同期タイムライン、Undo/Redo
- `src/subtitle_packer.py`: BudouXとJanomeを使ったページ分割・2行レイアウト
- `src/merge_transcripts.py`: 複数話者の時系列統合と重なり配置
- `src/render_ass.py`: ASS生成と話者色の適用
- `src/batch.py`: `video_import` 直下の動画一括処理
- `assets/runtime_config.json`: GPU、字幕、codecなどの実行設定
- `assets/speaker_colors.example.json`: ファイル名・話者名ごとの字幕枠色サンプル（個人用設定はGit管理外）

## テスト

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
~~~

`video_import/`、`video_export/`、`out/`、`assets/speaker_colors.json` はGit管理対象外です。APIキー、Hugging Faceトークン、生成動画をコミットしないでください。

## License

[MIT License](LICENSE)
