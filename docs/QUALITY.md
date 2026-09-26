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

既存の品質チェック用スクリプトとOS境界4モジュールに加え、実行設定の読み込み・検証・データ境界、文字起こしコンテキスト・辞書・非破壊カット・複数クリップのシーケンス・ショート動画設定・字幕プロジェクト全体・構成モデルと行数設定、スナップショット・クラッシュ復旧・音声ミックス・組版の分割境界と採点・行数調整・句読点の付け直し・ASS出力・話者色設定・SRT/VTT/CSV出力・字幕レビュー、および対応するテストを対象にしています（計59ファイル）。OS境界はstrictと明示的な`Any`禁止を継続します。移行済みのデータ境界・モデル・対応テストの54ファイルでは、次の規則をすべて適用します。

- `strict`: 注釈のない関数、型引数のないジェネリックなどを禁止
- `disallow_any_explicit`: 明示的な`Any`を禁止
- `disallow_any_expr`: 式に含まれる暗黙の`Any`を禁止
- `disallow_any_unimported`: 型情報のないimport由来の`Any`を禁止
- `disallow_any_decorated`: デコレーター適用後の関数型に含まれる`Any`を禁止

標準ライブラリのJSONデコード結果は`object`で受けます。コンテナを読み取る前に実際の形を検証し、各要素も`object`として扱います。`object`は任意の演算や属性アクセスを許可しないため、利用前の型の絞り込みが必要です。`cast`・`type: ignore`・検査除外を増やして通す方針は採りません。実行設定は未知のキーやセクションを保持する互換契約があるため、固定フィールドのモデルとして扱える領域とは分けて移行します。

CIの3つのOS設定は静的な条件分岐の検証であり、各OSでの実行テストの代わりではありません。mypy全体への`--ignore-missing-imports`は使いません。品質ジョブにGUI実行環境を導入しないため、既存のPySide6未導入許容は維持しています。未導入時はQt APIの型を保証しません。今回移行したデータ境界はPySide6に依存しません。

行数調整と句読点の付け直しは未検証の値を`object`で受け、形を確かめてから読み取ります。行数調整では数値文字列などの既存の変換と、元の字幕辞書を使う経路を維持します。句読点の付け直しでは入力の単語・拡張値を変更せず、必要な部分だけ複製します。保存処理を呼ぶ従来の`test_subtitle_line_count.py`は実行テストとして残し、型チェックには保存処理から独立した`test_subtitle_line_count_contracts.py`を加えます。

ASS出力では、CLIから読み込んだJSONを`object`として受け、字幕の行数調整へ渡します。CLI引数は型付きの名前空間で受け取り、未検証の`Any`を広げません。話者色設定はJSONの辞書構造を確認してから読み取り・更新し、既存の別名と未知フィールドを保持します。ASSヘッダー生成と関連する軽量テストも型チェック対象です。保存・パイプラインの統合テストは従来どおり実行します。

SRT/VTT/CSV出力では、外部入力を`object`として検証し、出力に必要な時刻・本文・ID・話者だけを型付きの内部データへ変換します。元の数値文字列、同時刻の安定した並び順、CSVの値の文字列化とエスケープを維持します。

字幕レビューでは、セグメントと判定根拠の値を`object`で扱い、既存の数値変換後にルールを評価します。レビュー状態の更新と再照合では型付きデータクラスの`replace`を使い、判定履歴とJSON出力を維持します。

## Any禁止への全体移行

最終対象は本体・スクリプト・テスト全体です。初回診断は271ファイル中248ファイルに21,582件のエラーがあり、明示的な`Any`だけでなく、モック・JSON・Qt・外部ライブラリからの伝播も含んでいます。この件数はmacOS / Python 3.13のローカル環境での調査値で、未導入ライブラリの診断も含みます。Python 3.10のCI基準での固定件数ではありません。

レビュー可能な単位で順に移行し、移行済みファイルはそのPRでCIの対象に追加します。

