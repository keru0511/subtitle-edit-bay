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

GUIは毎回空の入力画面から始まります。`SOURCE SETUP` へ動画・複数の話者音声・出力先をドラッグ＆ドロップし、基準音声、動画音声トラック、同期オフセットを確認・調整します。素材パスは保存されません。

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
| `.\.venv\Scripts\python.exe -m src.craig_pipeline <target>` | `video_import/<target>/` のサブフォルダ | Craigの話者別音声を動画へ同期 |
| `.\.venv\Scripts\python.exe -m src.batch` | `video_import/` 直下の動画 | MKV内の複数音声トラックを一括処理 |
| `.\.venv\Scripts\python.exe -m src.pipeline` | `--input` で動画を1本指定 | 単発の音声トラック処理とASS生成 |
| `.\.venv\Scripts\python.exe -m src.silence_cut` | `--input` で動画を1本指定 | 字幕とは独立した無音カット |

`batch` はサブフォルダ内を再帰検索しません。`video_import/<target>/` 形式なら `craig_pipeline` を使用してください。`batch` のOP/本編/EDは本編FPSと48kHzステレオへ統一され、音声のないOP/EDにも無音トラックを補って安全に連結されます。完成動画の本編音声は標準で `0:a:0` です。

## 主な構成

- `src/craig_pipeline.py`: Craig音声の同期、文字起こし、字幕統合、焼き込み
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
