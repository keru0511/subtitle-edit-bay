# Linux CI・クラウド共通環境

Linuxの通常CI、リリース準備のLinux検証、クラウド開発は、すべて
`bash scripts/linux_dev.sh` から同じ `linux/amd64` コンテナへ入ります。
ホストのPython、FFmpeg、`.venv`、Windows用lockは使用しません。
Windows固有のCI・配布環境は、従来のWindows用ランタイム契約で検証します。

## 固定するもの

- Python 3.10.22を含むベースイメージをdigestで固定します。
- `apt.lock` は、ベースと追加依存を含む全OSパッケージのバージョンを固定します。
- `requirements.lock` は、開発ツール、Qt、WhisperX、CPU版PyTorchとその推移的依存を
  SHA-256付きで固定します。インストール時の依存解決やソースビルドは行いません。
- `entrypoint.sh` とDockerfileで、offscreen/softwareのQt設定とスレッド数を共有します。

起動時にPython・アーキテクチャ・全Python/OSパッケージの実バージョンを検証し、
依存宣言のチェックサムも確認します。追加パッケージ、古いlock、異なるバージョンがあれば
処理を失敗させます。WhisperXとリポジトリの `scripts` も同じプロセスでimportします。
モデルのダウンロードや実音声の精度検証は行いません。

## 起動・検証

Dockerが使えるLinuxホストで、リポジトリルートから実行します。
イメージは環境定義のハッシュで識別し、未作成の場合だけ自動ビルドします。
ソースコードは `/workspace` にマウントし、実行ユーザーはホストと同じUID/GIDです。

```bash
bash runtime/linux-dev/cloud-install.sh   # 作成と実環境の整合性確認
bash scripts/linux_dev.sh quality         # CIと同じlint・3プラットフォームの型/Any検査
bash scripts/linux_dev.sh test            # discovery→単体→Qt→FFmpeg→実GUI起動
bash scripts/linux_dev.sh startup         # Main.qmlを読み込み、結果を確認して終了
bash scripts/linux_dev.sh python -m src.gui
bash scripts/linux_dev.sh python -m unittest tests.test_linux_dev_environment -v
bash scripts/linux_dev.sh shell           # 共通環境内の対話シェル
```

`test` は各グループを別のPythonプロセスで実行します。FFmpegの実処理を有効化し、
GUI起動は毎回新しい結果ファイルとタイムアウトで確認します。
音声出力デバイスのない環境ではPulseAudio等の警告が出ることがあります。
起動結果と終了コードで判定し、古い結果ファイルを成功の根拠にしません。

クラウドの環境設定に保存するインストールスクリプトは、次の呼び出しだけにします。
従来の `.cloud-setup` での独自解決・インストールや `env.sh` のsourceを置き換えます。

```bash
bash /workspace/subtitle-edit-bay/runtime/linux-dev/cloud-install.sh
```

起動スキルには「`/workspace/subtitle-edit-bay` を使用し、Python・テスト・GUIの実行は
必ず `bash scripts/linux_dev.sh ...` を通す。ホストの `.venv` や旧 `.cloud-setup/env.sh`
を使用しない」と記載します。環境設定の保存・公開はPRのマージとは別の操作です。
既存のGUI設定や動画・音声はセットアップから書き換えません。

管理されたクラウドではDockerの既存proxy設定を使用し、セッションのCAを
ビルド時のsecretと実行時の読み取り専用マウントで渡します。認証情報はイメージに保存しません。

## 依存更新

Linux用のバージョンは `requirements.in` で宣言します。最初の共通化では、PR #439の
成功したCIで使用したBudouX 0.9.3、PySide6 6.11.2、mypy 2.3.1、Ruff 0.16.9を採用しました。
Windows用lockをLinux用制約に変換する経路はありません。

Python 3.10.22 / uv 0.12.23を用意したLinuxで、リポジトリルートから再生成します。
既存lockのバージョンを維持する場合は `--upgrade` を付けません。

```bash
uv pip compile --python-version 3.10.22 --python-platform x86_64-manylinux_2_40 \
  --torch-backend cpu --generate-hashes --no-emit-index-url --no-annotate --no-header \
  runtime/linux-dev/requirements.in -o runtime/linux-dev/requirements.lock
sha256sum requirements.txt requirements-dev.txt requirements-type-stubs.txt \
  runtime/linux-dev/requirements.in > runtime/linux-dev/inputs.sha256
bash scripts/linux_dev.sh build
bash scripts/linux_dev.sh quality
bash scripts/linux_dev.sh test
```

OSパッケージを更新するときは、Dockerfileに固定したベースから新しい一時コンテナを作り、
`apt-get update` と `apt-get install --yes --no-install-recommends ffmpeg libegl1 libpulse0 git`
を実行した後の `dpkg-query -W -f='${binary:Package}=${Version}\n'` を `apt.lock` に保存します。
ベースdigest・Pythonバージョンを変える場合は `scripts/linux_dev_environment.py` の検証条件も
同じPRで更新してください。固定バージョンが配信元から消えた場合も、自動で最新版へ切り替えず
明示的なlock更新とCI検証を行います。
