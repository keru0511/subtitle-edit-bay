# BAT/ZIP版からInstaller版への移行

Installerの初回セットアップで「BAT/ZIP版の設定とworkspace参照を引き継ぐ」を選び、以前の
Subtitle Edit Bayフォルダーを指定すると、安全に引き継げるユーザーデータだけを移行します。
旧フォルダーは変更も削除もされません。

## 移行の境界

| 種類 | 方針 |
| --- | --- |
| `.venv`、Python package、PyTorch/CUDA runtime | 直接再利用せず、Installer rootへfresh構築 |
| OSのPython 3.10、FFmpeg/ffprobe | 対応版を検出・検証して再利用 |
| `.gui/runtime_config.json` | schemaと現在のCUDA/NVENC capabilityを検証して移行 |
| `assets/speaker_colors.json` | JSON/schemaを検証して移行 |
| dictionary / preset / `.gui/settings.json` | JSON/schemaを検証したuser設定だけを移行 |
| project、素材、出力 | コピー・移動せず、旧workspaceへの参照だけを登録 |
| secret/token類 | 移行しない。secretらしいkeyを含む設定はfail closed |

移行先に設定または話者色が既にある場合、既定では上書きしません。明示的な上書きは
CLIの`--overwrite`でのみ行えます。移行結果は
`.local/migration/migration-<UTC時刻>.json`へ記録されます。記録には移行元・移行先、
capabilityに合わせて補正した設定、旧workspace内の参照中データ、旧`.venv`の概算容量が
含まれます。

移行元、選択した項目はインストール時に`.local/migration/pending-request.json`へ保存されます。
初回setupを延期した場合やruntime構築が途中で失敗した場合も、「初回セットアップ・修復」から
同じ要求を再利用します。この保留要求は移行が成功した後にだけ削除されます。
保留要求はInstallerがUTF-8 JSONとして保存するため、日本語を含む旧workspaceパスも再試行時に
同じ値で読み込まれます。

## 初回起動の確認画面

Installerの初回起動では、fresh構築したInstaller runtimeから
`src.installer_migration_entrypoint`を呼び出し、`pending-plan.json`へinventory、設定の差分、
workspace参照、cleanup候補、診断を保存します。既存のsetup進捗画面とは別の確認画面で内容を
確認してから「移行を適用」できます。既存ファイルの上書きは既定で行わず、cleanupもこの画面では
実行しません。確認画面には「上書き: 行わない（既定）」と「cleanup: 自動実行しない」を明示し、
上書きとcleanupは候補ごとの個別確認なしには適用されません。

「キャンセル（後で再試行）」を選んだ場合は、新しいInstaller runtimeと旧workspaceの両方を残し、
`pending-request.json`を削除しません。次回の初回セットアップ・修復で同じsourceと選択を読み直します。
適用後は`latest-result.json`とUTC時刻付きのmigration recordへ結果と診断を書き出します。

この画面は既存のWindows PowerShell setup UIを利用する薄いreview層です。移行の判断・検証・書込は
#360〜#362の`legacy_migration` inventory/settings/cleanup plan/resultが正本であり、旧runtimeを
import、起動、再利用しません。CIやサイレント実行では`SUBTITLE_EDIT_BAY_SUPPRESS_MESSAGES=1`
または`SUBTITLE_EDIT_BAY_MIGRATION_AUTO_APPROVE=1`を指定して既存の明示的なInstaller task選択を
再利用します。

runtime config・話者色・dictionary・preset・user設定は、dry-runで差分とskip理由を確認してから
1つの移行transactionとして反映します。既存Installer側データの上書きには、`overwrite`と明示的な
`confirm`の両方が必要です。途中の保存に失敗した場合は、上書き前の内容を含めて開始前の状態へ戻します。
複数の旧workspaceを移行した場合、
登録済みworkspaceを維持して追記し、同じworkspaceの再実行では重複を作りません。
移行元とインストール先はjunctionを含むリンク先の最終パスで比較し、同じ実体ならファイル配置や
runtime構築を始める前に拒否します。

## capability補正

- CUDAを実際に利用できない場合、`device=cuda`は`cpu/int8`へ補正します。
- NVENC encode probeが失敗した場合、`h264_nvenc`は`libx264`へ補正します。
- `build_settings_migration_plan()`へcapability probe結果を渡さない場合も安全側に倒し、
  `cuda=False, nvenc=False`として上記の補正を適用します。未確認のGPU capabilityを
  移行処理が推測して有効化することはありません。
- 現行schemaに存在しない古いkeyは取り込みません。
- 値の型が現行schemaと異なる場合、移行全体を失敗させます。
- schemaはアプリの設定loaderと共有し、既定値JSONの掲載有無や`null`値から型を推測しません。

補正は新しいInstaller側だけに適用し、旧設定を書き換えません。

## cache policy

| cache | 共有/移行 |
| --- | --- |
| pip download cache | 旧workspace rootからは検出・移行せず、外部user cache capabilityとして明示 |
| PyTorch wheel cache | 旧workspace rootからは検出・移行せず、runtime構築と共有しない |
| Hugging Face / WhisperX model cache | 旧workspace rootからは検出・移行せず、外部user cache capabilityとして明示 |
| audio preview cache | project/runtime依存のためコピーしない |
| transcript cache | project fingerprint依存のためコピーしない |

環境変数でcache rootを変更していた旧環境についても、その値やcache内容をInstallerへコピーしません。
cacheを再利用できる場合でも、このsliceは共有を行わず、実行runtimeは必ずInstaller用`.venv`から起動します。

