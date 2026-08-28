# GUI更新フロー

Installer版の更新は、GUIでの確認、パッケージdownload、検証、GUI終了後の適用、自動再起動を分離して実行する。ZIP版とGit checkoutは既存の `scripts/update.ps1` / `update.bat` をfallbackとして残す。

## 状態

1. GitHub Releaseから現在version、最新version、release note、installer asset、サイズ、checksum manifestを取得する
2. `アップデート` はインストールディレクトリ外のLocalAppData更新キャッシュへパッケージをdownloadする
3. download中はbyte単位の進捗を表示し、キャンセル時は `.partial` を削除する
4. 完了後にSHA-256、サイズ、PE header、release version、package layoutを検証する
5. 検証済みパッケージは保持し、`再起動して更新` または `後で` を選べる
6. helperへpackage path、expected version/hash、parent PID、install root、restart path、result pathを渡す
7. helperはGUI終了とfile lock解放を待ち、Inno Setupを非表示で実行する
8. 必須ファイルとVERSIONを検証し、成功時だけ新ランチャーを自動起動する。ネイティブランチャーがない配布では `scripts/launch.ps1` をWindows PowerShellで起動する

download失敗とapply失敗は別のエラーとして表示する。更新前にdirty project、render、文字起こし、Codex turnが残っている場合は開始しない。ユーザーデータ、プロジェクト、`.gui`、`.local`、media、speaker colorsはinstallerの更新対象外として保持する。

## Package manifest

Releaseには `SubtitleEditBay-Setup.exe`、同名 `.sha256`、同名 `.manifest.json` を添付する。manifestはschema、installer package type、app version、asset name、SHA-256、必須ファイルを含む。GUIはchecksum取得・検証に失敗したpackageを適用しない。

## 失敗と再起動loop

helperはatomicなresult JSONをLocalAppDataへ保存する。Inno Setupの終了コード、VERSION不一致、必須ファイル欠落、launcher欠落はrollback結果として記録し、旧versionの情報とlog pathを保持する。同じpending resultを無条件に再適用せず、結果確認後に履歴として残す。

