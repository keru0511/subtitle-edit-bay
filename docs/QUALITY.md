# Quality checks

This repository uses a staged quality gate. The first enforced gate is intentionally small so it can run on every pull request without requiring a broad style-only rewrite.

## Local commands

Install runtime dependencies:

```powershell
python -m pip install -r requirements.txt
```

Install development-only tooling:

```powershell
python -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` also includes the YAML parser used by the GitHub Actions
contract tests. Any CI job that runs `tests/test_release_distribution.py` must
install both dependency files.

Run the same default quality checks as the local entrypoint:

```powershell
python scripts/check_quality.py
```

Run only Ruff lint checks:

```powershell
python scripts/check_quality.py --lint-only
```

Run Ruff lint checks for selected files or directories:

```powershell
python scripts/check_quality.py --lint-only --paths src tests
```

Run only the test suite:

```powershell
python scripts/check_quality.py --tests-only
```

Check that every `test_*.py` module is importable and collected by standard
unittest discovery:

```powershell
python scripts/check_unittest_discovery.py
```

The checker rejects module-level `test_*` functions, zero-test modules, and
import failures. It also fails when no test modules match or a custom
`load_tests` hook raises an error. Platform-specific skipped tests still count
as discovered.

## GUI test harness

GUI behavior tests share `tests/gui_test_harness.py`. The harness owns each QML
engine and window, provides bounded condition-based waits, finds controls by
`objectName`, performs mouse and keyboard interactions, checks visual bounds,
captures Qt/QML runtime messages for failure diagnostics, and reliably drains
deferred deletion during cleanup. `tests/edit_bay_gui_test_session.py` owns the
shared backend, creates a separate workspace for every test, and centralizes the
backend state and background-work reset.

Use `wait_until` for asynchronous UI state instead of fixed sleeps. Runtime QML
warnings from application-owned QML should fail the relevant behavior test.
Only allow a known message with `AllowedQmlMessage`, including a specific reason;
the harness deliberately ignores unrelated Qt backend noise when applying that
policy.

Large-project responsiveness uses deterministic contracts in the regular test
suite and a separate Windows Qt Multimedia benchmark. See
[`GUI_PERFORMANCE.md`](GUI_PERFORMANCE.md) for the eight scenarios, metrics,
baseline comparison, and local commands.

## QML test contracts

QML source assertions are limited to contracts that cannot be expressed more
reliably through a loaded UI. Keep `qmllint`, security prohibitions, exact copy
that is itself a product requirement, importability, public component existence,
and stable `objectName` values required by the GUI harness. Test user actions,
visibility, enabled state, layout bounds, backend state, and saved project state by
loading `Main.qml` through the GUI harness.

Do not assert internal binding expressions, component placement, fixed pixel
values, or the count and order of current layout elements. These details may
change without changing the product contract. When a static behavior assertion is
removed, identify its replacement behavior test or owning feature Issue in the
pull request.

Future UI behavior tests are owned by the feature that introduces the behavior:

| Behavior | Owning Issue |
|---|---|
| Codex sidebar authentication and persistence | #249 |
| Shared preview, edit modes, and playhead | #273 |
| Subtitle and volume modes | #274 |
| Cut mode and time mapping | #254 |
| Separate short-video workspace | #275 |
| Transcription and render actions | #276 |
| Project-first start screen | #277 |

Each feature pull request adds its behavior tests with the implementation. Do not
add permanently skipped tests or contracts for components that do not exist yet.

## Windows launcher test contracts

Test BAT and PowerShell launcher routing by starting the real entrypoint against a
temporary distribution tree. Assert the resolved install root, exclusive GUI or
repair child process, exit result, and diagnostic file instead of requiring C API
names or PowerShell function names to remain in source files. Windows-only
launcher behavior belongs to the required `windows-launcher-runtime` CI group;
Linux skips do not count as coverage. A missing required shell on Windows is a
test failure, not a skip.