1. 実行設定とデータ境界、そのテスト（#448で完了）
2. 字幕プロジェクト・文字起こし・辞書などのデータモデルとJSON契約（#449で文字起こしコンテキスト・辞書を移行、#450で非破壊カットのタイムラインを移行。#453で複数クリップのシーケンスとショート動画設定を移行。#458で字幕セグメント・話者・波形情報・音声ミックスと行数設定を移行。#461でプロジェクト全体のモデルとスナップショット・クラッシュ復旧を移行。#462で保存前の音声ミックス調整とフィルター生成を移行。#463で日本語分割・禁則ルール・改行候補の採点を移行。#466で字幕パッカー本体・互換モジュール・字幕イベントモデルを移行。#471で行数調整・句読点の付け直しを移行。#475でASS出力・ヘッダーと話者色設定を移行。#477でSRT/VTT/CSV出力を移行。今回は字幕レビューの判定ルールとキューを移行。通常の保存処理は後続）
3. パイプライン・CLI・配布スクリプトと外部プロセス境界
4. Qt・機械学習などの外部ライブラリ境界、GUI、対応するモック・テスト
5. 全対象へ規則を適用し、段階移行用の対象一覧・限定設定を撤去

文字起こしコンテキスト、辞書エントリー・出典、辞書候補の保存形式は`TypedDict`でフィールドの型を定義しています。入力は`object`として受け、既存の正規化で検証してからデータクラスへ変換します。辞書候補の正規化は`transcription_metadata.py`へ分離し、ネットワーク取得処理から独立させています。既存の`transcription_web_dictionary`からのimportも維持します。Qtやネットワーク取得自体のAny禁止はまだ完了していません。

コンテキストの単体テストは`test_transcription_context_models.py`へ分けてAny禁止の対象とし、字幕プロジェクトとの保存・読込の統合テストは`test_transcription_context.py`に維持しています。

非破壊カットの`video_timeline.py`では、入力をobjectから検証し、表示用の固定形式を`VideoTimelineView`などのTypedDictで定義しています。保存データは未知の拡張キー・値を保持するため`dict[object, object]`とし、固定形式と区別します。数値化は共通境界に集約し、従来の数値文字列・bool・整数への切り捨てを維持します。有限値や範囲の検証はタイムライン側で行います。タイムラインの単体テストは`test_video_timeline_models.py`でAnyを禁止し、字幕プロジェクトとの統合テストは既存ファイルに残しています。

複数クリップの`video_sequence.py`も拡張キー・値を保持し、表示形式はTypedDictで定義します。`short_video_schema.py`は保存形式自体をTypedDictで定義し、クリップの任意フィールドの省略を維持します。旧ショート形式の明示的な移行時に背景色が文字列でない場合は、属性アクセスの例外ではなく`ShortVideoError`で拒否します。シーケンスの純粋なモデルテストは`test_video_sequence_models.py`へ分離し、保存・GUIとの統合テストは既存ファイルに残します。

`subtitle_project_schema.py`には字幕セグメント・話者・波形情報・音声ミックスチャンネル・音声ミックスをまとめ、ファイル保存・NumPy・組版から切り離しています。行数設定の検証は`subtitle_line_count_config.py`へ分離しました。既存の`subtitle_project`・`subtitle_line_count`からのimportは同じクラス・関数・例外を再公開して維持します。モデルの純粋なテストも依存の軽い別ファイルへ移し、従来の保存・表示・ワークフローの統合テストは継続します。

これらの入力もobjectとして検証し、数値文字列や既存の丸め・既定値を維持します。単語データは辞書の配列、波形ピークは数値の反復可能な値として検証し、不正な構造・要素は`SubtitleProjectError`で拒否します。数値以外の波形ピークを暗黙に数値化しません。字幕・話者・波形の拡張データは保持し、音声ミックスの読み込みでは従来どおり未知フィールドを取り込みません。通常の保存処理、レイアウト・組版・波形生成はまだAny禁止の対象外です。

`SubtitleProject`と形式の移行は`subtitle_project_model.py`へ分離し、既存の`subtitle_project`から再公開します。設定セクションの未知のキーとnullは維持し、辞書・null以外の形は専用の例外で拒否します。コレクション内の辞書以外の要素を除外する既存動作と、除外前の位置に基づく字幕IDも維持します。

