# Linux CI・クラウド共通環境

通常CI・リリースのLinux検証・クラウド開発は、同じPython依存lockとセットアップを使います。
Dockerは不要です。対象はDebian/Ubuntu系のLinux x86_64です。

## セットアップと検証

Bash、curl、tar、xz、sha256sumが必要です。Qtの基本ライブラリが未導入の場合だけ、
aptを実行できる権限（rootまたはsudo）を使います。

```bash
bash scripts/setup_linux.sh
source runtime/linux-dev/env.sh
python scripts/run_ci_tests.py --validate
python scripts/check_quality.py --lint-only
python scripts/check_quality.py --format-only --paths scripts/check_quality.py
for platform in linux win32 darwin; do
  python scripts/check_quality.py --type-only --type-platform "$platform"
  python scripts/check_no_any.py --platform "$platform"
done
python scripts/check_unittest_discovery.py
python scripts/run_ci_tests.py --group portable-unit
python scripts/run_ci_tests.py --group qt-gui
RUN_FFMPEG_SMOKE=1 python scripts/run_ci_tests.py --group ffmpeg-runtime
bash scripts/check_gui_startup.sh
```

`setup_linux.sh` は `.venv` を共有lockに同期します。既存の余分なPythonパッケージは除去されます。
ツール・Python本体・キャッシュはGit管理外の `.local/linux-dev/` に置きます。
GUI設定や動画・音声は変更しません。モデルのダウンロードは行いません。
CIは `BASH_ENV` で同じ `env.sh` を読み、各検証ステップで同じ参照先とQt設定を使います。

## バージョンの正本

- `toolchain.env`: Python 3.10.22、uv 0.12.23、Node.js 20.19.2、FFmpeg/ffprobe 7.1.3系。
  ツールは固定URLとSHA-256で導入します。FFmpegは共有ライブラリも同梱した同じビルドを使います。
  TorchCodec 0.7の対応範囲に合わせて7系を選び、ASRが読み込むビルドとCLIの一致も確認します。
- `requirements.in` / `requirements.lock`: 開発・Qt・WhisperX・CPU版PyTorchを含む全Python依存。
  lockのハッシュを必須とし、ソースビルドを行いません。`--torch-backend cpu` により
  torch・torchaudio・torchvision・tritonをlock生成時と同じPyTorch CPU indexから取得します。
- OSのバージョンは固定しません。QtとFFmpegの基本ライブラリ
  （libegl1・libpulse0・libva-drm2・libva-x11-2）は必要に応じてaptで導入します。
  Qtのメディア処理はCPUに固定し、ヘッドレス環境でGPUを探索しません。
  Windows配布用のランタイム契約・lock・互換性検証は従来どおりです。

初期のPython依存は、成功したPR #439のCIで使用したBudouX 0.9.3、Qt 6.11.2、
mypy 2.3.1、Ruff 0.16.9に合わせています。

## 更新

Python依存は `requirements.in` を変更し、セットアップ済みのLinuxで再生成します。
必要なパッケージだけを更新する場合は `--upgrade-package <name>` を追加します。

```bash
source runtime/linux-dev/env.sh
uv pip compile --python-version "$PYTHON_VERSION" --python-platform x86_64-manylinux_2_34 \
  --torch-backend cpu --generate-hashes --no-emit-index-url --no-annotate --no-header \
  runtime/linux-dev/requirements.in -o runtime/linux-dev/requirements.lock
bash scripts/setup_linux.sh
```

ツール更新時は `toolchain.env` のバージョン・取得元・SHA-256を一緒に変更し、上記の検証を実行します。
FFmpegはBtbNの月末保存版（配布元の保持期間は2年）を指定します。`latest` や日次版は使用しません。
取得不能・ハッシュ不一致の場合は失敗させ、別バージョンに自動で切り替えません。

## クラウド設定への適用

Git管理外の保存済みinstall_scriptを次の呼び出しに置き換えます。

```bash
bash /workspace/subtitle-edit-bay/scripts/setup_linux.sh
```

start_skillには「`/workspace/subtitle-edit-bay` を使用し、Python・GUI・テストの前に
`source runtime/linux-dev/env.sh` を実行する」と記載します。
旧 `.cloud-setup` でのlock生成・インストール・`env.sh` のsourceを置き換えてください。
PRのマージだけでは保存済みのクラウド設定は更新されません。
