# GUI性能ベンチマーク

大規模な字幕プロジェクトで、Python処理だけでなくQt/QML境界、delegate生成、Qt Multimediaの読み込み・再生、GUIイベントループの停止を同じ操作手順で測定します。通常CIの決定的な契約テストと、実時間を扱うWindows専用ベンチマークは分離しています。

## 測定対象

`scripts/run_gui_performance.py`は3,000件と10,000件の字幕について、次の8シナリオを順番に実行します。

| シナリオ | 実操作 | 主な出力 |
| --- | --- | --- |
| `project_initial_interactive` | プロジェクトと`Main.qml`を開く | 初回操作可能時間、初期delegate、MediaPlayer数 |
| `main_preview_continuous_playback` | メインプレビューを30秒再生 | 再生位置、映像frame、UI playhead遅延 |
| `editor_open_close_without_media_reload` | 字幕編集画面を開閉 | source変更、Loading遷移、MediaPlayer数 |
| `list_and_timeline_selection` | 先頭・中間・末尾を一覧とタイムラインで選択 | delegate数、QML/Python呼び出し数 |
| `subtitle_text_time_speaker_font_edit` | 本文・時刻・話者・フォント・倍率を変更 | 操作時間、整形回数、全件処理回数 |
| `playback_selection_and_timeline_follow` | 編集画面で再生を継続 | 選択追従、timeline位置、playhead遅延 |
| `short_mode_selection_reorder_delete_settings` | クリップ選択・移動・trim・削除・設定変更 | delegate数、clip materialize回数 |
| `short_visual_update_preserves_playback` | fit・背景色・字幕倍率だけを変更 | source・Loading・停止・再開回数 |

各シナリオでは、10msのprecise timerによるイベントループ遅延のp50・p95・最大値、test-onlyバックエンドによるQML/Python境界呼び出し、字幕整形と配列materialize回数、MediaPlayerの状態遷移、プロセスのpeak RSSをJSONへ保存します。計測用counterはベンチマーク専用サブクラスにだけ存在し、通常起動時には実行されません。

## fixture

fixtureは乱数や外部ダウンロードを使いません。字幕には単語時刻、重なり、4話者、手動改行、1行・2行指定、字幕ごとのフォントと倍率、全字幕に対応するショートクリップを含みます。動画と440Hzの音声はFFmpegのlavfiから実行時に生成され、生成物はリポジトリへコミットしません。

fixtureだけを生成する場合:

```powershell
python scripts/generate_large_gui_fixture.py `
  --output-dir artifacts/gui-fixtures `
  --segment-count 3000 `
  --segment-count 10000
```

同じ出力先・件数ではproject JSONがbyte単位で一致します。動画ファイルはFFmpegバージョン間のbyte一致を契約にせず、同じ解像度、frame rate、長さ、入力filterを使用します。

## ローカル実行

FFmpeg、FFprobe、`requirements.txt`の依存関係が必要です。

```powershell
python scripts/run_gui_performance.py `
  --segment-count 3000 `
  --segment-count 10000 `
  --repetitions 3 `
  --playback-seconds 30 `
  --output artifacts/gui-performance-current.json
```

短い動作確認では件数、繰り返し、再生時間を下げられます。

```powershell
python scripts/run_gui_performance.py `
  --segment-count 100 `
  --repetitions 1 `
  --playback-seconds 1 `
  --output artifacts/gui-performance-smoke.json
```

各繰り返しは新しいPythonプロセスで動かします。これにより、QApplication、QML engine、MediaPlayer、preview text cache、peak memoryが前回の測定から持ち越されません。

## 結果の判定

絶対上限とbaselineからの悪化率を別々に記録します。既定値は、1操作45秒、イベントループ停止3秒、playheadのp95遅延500ms、baseline比20%です。これらはハングや明確な追従不能を検出する安全上限で、runner更新後は同じWindows runnerで3回以上測定してから狭めます。

```powershell
python scripts/compare_gui_performance.py `
  --current artifacts/gui-performance-current.json `
  --baseline artifacts/gui-performance-baseline.json `
  --max-regression-percent 20 `
  --output artifacts/gui-performance-comparison.json
```

`--fail-on-regression`を付けた場合だけ時間閾値をexit codeへ反映します。失敗結果にはfixture件数、シナリオ名、計測項目、現在値、baseline、悪化率、上限が入るため、遅い箇所をログから特定できます。source再設定、停止・再開、全件materialize、virtualizationなど決定的な契約は常にベンチマーク本体で判定します。

## CIとbaseline運用

- 通常の`CI`では`tests/test_gui_large_project_performance.py`でfixtureの再現性とレポート診断を、`tests/test_gui_editor.py`で3,000件のListView virtualizationと編集画面でのMediaPlayer再利用を、時間閾値なしで検証します。
- `GUI performance` workflowはWindowsで実動画を再生し、JSONを30日間Artifactとして保存します。QML・GUI・計測コードが変わるPRと手動実行が対象です。
- workflowは既定で#302適用前の`b600e90`を別worktreeへ展開し、PR側と同じfixture・同じ計測ハーネス・同じrunnerで3,000件の比較値を取得します。現在側は3,000件と10,000件を測定します。
- 通常のPRでは時間差をレポートだけに残し、不安定なrunner時間でマージを止めません。基準runnerで連続3回以上の分布を確認した後、手動実行の`fail_on_regression`を有効にして予算変更を検証します。
- baselineを更新する際は、workflow run URL、commit SHA、Windows image、Python・PySide6・FFmpegバージョン、各回のJSONを残します。異なるrunnerや依存バージョンの結果を同じbaselineとして混ぜません。

### #302前後の比較

比較元`b600e90`は#302直前、`4968485`は#302のmerge commitです。専用workflowが両者を同一環境で再実行するため、古い測定値を新しいrunnerへ流用しません。主に次の変化を確認します。

- `property.subtitleSegments`と`property.shortVideoClips`の全件materialize回数
- 編集画面を開いた時のMediaPlayer数、source変更、Loading遷移
- 字幕一覧・タイムライン・ショート一覧のdelegate数
- 再生中の`activeSubtitleSegments`、`setEditorPlayhead`呼び出しとplayhead遅延
- プロジェクト読込、字幕編集、ショート設定変更の操作時間とpeak RSS

新しいQML構造でobject名が変わる場合は、製品コード、シナリオrunner、通常CI契約を同じPRで更新してください。計測不能を性能改善として扱わないためです。
