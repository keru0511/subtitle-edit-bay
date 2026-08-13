# Subtitle Edit Bay

ゲーム実況動画から、WhisperXによる文字起こし、話者別字幕、読みやすい日本語レイアウト、ASS生成、FFmpegでの字幕焼き込みまでを行うPythonパイプラインです。

現在の推奨運用は、Craigで話者ごとに分離した音声を動画へ同期する `craig_pipeline` です。

## Windows版を使う（推奨）

プログラミング環境の準備は不要です。[最新リリース](https://github.com/keru0511/subtitle-edit-bay/releases/latest)を開き、Assetsから `SubtitleEditBay-Setup.exe` をダウンロードして実行してください。

[SubtitleEditBay-Setup.exeを直接ダウンロード](https://github.com/keru0511/subtitle-edit-bay/releases/latest/download/SubtitleEditBay-Setup.exe)

初版のインストーラーは、インストール中にPython、FFmpeg、WhisperXなどの実行環境をネットワーク経由でセットアップする場合があります。インターネット接続と十分な空き容量を確保し、完了するまで画面を閉じないでください。配布ページに掲載された注意事項がある場合は、そちらも確認してください。

開発者向けの公開手順は[リリースガイド](docs/RELEASING.md)にまとめています。

## ドキュメント

- [利用ガイド](docs/USAGE.md): セットアップ、入力配置、実行、再実行、出力確認
- [設定ガイド](docs/CONFIGURATION.md): GPU、字幕タイミング、色、話者分離、codec
- [トラブルシューティング](docs/TROUBLESHOOTING.md): よくあるエラーと切り分け
- [リリースガイド](docs/RELEASING.md): バージョンタグ、GitHub Releases、公開後確認

## ZIP版セットアップ（開発・代替経路）

通常の利用者は上記のWindows版インストーラーを使用してください。インストーラーを利用できない場合や、ソースコードから動作確認する場合は次の手順を使用できます。

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

NVIDIA GPUを検出した場合は、WhisperX 3.8.6と互換性のあるPyTorch 2.8.0のCUDA 12.8版を自動導入します。CPU版PyTorchへ置き換わった環境も、`setup.bat` の再実行で修復できます。

CUDAを利用できないPCでは、文字起こしをCPUへ自動で切り替えます。動画書き出しは毎回NVENCで1フレームの試験エンコードを行い、利用できればGPU、利用できなければCPUの `libx264` を自動選択します。GUIで動画codecを選ぶ必要はありません。セットアップは再実行可能で、動画、音声、話者色、既存GUI設定は上書きしません。

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
.\.venv\Scripts\python.exe -m pip install torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python.exe -m pip install whisperx==3.8.6
.\.venv\Scripts\python.exe --version
ffmpeg -version
ffprobe -version
.\.venv\Scripts\python.exe -m whisperx --help
~~~

GUIは通常 `start.bat` で起動します。手動で起動する場合は次を実行します。

~~~powershell
.\.venv\Scripts\python.exe -m src.gui
~~~

起動時に `ffmpeg`・`ffprobe`・WhisperXを検査します。不足している場合は右上の `素材設定` に導入案内が表示され、インストール後は `再確認` で再検査できます。動画と話者音声は、素材設定内またはアプリ画面へのドラッグ＆ドロップでも追加できます。

メイン画面は `素材`、`文字起こし`、`字幕・音量編集`、`書き出し` の4工程を表示し、その時点で必要な操作だけを右側の `次の操作` に出します。処理デバイスや字幕サイズなどは `詳細設定` を開いた時だけ表示されます。ゲーム固有名詞などは `文字起こし辞書を設定` から独立した辞書画面で編集するため、詳細設定や動画プレビューに重なりません。動画・複数の話者音声・出力先を指定して文字起こしすると、raw transcriptとは別に `*.subtitle-project.json` が作成されます。このプロジェクトがユーザー編集の正本で、ASSと完成動画は何度でも作り直せます。

`字幕を編集する` で専用の字幕編集画面へ切り替わり、動画を再生しながらテキスト・開始時刻・終了時刻・話者・字幕ごとのサイズ倍率を表形式で変更できます。字幕本文は複数行入力に対応し、自動整形で決まった改行位置も編集欄へ表示します。`Enter` で入れ直した手動改行は自動整形より優先され、入力中から画面内プレビューへ反映されます。字幕ごとの行数を選ぶ操作はありません。実波形付きタイムラインでは字幕ブロックの移動と左右端のリサイズができ、ズーム、グリッド／字幕端スナップ、追加、分割、削除、元に戻す／やり直す、700ms後のバックグラウンド自動保存に対応します。保存中に続けて編集しても最新版を追って保存し、`Ctrl+S`、`ASSを更新`、焼き付け、編集画面を閉じる操作、アプリ終了では入力中の本文と改行を先に確定します。`ASSを更新` は動画を書き出さず、編集済みASSと画面内プレビューだけを更新します。確認後は編集画面の `字幕を焼き付ける` から、そのまま完成動画の書き出しへ進めます。

設定欄では全話者共通の字幕基準サイズ、縁取りの色・太さ、発話音量に応じた拡大・縮小幅を指定できます。基準サイズは既定の50pxを100%として10〜900%で指定でき、音量連動を `0%` にすると固定サイズになります。縁取りは全字幕へ一括適用され、編集プレビューと完成動画の両方へ反映されます。文字起こし後は話者ごとの自動倍率を字幕単位で手動上書きできます。

メイン画面の `次の操作` には `字幕を編集する` と `音量を調整する` が同じ階層で表示されます。`音量を調整する` で独立したミキサー画面へ切り替わり、動画内の各音声トラックと話者ごとの個別音声をチャンネルストリップとして横並びに表示します。再生ボタン、再生位置へ追従するシークバー、INPUT ONのチャンネルだけを並べる音量バー／波形付きシーケンスで出力音を確認でき、再生に追従するレベルメーターを見ながら、縦型dBフェーダー、有効／無効、ミュート、ソロを再生音へ即時反映できます。個別音声には文字起こし時の同期オフセットを適用して完成動画へAACで合成します。既定状態は従来どおり動画の先頭音声だけが有効です。

ミキサーを初めて開くと、動画内トラックと個別音声をプレビュー専用のMKAへバックグラウンドで準備します。キャッシュは `%LOCALAPPDATA%\Subtitle Edit Bay\audio-preview` に保存され、元ファイルのサイズまたは更新日時が変わると自動更新されます。動画トラックはまとめて1回だけ読み込み、生AACも正しい時間情報付きで再生するため、元動画をチャンネル数分開くことはありません。

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

Qt GUIテストはオフスクリーンで通常のテストに含まれます。字幕編集、工程別ボタン状態、最小画面での操作、QML lintまで上の1コマンドで確認でき、追加の環境変数は不要です。

`video_import/`、`video_export/`、`out/`、`assets/speaker_colors.json` はGit管理対象外です。APIキー、Hugging Faceトークン、生成動画をコミットしないでください。

## License

[MIT License](LICENSE)
