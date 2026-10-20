#!/usr/bin/env bash
# The only installation path used by Linux CI, release tests, and cloud setup.
set -euo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"
[[ "$(uname -s)/$(uname -m)" == Linux/x86_64 ]] || {
    echo "This setup supports Linux x86_64." >&2
    exit 1
}
source runtime/linux-dev/env.sh

# OS libraries remain host-managed. Python and executable tools are pinned below.
if [[ ! -f /usr/lib/x86_64-linux-gnu/libEGL.so.1 || ! -f /usr/lib/x86_64-linux-gnu/libpulse.so.0 ]]; then
    elevate=()
    if (( EUID != 0 )); then elevate=(sudo); fi
    "${elevate[@]}" apt-get update -qq
    "${elevate[@]}" apt-get install --yes --no-install-recommends libegl1 libpulse0
fi

install_archive() (
    url=$1 checksum=$2 destination=$3
    [[ ! -f "$destination/.installed" ]] || exit 0
    temporary=$(mktemp -d "$linux_state_dir/download.XXXXXX")
    trap 'rm -rf -- "$temporary"' EXIT
    curl --fail --location --silent --show-error --retry 3 "$url" -o "$temporary/archive"
    printf '%s  %s\n' "$checksum" "$temporary/archive" | sha256sum --check --status || {
        echo "SHA-256 mismatch: $url" >&2
        exit 1
    }
    mkdir "$temporary/unpacked"
    tar -xf "$temporary/archive" --strip-components=1 -C "$temporary/unpacked"
    touch "$temporary/unpacked/.installed"
    mv "$temporary/unpacked" "$destination"
)

install_archive "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-unknown-linux-gnu.tar.gz" \
    "$UV_SHA256" "$linux_state_dir/uv-$UV_VERSION"
install_archive "https://nodejs.org/dist/v$NODE_VERSION/node-v$NODE_VERSION-linux-x64.tar.xz" \
    "$NODE_SHA256" "$linux_state_dir/node-$NODE_VERSION"
install_archive "https://github.com/BtbN/FFmpeg-Builds/releases/download/$FFMPEG_BUILD/$FFMPEG_ARCHIVE" \
    "$FFMPEG_SHA256" "$linux_state_dir/ffmpeg-$FFMPEG_BUILD"

uv venv --allow-existing --python "$PYTHON_VERSION" "$VIRTUAL_ENV"
uv pip sync --python "$VIRTUAL_ENV/bin/python" --torch-backend cpu \
    --require-hashes --only-binary=:all: --strict runtime/linux-dev/requirements.lock
python -m pip check
python - "$(ffmpeg -version | awk 'NR == 1 {print $3}')" <<'PY'
import sys

import scripts.run_ci_tests
import whisperx.asr
import whisperx.alignment
from torchcodec._core import get_ffmpeg_library_versions
from PySide6 import QtCore

asr_ffmpeg = get_ffmpeg_library_versions()["ffmpeg_version"]
assert asr_ffmpeg == sys.argv[1], ("ASR and CLI use different FFmpeg builds", asr_ffmpeg, sys.argv[1])
print("Repository scripts, ASR, TorchCodec, and Qt imports passed.")
print("ASR FFmpeg:", asr_ffmpeg)
PY
python --version
uv --version
node --version
ffmpeg -version | sed -n '1p'
ffprobe -version | sed -n '1p'
echo 'Ready: source runtime/linux-dev/env.sh'