### cache/cleanup plan

`build_cache_cleanup_plan(inventory)` は、上記のinventoryを再利用したread-onlyの構造化planです。
planの各entryは `kind`、`state`、`size_bytes`、`reclaimable_bytes`、`protection_reason`、
`referenced_data`、`cleanup_allowed` を持ち、値やsecretは含めません。状態の意味は次のとおりです。

| kind | 既定のstate | cleanupの扱い |
| --- | --- | --- |
| pip download | `rebuild`（lock/fingerprintを検証済みなら明示的に`reuse`） | migration成功後にplanで指定したpathだけ削除可能 |
| PyTorch wheel | `rebuild`（lock/fingerprintを検証済みなら明示的に`reuse`） | 同上 |
| Hugging Face / WhisperX model | `rebuild` | runtime依存のため既定では再利用しない |
| audio preview | `rebuild` | project/media参照が解決するまで再利用しない |
| transcript metadata | `rebuild` | project/runtime fingerprint不明なら再利用しない |
| 旧`.venv` | migration未完了は`preserve`、成功後は`removable_after_success` | 自動削除しない |
| 旧app/source | migration未完了は`preserve`、成功後は`removable_after_success` | 旧rootそのものは削除対象にしない |
| project / media / output | `preserve` | 常に自動削除不可 |

pip/PyTorch/Hugging Faceのuser cacheは旧rootの外にあるため、inventoryでは`not_discoverable`です。
共有またはcleanupを検討する場合も、呼び出し側がcandidate IDに対応する絶対pathを
`CacheCleanupOptions(cache_paths=...)`で明示する必要があります。`reusable_cache_ids`を指定しない
限り、pathが存在するだけでは`reuse`になりません。project/runtimeに依存するcacheを、stale判定なしに
再利用する経路はありません。

cleanupを実行する場合は、`migration_completed=True`、plan作成時または
`apply_cache_cleanup(plan, confirm=True)`での明示確認、plan内candidate IDの選択が必要です。
selectionが空の場合は、確認済みのplanであっても安全なno-opになり、候補を自動的に全選択しません。
削除する場合は、`selected=("legacy:.venv",)`のようにcandidate IDを1件以上明示してください。
source rootそのもの、project/media/output、symlink/junction、root外や`..`を含むpathは拒否します。
全対象の安全性を先に検査してから削除するため、危険なpathが混ざった場合に先行対象だけを消すことも
ありません。cleanupは移行transactionのrollback対象ではないpost-success操作なので、Installerの
runtime構築と必要なデータの保全を確認した後だけ実行してください。

## cleanup

移行記録の`cleanup_blockers`が空でなくても異常ではありません。project、素材、出力、設定などが
旧workspaceに残っていることを示します。必要なデータを別途バックアップし、参照先を変更するまで
旧フォルダー全体を削除しないでください。

`reclaimable_venv_bytes`は旧`.venv`を削除した場合に解放できる概算容量です。移行処理は
`.venv`を自動削除しません。Installer版が正常起動し、必要なproject/media/outputが保全されて
いることを確認してから、旧`.venv`だけを手動削除できます。Installer版は旧`.venv`を参照しないため、
削除後も起動できます。

従来の`reclaimable_venv_bytes`は移行結果に記録される旧`.venv`単体の概算です。より広い候補を
確認する場合は、`build_cache_cleanup_plan()`の`reclaimable_bytes`を使います。この値は
`cleanup_allowed=True`かつ安全に観測できたplan内pathだけを合計し、project/media/outputや
未発見の外部cacheを含めません。planを保存して後で実行する場合も、pathを再探索して増やさず、
保存時に確認したcandidate IDとpathだけをcleanup対象にします。

## 手動実行

初回セットアップ後にもInstallerのPythonから再実行できます。

```powershell
.\.venv\Scripts\python.exe -m src.installer_migration `
  --source "C:\path\to\old-subtitle-edit-bay" `
  --destination "$PWD"
```

CUDA/NVENCを検証済みの場合だけ、それぞれ`--cuda`、`--nvenc`を付けます。通常は
`scripts/setup.ps1 -MigrationSource ...`を使い、setupがprobe結果を渡す経路を推奨します。

コードから確認する場合は、`src.legacy_migration.build_settings_migration_plan()`で
inventoryを入力にしたdry-runを作り、`apply_settings_migration()`へ渡します。plan/resultは
値を含めず、移行対象のkey、調整内容、skip理由だけをJSON化します。旧`.venv`、project、
素材、出力、cache候補はこのAPIの書き込み対象になりません。
`capabilities`は信頼できるCUDA/NVENC probe結果を渡す場合だけ指定し、省略時は
`RuntimeCapabilities(cuda=False, nvenc=False)`として保守的に補正します。書き込み中に失敗した
場合は、通常の一時ファイルとrollback用一時ファイルを残さず、開始時のdestination treeへ戻します。

## 関連Issueと段階導入

- #257: Installer → setup → launcherのWindows E2Eに、この移行経路のE2Eを接続します。
- #260: Installer更新transactionでは、本移行記録とuser dataをrollback対象外として保持します。
- #261: release runtime lock導入後は、fresh構築する`.venv`の入力をそのlockへ切り替えます。
- #262: launcher/installer署名は配布binaryの契約であり、移行するuser dataには署名情報を混在させません。

この変更は移行境界とデータtransactionを先に固定します。runtime lock、署名、実GPU環境を必要とする
release E2Eは上記Issueの完了後に同じ契約へ接続します。
