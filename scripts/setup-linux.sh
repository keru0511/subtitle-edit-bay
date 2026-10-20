#!/usr/bin/env bash
# Linux CI と同じ軽量開発環境を用意する。WhisperX の実推論は含まない。
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$repo_root/.local/uv-cache}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$repo_root/.local/python}"
command -v uv >/dev/null || { echo 'uv をインストールしてください。' >&2; exit 1; }
for tool in ffmpeg ffprobe; do
    command -v "$tool" >/dev/null || { echo "$tool をインストールしてください。" >&2; exit 1; }
done
if [[ ! -x .venv/bin/python ]]; then
    uv venv --python 3.10 .venv
fi
.venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 10), "Python 3.10 の .venv が必要です"'
# Qt 6.12 のヘッドレス起動クラッシュと mypy 2.4 の診断変更を回避する。
uv pip install --python .venv/bin/python -r requirements.txt -r requirements-dev.txt \
    'PySide6==6.11.2' 'mypy==2.3.1'
uv pip check --python .venv/bin/python