Native launcher and installer artifact requirements belong to their release
gates. Product-EXE presence and removal of the PowerShell fallback are owned by
#257. PE subsystem, architecture, imports, resources, and signing inspection are
owned by #262. Do not preserve a future-obsolete fallback with a positive source
marker while those artifact contracts are pending.

## Windows updater test contracts

Test updater behavior by running the real BAT or PowerShell entrypoint against a
temporary distribution tree. Assert installer start or non-start, exit result,
installed version and files, preserved user data, recovery state, structured
result, and restart marker. Positive source markers for PowerShell commands,
function names, and log messages do not count as behavior coverage. Windows-only
updater behavior belongs to the required `windows-launcher-runtime` CI group;
Linux skips do not count as coverage. A missing required shell on Windows is a
test failure, not a skip.

Git checkout tests use a temporary bare repository to verify fast-forward-only
updates, tracked-change rejection, setup execution, and untracked-data
preservation. ZIP release tests use a loopback HTTP server and URL dependency
injection to exercise release metadata resolution and archive download without
external network access. The focused prohibition against `git reset --hard`
remains a source-level data-loss guard.

Installer-only parent process-tree and file-lock release, atomic
application/runtime rollback, old-version restart after rollback, and restart
executable re-resolution from the updated install root are owned by #260.
PowerShell restart fallback removal and hidden-window behavior are owned by #257,
and installer artifact inspection is owned by #262.

## Media semantic E2E contracts

Use `tests/media_test_helpers.py` for deterministic, download-free lavfi fixtures,
bounded FFmpeg/FFprobe execution with process-tree termination on timeout, stream
probing, RGB frame extraction, and region-level pixel comparison. Keep this helper
outside the `test_*.py` discovery pattern. The owning test module belongs to
`ffmpeg-runtime`; explicitly selected representative cases are rerun by
`windows-ffmpeg-runtime`.

Subtitle burn-in tests compare a rendered output with a no-dialogue control at the
same timestamp. For the 30 fps fixture, timing checks use a one-frame allowance
plus 20 ms of mux tolerance and sample on both sides of the start and end
boundaries. Manual line breaks must appear as `\N` in ASS and increase the
vertical occupied pixel region; file existence and ASS text alone are not enough.

CPU fallback tests inject only the probed NVENC capability. They pass the codec
returned by the production automatic-selection boundary into the real render,
then inspect the output codec, pixel format, duration, faststart layout, and audio
stream. Do not mock the FFmpeg encode or treat a permanently GPU-less runner as
the fallback condition.

Audio mixer semantic tests use three steady, low-level stereo tones at 440 Hz,
880 Hz, and 1320 Hz. They save the complete channel state, invoke the production
project render, reload the project, and inspect the output audio. Mute and solo
checks use narrow frequency bands and require at least 15 dB of separation. Gain
checks compare 50%, 100%, and 200% outputs with the expected `20 * log10(gain)`
change within 2 dB while the fixture remains below the limiter range.

Normalize checks measure EBU R128 integrated loudness with FFmpeg `ebur128`, not
`volumedetect` mean volume. A six-second steady fixture provides a deterministic
window: discard the first second as warm-up and measure the following four
seconds. Compare the source with normalize-off output, then compare normalize-on
output with the configured LUFS target. Assertion diagnostics must include
measured frequency levels or integrated loudness, target and tolerance, the
analysis command, and FFmpeg stderr.

The required media coverage matrix is intentionally small and semantic:

| Contract | Linux `ffmpeg-runtime` | Windows `windows-ffmpeg-runtime` |
| --- | --- | --- |
| Subtitle burn-in timing, line count, manual break | `test_media_semantic_e2e` | timing, line-count, manual-break selectors |
| CPU fallback stream contract | `test_media_semantic_e2e` | `test_cpu_fallback_produces_compatible_h264_with_audio` |
| Silence/manual cut duration, frame order, audio frequency, subtitle retime | `test_manual_cut_semantic_e2e` | duration, frame, audio, subtitle selectors |
| Audio mixer mute, solo, gain, EBU R128 normalize | `test_audio_mix_semantic_e2e` | mute, solo, gain, normalize selectors |
| Short resolution, duration, audio, subtitle frame | `test_short_video_semantic_e2e` and `test_short_video_ass` | source-order, media contract, subtitle, and Unicode smoke selectors |

