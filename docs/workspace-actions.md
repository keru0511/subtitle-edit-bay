# 文字起こしツールと動画出力

## プロジェクト開始画面

プロジェクト未読込時は工程ステッパーや空の編集領域を表示せず、次の2操作を主要導線として表示します。

- **新しい動画を編集**: 動画を選択し、文字起こしを待たずに空プロジェクトを作成して通常編集ワークスペースへ入ります。素材設定やドロップで動画を選択済みの場合は、その動画をそのまま使います。同名の既存プロジェクトがあれば新規作成で上書きせず、既存プロジェクトを読み込みます。
- **プロジェクトを開く**: 既存の `.subtitle-project.json` を読み込み、文字起こしの実施有無に関係なく同じ通常編集ワークスペースへ入ります。

補助操作として「文字起こしから始める」「素材設定」「文字起こし辞書」を配置します。「文字起こしから始める」は空プロジェクトを作成してから本ページ後半に記載した共通文字起こしActionを呼び出します。専用の処理状態は持たず、完了・失敗・停止も共通経路で扱います。開始後に文字起こしを停止しても、作成済みの空プロジェクトは残ります。

通常動画の編集モードは `subtitle / cut / audio` の3種類です。文字起こしと書き出しはモードを切り替えず、既存の処理状態・進捗・停止・エラー表示を使います。

ワークスペース上部の「ツール」から文字起こしの追加・更新と辞書設定、「出力」から通常動画の書き出しを実行します。「文字起こし・出力設定」からモデルと処理デバイスを変更できます。

## 開始画面・ショート画面からの再利用

- 開始画面（#277）は、素材選択後に `MainWorkflowScreen.requestTranscription()` を呼び出します。既存プロジェクトの追加・置換確認、同名プロジェクトの上書き確認、設定と進捗を共有します。
- ショート画面（#275）は `actionCapabilities.canRenderShort` / `shortRenderNeedsOutput` と `shortRenderReason` を表示し、`renderShortVideo()` を呼び出します。
- 通常動画とショートは `EditBayBackend._start_render()` → `workflow_actions.prepare_render_request()` → `_start_command()` の共通経路を使います。

`actionCapabilities` は実行中状態・プロジェクト・素材・設定・依存状態の変更を通知します。未保存のデバイス選択は `actionCapabilitiesForDevice(device)` で評価できます。

| Capability | 条件 |
| --- | --- |
| `canTranscribe` | FFmpeg・ffprobe・WhisperX、動画・音声・プロジェクト保存先。cuda選択時のみPyTorch CUDAも必要 |
| `canRenderNormal` | FFmpeg・ffprobe、プロジェクトと動画素材、書き込み可能な出力先 |
| `canRenderShort` | 通常出力の条件に加え、有効なショート設定とクリップ |
| `normalRenderNeedsOutput` / `shortRenderNeedsOutput` | 完成動画の出力先だけが未設定。ボタンから保存先選択を開始できる |
| `canUseTranscriptionCuda` | PyTorch CUDAの検出結果 |
| `canUseNvenc` | FFmpegによるNVENC実行プローブの結果 |

共通処理は渡された依存状態から `h264_nvenc` / `libx264` を選びます。WhisperX・PyTorch CUDA・字幕件数を書き出し条件にしません。`validate_render_output()` はファイルを作らず出力先を検証します。

## プロジェクト保存先と完成動画の出力先

「素材設定」に2つの保存先を表示します。

- **プロジェクト保存先**: 新規作成時は動画の隣の `<動画名>.subtitle-project.json`。読み込んだプロジェクトは実際に開いたファイルへ保存・自動保存します。「別名保存」で移動しても完成動画の出力先は変わりません。既存ファイルがある場合は空プロジェクトで上書きせず、開くか別の保存先を選びます。
- **完成動画の出力先**: JSONの `output_dir`。未設定（空文字）のままプロジェクト作成・字幕編集・カット・ミキサー・文字起こし・ショート構成ができます。通常／ショートの書き出し開始時に未設定なら選択します。キャンセルしても編集を継続できます。出力先の変更では、プロジェクトの保存先・編集状態・素材の参照先を変更しません。

通常動画は `<プロジェクト名>.edited.subtitled.mp4`、ショートは `<プロジェクト名>.short.mp4` を完成動画の出力先に保存します。既存JSONの `output_dir` もそのまま完成動画の出力先として扱い、プロジェクトファイルの場所から推測し直しません。

GUIの文字起こし作業ファイルはプロジェクトの隣の `.<プロジェクト名>.work/` に保存します。文字起こしキャッシュ・中間JSON・追加／置換用の一時プロジェクトが含まれます。保存したJSONの `transcription.work_dir` からログの場所も追跡できます。別名保存後の文字起こしは新しい保存先の作業フォルダを使います。既存のキャッシュや参照ファイルは移動・削除しません。

相対パスの文字起こし辞書は、作業フォルダを移しても同じファイルを参照します。新規GUIプロジェクトでは作成時のプロジェクトフォルダを基準にし、既存プロジェクトでは従来の作業先（未記録なら `output_dir`）を引き継ぎます。基準は `transcription.context_base_dir` に保存し、出力先変更・別名保存・再読込後も維持します。

CLIの従来の `transcribe --output-dir DIR` は互換性のため、作業先と完成動画の出力先を同じDIRにします。分ける場合は `--project-path` と `--render-output-dir` を指定します。完成動画の出力先を未設定にするには `--render-output-dir ""` を渡します。`render` / `render-short` の `--output FILE` はJSONの設定より優先されます。

CLIの辞書の相対パスは従来どおり `--output-dir` 基準です。作業先と分ける場合は `--context-base-dir DIR` を指定します。指定時には確認済み辞書のパスを絶対パスで生成プロジェクトに記録します。

```bash
python -m src.subtitle_workflow transcribe --video game.mp4 --audio-file 1-alice.flac --output-dir projects/.edit.work --project-path projects/edit.subtitle-project.json --render-output-dir "" --run
python -m src.subtitle_workflow render --project projects/edit.subtitle-project.json --output exports/edit.mp4 --run
```

## 検証

```bash
python -m unittest tests.test_workflow_actions
RUN_FFMPEG_SMOKE=1 python -m unittest tests.test_workflow_action_semantic_e2e
python -m unittest tests.test_gui_editor tests.test_qml_static
```

実動画テストは、共通境界が選んだCPUコーデックとコマンドで字幕0件の通常・ショート動画を生成し、H.264、画面サイズ、尺、映像の色、音声を検証します。NVENCの選択は依存状態を与えた単体テストで検証し、GPU実機でのエンコード検証とは区別します。
