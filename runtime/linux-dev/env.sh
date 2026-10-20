# Source this file in a Bash shell; CI uses the same file through BASH_ENV.
linux_repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
source "$linux_repo_root/runtime/linux-dev/toolchain.env"
linux_state_dir="$linux_repo_root/.local/linux-dev"
export VIRTUAL_ENV="$linux_repo_root/.venv"
export PATH="$linux_state_dir/ffmpeg-$FFMPEG_BUILD/bin:$linux_state_dir/node-$NODE_VERSION/bin:$linux_state_dir/uv-$UV_VERSION:$VIRTUAL_ENV/bin:$PATH"
export LD_LIBRARY_PATH="$linux_state_dir/ffmpeg-$FFMPEG_BUILD/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export UV_CACHE_DIR="$linux_state_dir/cache/uv"
export UV_PYTHON_INSTALL_DIR="$linux_state_dir/python"
export UV_SYSTEM_CERTS=true
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$linux_state_dir/cache}"
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$linux_state_dir/config}"
export XDG_DATA_HOME="${XDG_DATA_HOME:-$linux_state_dir/data}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-$linux_state_dir/run}"
export PYTHONUTF8=1
export QT_QPA_PLATFORM=offscreen
export QT_QUICK_CONTROLS_STYLE=Basic
export QT_QUICK_BACKEND=software
export QSG_RHI_BACKEND=software
export QT_FFMPEG_DECODING_HW_DEVICE_TYPES=,
export QT_FFMPEG_ENCODING_HW_DEVICE_TYPES=,
export OPENBLAS_NUM_THREADS=2
export OMP_NUM_THREADS=2
export MKL_NUM_THREADS=2
export PIP_DISABLE_PIP_VERSION_CHECK=1
mkdir -p "$linux_state_dir" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
