# CIテストグループ

通常のCIでは、テストを実行環境への依存度ごとに分けます。分類の正本は
`tests/ci_test_groups.json`、実行入口は `scripts/run_ci_tests.py` です。

## 分類と実行環境

| グループ | 実行環境 | 対象 |
| --- | --- | --- |
| `portable-unit` | Ubuntu | OSや実ランタイムに依存しないロジック、モデル、設定、モックを使うプロセステスト |
| `qt-gui` | Ubuntu（offscreen） | クロスプラットフォームのQt/QML、GUI状態、音声ミキサーのテスト |
| `ffmpeg-runtime` | Ubuntu | 実際のFFmpeg/FFprobe/libassを使うクロスプラットフォームのメディアテスト |
| `windows-runtime` | Windows | 更新管理ロジックとWindows上のQProcessを検証するテスト |
| `windows-launcher-runtime` | Windows | Python外の依存を追加せず、PowerShell・BAT・インストーラー更新経路を実プロセスと実ファイルシステムで検証するテスト |
| `windows-ffmpeg-runtime` | Windows | Windows版FFmpeg、Qt Multimedia、GUI音声ミキサーを実際に動かすスモークテスト |
| `ffmpeg6-compat` | Windows | 固定したFFmpeg 6.1.1でフィルタースクリプト互換性を検証するテスト |

Windowsの `Main.qml` 起動スモークは、テストモジュールとは別に
`Start GUI on Windows` ステップで実行します。インストーラースモークと
`.github/workflows/windows-deep-runtime.yml` のWhisperX/PyTorch/CUDA検証も独立したままです。
これらの重い準備処理を通常のテストグループ内では繰り返しません。
依存不要の `windows-launcher-runtime` は専用ジョブで並列実行し、通常のWindows
ランタイムジョブではFFmpeg 9.0.1の展開済みツリーをキャッシュします。

## 所有権と意図的な再実行

各 `tests/test_*.py` モジュールは、マニフェスト内のどれか1つの `modules` に
必ず一度だけ登録します。未分類、重複登録、削除済みファイルの登録がある場合は
`--validate` が失敗するため、新しいテストが暗黙にWindowsの全件実行へ戻ることはありません。

別OSでも同じモジュールの特定ケースだけ再実行する必要がある場合は、対象グループの
`selectors` に完全なunittest名を登録します。現在は、クロスプラットフォームの
`test_short_video_ass` をLinuxで所有しつつ、Windows固有のUnicodeパスと
FFmpegフィルタースクリプトのケースだけを `windows-ffmpeg-runtime` で明示的に再実行します。
selectorは `tests.test_module.TestCaseClass.test_method` 形式の標準unittest名にします。
モジュール直下の `test_*` 関数は標準discoveryで収集されないため許可しません。

## ローカル実行

分類だけを検証する場合:

```powershell
python scripts/run_ci_tests.py --validate
```

標準unittestから全test moduleが収集できることを検証する場合:

```powershell
python scripts/check_unittest_discovery.py
```

1グループを実行する場合:

```powershell
python scripts/run_ci_tests.py --group portable-unit
```

同じ環境の複数グループは、`--group` を繰り返して一度に実行できます。

```powershell
python scripts/run_ci_tests.py --group portable-unit --group qt-gui
```

実行後はテスト数、失敗数、エラー数、スキップ数、経過時間を表示します。
GitHub Actionsでは同じ情報とスキップ理由ごとの件数をStep Summaryにも追記します。

## テスト追加時の手順

1. `unittest.TestCase` を持つ `tests/test_*.py` を追加する。
2. 主な実行環境に対応する1グループの `modules` に、拡張子なしのモジュール名を辞書順で追加する。
3. 別環境で必要なケースだけを再実行する場合は、その環境の `selectors` に完全名を辞書順で追加する。
4. discovery checker、`python scripts/run_ci_tests.py --validate`、対象グループを実行する。

モジュール単位の所有先を決められない場合は、テスト責務を分けてから登録してください。