`windows-ffmpeg-runtime` reruns only these representative selectors; the full
Linux matrix remains the required cross-platform semantic run. The fixture and
probe generation is centralized in `tests/media_test_helpers.py`, including
testsrc inputs and non-yuv420p source formats used by the burn-in smoke. This
keeps the required matrix free of GPU/NVENC success mocks and large binary
fixtures while allowing the longer fit/crossfade/BGM matrix to remain an
optional Windows smoke case.

Run the release workflow contract tests directly:

```powershell
python -m unittest tests.test_release_distribution -v
```

These tests parse the workflow and validate the job DAG, transitive test/build
gates, least-privilege publish permissions, and release step ordering. Mutation
tests confirm that missing dependencies, cycles, `continue-on-error`,
success-bypassing `if` conditions, implicit token permissions, and skipped
artifact verification, masked contract failures, or post-verification mutation
steps are rejected.

Run only Ruff format checks:

```powershell
python scripts/check_quality.py --format-only
```

Run Ruff format checks for selected files or directories:

```powershell
python scripts/check_quality.py --format-only --paths scripts/check_quality.py
```

Run only mypy type checks:

```powershell
python scripts/check_quality.py --type-only
```

Run mypy type checks for selected files or directories:

```powershell
python scripts/check_quality.py --type-only --paths scripts/check_quality.py
```

Run lint, type check, and tests together for selected files or directories:

```powershell
python scripts/check_quality.py --include-type-check --paths scripts/check_quality.py
```

Run lint, format check, and tests together:

```powershell
python scripts/check_quality.py --include-format
```

Apply Ruff formatting locally:

```powershell
python scripts/check_quality.py --format-only --fix-format
```

Install dependencies and then run checks from a fresh environment:

```powershell
python scripts/check_quality.py --install-runtime --install-dev
```

CIのPython品質ジョブは共通の`check_quality.py`を使い、Ruff、同スクリプトの整形確認、設定した対象の型チェックを実行します。Windows上の実プロセス・GUI・メディア検証は別ジョブで維持します。

## Ruffの対象範囲

`E9`（構文エラー）と`F`（Pyflakes全ルール）をPython全体へ適用します。未定義名に加えて、未使用import・未使用変数・重複定義などを検出します。互換APIとして再公開する名前は同名aliasを使い、再公開の意図を明示します。外部から使われる名前を機械的に削除しません。

全体の整形・import並び替え・広範なスタイルルールは別変更で扱います。今回、全体への整形適用は行いません。

## 型チェックの対象範囲

CIとローカルの既定対象は`pyproject.toml`の`tool.mypy.files`に集約しています。`--paths`を指定すると、その実行に限って対象を上書きできます。

```sh
# 設定済みの対象をまとめて確認
python scripts/check_quality.py --type-only
# Windowsの条件分岐で確認（CIではlinux・win32・darwinを順に実行）
python scripts/check_quality.py --type-only --type-platform win32
# 未移行コードを含めて調査するときの例
python scripts/check_quality.py --type-only --paths src scripts tests
```

既存の品質チェック用スクリプトとOS境界4モジュールに加え、実行設定の読み込み・検証・データ境界、文字起こしコンテキスト・辞書・非破壊カットのタイムラインモデルと対応するテストを対象にしています（計19ファイル）。OS境界はstrictと明示的な`Any`禁止を継続します。移行済みのデータ境界・モデル・対応テストの14ファイルでは、次の規則をすべて適用します。

