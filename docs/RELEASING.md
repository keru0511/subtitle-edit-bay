# リリースガイド

この文書は、Windows向けインストーラーをGitHub Releasesで公開する担当者向けです。利用者には[最新リリース](https://github.com/keru0511/subtitle-edit-bay/releases/latest)から `SubtitleEditBay-Setup.exe` を1つダウンロードする導線を案内します。

## バージョンとタグ

公開タグにはSemantic Versioning形式の `vX.Y.Z` を使用します。

- `X`: 互換性を壊す変更
- `Y`: 後方互換のある機能追加
- `Z`: 後方互換のある修正

例は `v0.1.0`、`v0.1.1`、`v1.0.0` です。タグから先頭の `v` を除いたバージョンは、リリース対象コミットの `VERSION`（先頭の `v` は省略可）と一致させてください。公開済みのタグを削除・付け替えしないでください。公開後に問題が見つかった場合は、修正してパッチ番号を上げた新しいタグを作成します。

## タグ作成前チェック

1. リリース対象を `main` へ反映し、リモートと同期します。
2. `VERSION` が作成予定のタグと一致することを確認します。
3. 作業ツリーに意図しない変更がないことを確認します。
4. 自動テストを実行します。
5. README、利用ガイド、設定ガイドに利用者向けの変更が反映されていることを確認します。
6. APIキー、トークン、入力素材、出力動画、個人設定が含まれていないことを確認します。
7. 可能なら、まっさらなWindows環境でインストールと起動を確認します。

~~~powershell
git switch main
git pull --ff-only
git status --short
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
~~~

`git status --short` に出力がある場合は、その変更がリリースへ含めるものか確認してから進めます。

## リリースの作成

リリース対象のコミットに注釈付きタグを作成し、GitHubへpushします。次は `v0.1.0` を公開する例です。

~~~powershell
git tag -a v0.1.0 -m "Release v0.1.0"
git push origin v0.1.0
~~~

厳密な `vX.Y.Z` タグのpushを契機に `.github/workflows/release.yml` が次を自動実行します。

1. タグ形式、タグの存在、タグと `VERSION` の一致を検証する
2. Python 3.10環境で自動テストを実行する
3. Inno SetupでWindowsインストーラーを構築する
4. SHA-256チェックサム、manifestのバージョンと必須ファイル契約を検証する
5. 同じタグのGitHub Releaseを作成し、リリースノートを生成する
6. `SubtitleEditBay-Setup.exe`、`SubtitleEditBay-Setup.exe.sha256`、`SubtitleEditBay-Setup.exe.manifest.json` を添付する

一時的な失敗はGitHub Actionsからジョブを再実行できます。ワークフローを手動実行する場合は、既に存在するタグを `vX.Y.Z` 形式で指定します。コード修正が必要になった場合はタグを移動せず、修正後にパッチ番号を上げた新しいタグを作成してください。

## リリース後の検証

1. GitHub Actionsの `Release Windows installer` が成功していることを確認します。
2. 対象タグのReleaseが作成されていることを確認します。
3. Assetsに `SubtitleEditBay-Setup.exe`、チェックサム、manifestが表示されることを確認します。
4. [直接ダウンロードURL](https://github.com/keru0511/subtitle-edit-bay/releases/latest/download/SubtitleEditBay-Setup.exe)からファイルを取得できることを確認します。
5. ダウンロードしたファイルをWindows上で実行し、インストール、初回セットアップ、GUI起動を確認します。
6. 可能なら短い検証素材で、文字起こしから字幕編集、動画書き出しまでを確認します。

初版の `SubtitleEditBay-Setup.exe` はアプリのソースを配置するインストーラーで、初回セットアップ時にPython、FFmpeg、WhisperXなどの実行環境をネットワーク経由で導入する場合があります。オフラインインストーラーではありません。ネットワーク障害、空き容量不足、セキュリティソフトによる停止も含めて検証してください。

公開後の利用者向けURLは次の2つです。

- リリースページ: <https://github.com/keru0511/subtitle-edit-bay/releases/latest>
- EXE直接取得: <https://github.com/keru0511/subtitle-edit-bay/releases/latest/download/SubtitleEditBay-Setup.exe>

## ZIP版について

ZIP展開後に `setup.bat` と `start.bat` を実行する方法は、開発用途およびインストーラーが使えない場合の代替経路として維持します。一般利用者への第一案内には `SubtitleEditBay-Setup.exe` を使用してください。
