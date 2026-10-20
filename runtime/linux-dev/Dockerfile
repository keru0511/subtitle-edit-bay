# The architecture and base digest are part of the environment contract.
FROM --platform=linux/amd64 python:3.10.22-slim-trixie@sha256:7b6bd969dda3ecfca0655524e338b57a8b8cd692a9362e3d55b5a3ef49b07256

COPY runtime/linux-dev/apt.lock /opt/linux-dev/apt.lock
RUN --mount=type=secret,id=proxy_ca \
    export DEBIAN_FRONTEND=noninteractive; \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; \
    else export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt; fi; \
    apt-get -o Acquire::https::CaInfo="$SSL_CERT_FILE" update \
    && xargs apt-get -o Acquire::https::CaInfo="$SSL_CERT_FILE" install --yes --no-install-recommends < /opt/linux-dev/apt.lock \
    && rm -rf /var/lib/apt/lists/*

COPY runtime/linux-dev/requirements.lock /opt/linux-dev/requirements.lock
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    awk '/^[^[:space:]]/ { keep = $0 ~ /^(torch|torchaudio|torchvision)==/; if (keep) count++ } \
        keep; END { if (count != 3) exit 1 }' /opt/linux-dev/requirements.lock > /tmp/torch.lock \
    && python -m pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        --require-hashes --only-binary=:all: --no-deps -r /tmp/torch.lock \
    && python -m pip install --no-cache-dir --index-url https://pypi.org/simple \
        --require-hashes --only-binary=:all: -r /opt/linux-dev/requirements.lock \
    && python -m pip check && rm /tmp/torch.lock

COPY --chmod=0644 runtime/linux-dev/apt.lock runtime/linux-dev/requirements.lock \
    runtime/linux-dev/inputs.sha256 runtime/linux-dev/entrypoint.sh scripts/linux_dev_environment.py /opt/linux-dev/
ENV PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    QT_QPA_PLATFORM=offscreen QT_QUICK_CONTROLS_STYLE=Basic \
    QT_QUICK_BACKEND=software QSG_RHI_BACKEND=software \
    OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 \
    HOME=/tmp/subtitle-edit-bay-home \
    XDG_CACHE_HOME=/tmp/subtitle-edit-bay-home/cache \
    XDG_CONFIG_HOME=/tmp/subtitle-edit-bay-home/config \
    XDG_DATA_HOME=/tmp/subtitle-edit-bay-home/data \
    XDG_RUNTIME_DIR=/tmp/subtitle-edit-bay-runtime \
    HF_HOME=/tmp/subtitle-edit-bay-home/cache/huggingface \
    MPLCONFIGDIR=/tmp/subtitle-edit-bay-home/cache/matplotlib
ENTRYPOINT ["bash", "/opt/linux-dev/entrypoint.sh"]
CMD ["verify"]
