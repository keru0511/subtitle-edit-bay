#!/usr/bin/env bash
# デフォルトで GUI、引数指定時は同じ環境で任意の開発コマンドを起動する。
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
[[ -x .venv/bin/python ]] || { echo '先に bash scripts/setup-linux.sh を実行してください。' >&2; exit 1; }
export PATH="$repo_root/.venv/bin:$PATH"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$repo_root/.local/cache}"
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$repo_root/.local/config}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-$repo_root/.local/runtime}"
mkdir -p "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME"
if [[ ! -d "$XDG_RUNTIME_DIR" ]]; then
    mkdir -m 700 -p "$XDG_RUNTIME_DIR"
fi
export PYTHONUTF8=1
if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
    export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
    export QT_QUICK_CONTROLS_STYLE="${QT_QUICK_CONTROLS_STYLE:-Basic}"
    export QT_QUICK_BACKEND="${QT_QUICK_BACKEND:-software}"
    export QSG_RHI_BACKEND="${QSG_RHI_BACKEND:-software}"
fi
if (( $# )); then
    exec "$@"
fi
exec python -m src.gui
