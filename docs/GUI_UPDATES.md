# GUI更新フロー

Installer版の更新は、GUIでの確認、パッケージdownload、検証、GUI終了後の適用、自動再起動を分離して実行する。ZIP版とGit checkoutは既存の `scripts/update.ps1` / `update.bat` をfallbackとして残す。

## 状態

1. GitHub Releaseから現在version、最新version、release note、installer asset、サイズ、checksum manifestを取得する
2. `アップデート` はインストールディレクトリ外のLocalAppData更新キャッシュへパッケージをdownloadする
3. download中はbyte単位の進捗を表示し、キャンセル時は `.partial` を削除する
4. 完了後にSHA-256、サイズ、PE header、release version、package layoutを検証する
5. 検証済みパッケージは保持し、`再起動して更新` または `後で` を選べる
6. helperへpackage path、expected version/hash、parent PID、install root、result pathを渡す。再起動先は更新前に固定しない
7. helperはGUIとinstall root配下から起動された親launcher chainの終了、app/runtimeのfile lock解放を待つ
8. app filesと `.venv`、runtime設定のrollback pointを作り、Inno Setupへ `/DIR=<install root>` と `/LOG=<installer log>` を明示して実行する
9. installer後は新しい `scripts/setup.ps1` で空の `.venv` を構築し、Python主要import、dependency、FFmpeg/FFprobe、設定済みCUDAを独立したvalidatorで確認する
10. 全検証の成功後だけrollback pointを破棄し、更新後のinstall rootからlauncherを解決して起動する。ネイティブランチャーがない配布では `scripts/launch.ps1` をWindows PowerShellで起動する

download失敗とapply失敗は別のエラーとして表示する。更新前にdirty project、render、文字起こし、Codex turnが残っている場合は開始しない。ユーザーデータ、プロジェクト、`.gui`、`.local`、media、speaker colorsはinstallerの更新対象外として保持する。

## Package manifest

Releaseには `SubtitleEditBay-Setup.exe`、同名 `.sha256`、同名 `.manifest.json` を添付する。manifestはschema、installer package type、app version、asset name、SHA-256、必須ファイルを含む。GUIはchecksum取得・検証に失敗したpackageを適用しない。

## 失敗と再起動loop

helperはatomicなresult JSONをLocalAppDataへ保存する。結果にはcorrelation ID、helper、Inno Setup、runtime setup/validationの個別log pathを記録する。Inno Setupの終了コード、VERSION不一致、runtime不成立、launcher欠落ではapp/runtime/runtime設定をrollbackし、復元後のinstall rootから旧launcherを解決して再起動する。rollback自体が失敗した場合はrecovery pointを保持する。同じpending resultを無条件に再適用せず、結果確認後に履歴として残す。

依存関係を完全に固定したruntime manifestは #261、native launcherを全配布で必須化してPowerShell fallbackを削除する契約は #257、BAT/ZIP版からinstaller版への移行は #263 が所有する。本フローはこれらが未導入でも、installer asset内のsetup/validatorを必須ファイルとして検証して原子的に適用する。
