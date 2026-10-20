#!/usr/bin/env bash
# CI and cloud setup must enter the same image, never the host .venv.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"
state_dir="${XDG_CACHE_HOME:-${TMPDIR:-/tmp}}/subtitle-edit-bay-linux-dev"
mkdir -p "$state_dir"
# Preserve Docker credentials/config; only Buildx's writable state lives here.
export BUILDX_CONFIG="${BUILDX_CONFIG:-$state_dir/buildx}"

environment_hash=$(
    cat .dockerignore runtime/linux-dev/Dockerfile runtime/linux-dev/apt.lock \
        runtime/linux-dev/requirements.lock \
        runtime/linux-dev/inputs.sha256 runtime/linux-dev/entrypoint.sh \
        scripts/linux_dev_environment.py | sha256sum | cut -d ' ' -f 1
)
image="subtitle-edit-bay-linux-dev:$environment_hash"
build_args=(--platform linux/amd64 --file runtime/linux-dev/Dockerfile --tag "$image")
run_args=(--rm --init --platform linux/amd64 --user "$(id -u):$(id -g)"
    --mount "type=bind,src=$repo_root,dst=/workspace" --workdir /workspace)

# Managed cloud builds/runs need the session CA in addition to proxy defaults.
# It is mounted for each command and never copied into an image layer.
if [[ -n "${CODEX_PROXY_CERT:-}" ]]; then
    ca_bundle=/etc/ssl/certs/ca-certificates.crt
    build_args+=(--secret "id=proxy_ca,src=$ca_bundle")
    run_args+=(--mount "type=bind,src=$ca_bundle,dst=/run/proxy-ca.pem,readonly"
        --env SSL_CERT_FILE=/run/proxy-ca.pem --env REQUESTS_CA_BUNDLE=/run/proxy-ca.pem
        --env PIP_CERT=/run/proxy-ca.pem)
fi

if [[ "${1:-}" == build ]] || ! docker image inspect "$image" > /dev/null 2>&1; then
    docker build "${build_args[@]}" .
fi
if [[ "${1:-}" == build ]]; then
    exit 0
fi
if [[ "${1:-}" == shell ]]; then
    shift
    run_args+=(-it)
    set -- bash "$@"
fi
docker run "${run_args[@]}" "$image" "${@:-verify}"
