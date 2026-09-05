# 文字起こしツールと動画出力

通常動画の編集モードは `subtitle / cut / audio` の3種類です。文字起こしと書き出しはモードを切り替えず、既存の処理状態・進捗・停止・エラー表示を使います。

ワークスペース上部の「ツール」から文字起こしの追加・更新と辞書設定、「出力」から通常動画の書き出しを実行します。「文字起こし・出力設定」からモデルと処理デバイスを変更できます。

## 開始画面・ショート画面からの再利用

- 開始画面（#277）は、素材選択後に `MainWorkflowScreen.requestTranscription()` を呼び出します。既存プロジェクトの追加・置換確認、同名プロジェクトの上書き確認、設定と進捗を共有します。
- ショート画面（#275）は `actionCapabilities.canRenderShort` と `shortRenderReason` を表示し、`renderShortVideo()` を呼び出します。
- 通常動画とショートは `EditBayBackend._start_render()` → `workflow_actions.prepare_render_request()` → `_start_command()` の共通経路を使います。

`actionCapabilities` は実行中状態・プロジェクト・素材・設定・依存状態の変更を通知します。未保存のデバイス選択は `actionCapabilitiesForDevice(device)` で評価できます。

| Capability | 条件 |
| --- | --- |
| `canTranscribe` | FFmpeg・ffprobe・WhisperX、動画・音声・出力先。cuda選択時のみPyTorch CUDAも必要 |
| `canRenderNormal` | FFmpeg・ffprobe、プロジェクトと動画素材、書き込み可能な出力先 |
| `canRenderShort` | 通常出力の条件に加え、有効なショート設定とクリップ |
| `canUseTranscriptionCuda` | PyTorch CUDAの検出結果 |
| `canUseNvenc` | FFmpegによるNVENC実行プローブの結果 |

共通処理は渡された依存状態から `h264_nvenc` / `libx264` を選びます。WhisperX・PyTorch CUDA・字幕件数を書き出し条件にしません。出力先は従来のプロジェクト隣接パスを維持し、分離は #272 で扱います。`validate_render_output()` はファイルを作らず出力先を検証します。

## 検証

```bash
python -m unittest tests.test_workflow_actions
RUN_FFMPEG_SMOKE=1 python -m unittest tests.test_workflow_action_semantic_e2e
python -m unittest tests.test_gui_editor tests.test_qml_static
```

実動画テストは、共通境界が選んだCPUコーデックとコマンドで字幕0件の通常・ショート動画を生成し、H.264、画面サイズ、尺、映像の色、音声を検証します。NVENCの選択は依存状態を与えた単体テストで検証し、GPU実機でのエンコード検証とは区別します。