- `strict`: 注釈のない関数、型引数のないジェネリックなどを禁止
- `disallow_any_explicit`: 明示的な`Any`を禁止
- `disallow_any_expr`: 式に含まれる暗黙の`Any`を禁止
- `disallow_any_unimported`: 型情報のないimport由来の`Any`を禁止
- `disallow_any_decorated`: デコレーター適用後の関数型に含まれる`Any`を禁止

標準ライブラリのJSONデコード結果は`object`で受けます。コンテナを読み取る前に実際の形を検証し、各要素も`object`として扱います。`object`は任意の演算や属性アクセスを許可しないため、利用前の型の絞り込みが必要です。`cast`・`type: ignore`・検査除外を増やして通す方針は採りません。実行設定は未知のキーやセクションを保持する互換契約があるため、固定フィールドのモデルとして扱える領域とは分けて移行します。

CIの3つのOS設定は静的な条件分岐の検証であり、各OSでの実行テストの代わりではありません。mypy全体への`--ignore-missing-imports`は使いません。品質ジョブにGUI実行環境を導入しないため、既存のPySide6未導入許容は維持しています。未導入時はQt APIの型を保証しません。今回移行したデータ境界はPySide6に依存しません。

## Any禁止への全体移行

最終対象は本体・スクリプト・テスト全体です。初回診断は271ファイル中248ファイルに21,582件のエラーがあり、明示的な`Any`だけでなく、モック・JSON・Qt・外部ライブラリからの伝播も含んでいます。この件数はmacOS / Python 3.13のローカル環境での調査値で、未導入ライブラリの診断も含みます。Python 3.10のCI基準での固定件数ではありません。

レビュー可能な単位で順に移行し、移行済みファイルはそのPRでCIの対象に追加します。

1. 実行設定とデータ境界、そのテスト（#448で完了）
2. 字幕プロジェクト・文字起こし・辞書などのデータモデルとJSON契約（#449で文字起こしコンテキスト・辞書を移行、今回は非破壊カットのタイムラインを移行。字幕セグメント・複数クリップのシーケンスは後続）
3. パイプライン・CLI・配布スクリプトと外部プロセス境界
4. Qt・機械学習などの外部ライブラリ境界、GUI、対応するモック・テスト
5. 全対象へ規則を適用し、段階移行用の対象一覧・限定設定を撤去

文字起こしコンテキスト、辞書エントリー・出典、辞書候補の保存形式は`TypedDict`でフィールドの型を定義しています。入力は`object`として受け、既存の正規化で検証してからデータクラスへ変換します。辞書候補の正規化は`transcription_metadata.py`へ分離し、ネットワーク取得処理から独立させています。既存の`transcription_web_dictionary`からのimportも維持します。Qtやネットワーク取得自体のAny禁止はまだ完了していません。

コンテキストの単体テストは`test_transcription_context_models.py`へ分けてAny禁止の対象とし、字幕プロジェクトとの保存・読込の統合テストは`test_transcription_context.py`に維持しています。

非破壊カットの`video_timeline.py`では、入力をobjectから検証し、表示用の固定形式を`VideoTimelineView`などのTypedDictで定義しています。保存データは未知の拡張キー・値を保持するため`dict[object, object]`とし、固定形式と区別します。数値化は共通境界に集約し、従来の数値文字列・bool・整数への切り捨てを維持します。有限値や範囲の検証はタイムライン側で行います。タイムラインの単体テストは`test_video_timeline_models.py`でAnyを禁止し、字幕プロジェクトとの統合テストは既存ファイルに残しています。

進捗は診断件数だけでなく、Any禁止をCIで保証する対象が増えたかで確認します。全体移行は未完了です。

## Heavier Windows checks

The regular CI workflow also runs Windows smoke checks for GUI startup, FFmpeg media handling, Qt Multimedia playback, audio mixer operations, and installer startup.

WhisperX, PyTorch, and CUDA checks are intentionally kept in the manual Windows deep runtime workflow because they are slower and depend on runner capabilities.
