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

設定・話者色・workspace一覧・監査記録は1つの移行transactionとして反映します。途中の保存に
失敗した場合は、上書き前の内容を含めて開始前の状態へ戻します。複数の旧workspaceを移行した場合、
登録済みworkspaceを維持して追記し、同じworkspaceの再実行では重複を作りません。
移行元とインストール先はjunctionを含むリンク先の最終パスで比較し、同じ実体ならファイル配置や
runtime構築を始める前に拒否します。

## capability補正

- CUDAを実際に利用できない場合、`device=cuda`は`cpu/int8`へ補正します。
- NVENC encode probeが失敗した場合、`h264_nvenc`は`libx264`へ補正します。
- 現行schemaに存在しない古いkeyは取り込みません。
- 値の型が現行schemaと異なる場合、移行全体を失敗させます。
- schemaはアプリの設定loaderと共有し、既定値JSONの掲載有無や`null`値から型を推測しません。

補正は新しいInstaller側だけに適用し、旧設定を書き換えません。

## cache policy

| cache | 共有/移行 |
| --- | --- |
| pip download cache | pipの標準user cacheを共有可能。`.venv`は共有しない |
| PyTorch wheel cache | installer用runtimeの解決結果が同じ場合だけ標準cacheから再利用 |
| Hugging Face / WhisperX model cache | library標準のuser cacheを共有可能 |
| audio preview cache | project/runtime依存のためコピーしない |
| transcript cache | project fingerprint依存のためコピーしない |

環境変数でcache rootを変更していた旧環境について、移行処理はその値をInstallerへコピーしません。
cacheを再利用できても、実行runtimeは必ずInstaller用`.venv`から起動します。

## cleanup

移行記録の`cleanup_blockers`が空でなくても異常ではありません。project、素材、出力、設定などが
旧workspaceに残っていることを示します。必要なデータを別途バックアップし、参照先を変更するまで
旧フォルダー全体を削除しないでください。

`reclaimable_venv_bytes`は旧`.venv`を削除した場合に解放できる概算容量です。移行処理は
`.venv`を自動削除しません。Installer版が正常起動し、必要なproject/media/outputが保全されて
いることを確認してから、旧`.venv`だけを手動削除できます。Installer版は旧`.venv`を参照しないため、
削除後も起動できます。

## 手動実行

初回セットアップ後にもInstallerのPythonから再実行できます。

```powershell
.\.venv\Scripts\python.exe -m src.installer_migration `
  --source "C:\path\to\old-subtitle-edit-bay" `
  --destination "$PWD"
```

CUDA/NVENCを検証済みの場合だけ、それぞれ`--cuda`、`--nvenc`を付けます。通常は
`scripts/setup.ps1 -MigrationSource ...`を使い、setupがprobe結果を渡す経路を推奨します。

## 関連Issueと段階導入

- #257: Installer → setup → launcherのWindows E2Eに、この移行経路のE2Eを接続します。
- #260: Installer更新transactionでは、本移行記録とuser dataをrollback対象外として保持します。
- #261: release runtime lock導入後は、fresh構築する`.venv`の入力をそのlockへ切り替えます。
- #262: launcher/installer署名は配布binaryの契約であり、移行するuser dataには署名情報を混在させません。

この変更は移行境界とデータtransactionを先に固定します。runtime lock、署名、実GPU環境を必要とする
release E2Eは上記Issueの完了後に同じ契約へ接続します。