`project_snapshots.py`と`project_recovery.py`ではJSONをobjectとして読み込み、ルートとプロジェクトの形を検証します。不正なルートはSnapshotError・RecoveryErrorとして扱い、スナップショット一覧では破損データを除外し、復旧失敗時にはジャーナルを残します。保存形式と差分はTypedDictで定義し（復旧ジャーナルは未知フィールドを保持する辞書）、revisionは既存の任意値を維持しつつ比較時に整数へ変換します。チェックサム、秘密情報・メディアパスの除去、復元時のメディア参照差し戻し、保持期間・件数・容量の制御もAny禁止の対象です。

`audio_mixer.py`では音声チャンネルの正規化・更新・有効チャンネルの選択・外部向け表示・FFmpegフィルター生成をAny禁止にしています。ミックスの固定項目とパスを含まない表示はTypedDictで定義し、チャンネルの拡張値は未検証のobjectとして保持します。読み取り用のマッピング検証とは別に、元のプロジェクトや音声ソースへIDを書き戻す箇所では同じ辞書参照を検証して更新します。更新APIはコピーを返す従来の契約を維持します。不正なコンテナはAudioMixErrorで拒否します。純粋なテストは`test_audio_mixer_models.py`へ移し、保存・再読み込みとの統合テストを既存ファイルに残しています。呼び出すログのマスキング処理自体はまだ全面的なAny禁止の対象外です。

`subtitle_layout`のtokenize・rules・scoringと`typed_cache.py`にもAny禁止を適用します。分割器はProtocolで必要な操作を定義し、BudouXとJanomeの使用APIを`typings/`の最小スタブで補います。スタブは任意APIを許可するものではありません。APIの利用範囲を広げる際は実ライブラリの仕様を確認して定義を追加し、実ライブラリとの契約テストも更新します。現在はBudouXの標準日本語パーサーと、引数なしで作成するJanome Tokenizerの標準tokenize呼び出しだけを定義しています。未導入時のフォールバックと、必須チェック時のエラーは維持します。

標準lru_cacheの型定義は関数の引数情報を失うため、`typed_cache.py`では標準ライブラリから返されたデコレーターをobjectとして受け、呼び出し可能なProtocolに絞り込みます。ParamSpecで関数の引数・戻り値とcache_clear・cache_infoの契約を保持します。任意の外部値をこの契約へ変換せず、実行時は同じ標準LRU実装と上限値を使用します。引数・戻り値の誤用が型エラーになること、実行時のヒット数・追い出し・クリアを確認しています。字幕パッカー本体と既存互換モジュールも対象に追加しています。

`subtitle_packer.py`・`subtitle_layout/packer.py`・`models.py`では、単語時刻からのページ分割・幅に基づく時刻補完・字幕イベント生成までAny禁止にしています。外部の字幕・単語データはobjectとして受け、マッピング・シーケンス・文字列を利用前に検証します。数値文字列などの変換は共通のcoerce_float/coerce_intを使い、処理中の通常のfloat値は重複変換を省きます。入力の拡張フィールドと単語の参照、分割しない場合の元セグメントの参照を保持します。単語間隔で分割する公開APIは更新可能な辞書を返し、従来の参照・更新契約を維持します。内部の分割単位と文字時刻はTypedDictで定義します。字幕イベントの文字列項目に不正な値を渡した場合はTypeErrorで拒否します。新しい境界テストもAny禁止に含め、既存の字幕出力・互換APIのテストは引き続き実行します。

進捗は診断件数だけでなく、Any禁止をCIで保証する対象が増えたかで確認します。全体移行は未完了です。

## Heavier Windows checks

The regular CI workflow also runs Windows smoke checks for GUI startup, FFmpeg media handling, Qt Multimedia playback, audio mixer operations, and installer startup.

WhisperX, PyTorch, and CUDA checks are intentionally kept in the manual Windows deep runtime workflow because they are slower and depend on runner capabilities.
