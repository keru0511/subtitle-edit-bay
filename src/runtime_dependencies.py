from __future__ import annotations

import importlib.util
import shutil
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RuntimeDependencyStatus:
    ffmpeg: bool
    ffprobe: bool
    whisperx: bool
    cuda: bool = False

    @property
    def ready(self) -> bool:
        return self.ffmpeg and self.ffprobe and self.whisperx

    def missing(self, require_whisperx: bool = True) -> list[str]:
        missing: list[str] = []
        if not self.ffmpeg:
            missing.append("ffmpeg")
        if not self.ffprobe:
            missing.append("ffprobe")
        if require_whisperx and not self.whisperx:
            missing.append("whisperx")
        return missing

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["ready"] = self.ready
        payload["missing"] = self.missing()
        return payload


def check_runtime_dependencies() -> RuntimeDependencyStatus:
    return RuntimeDependencyStatus(
        ffmpeg=shutil.which("ffmpeg") is not None,
        ffprobe=shutil.which("ffprobe") is not None,
        whisperx=importlib.util.find_spec("whisperx") is not None,
        cuda=_torch_cuda_available(),
    )


def _torch_cuda_available() -> bool:
    if importlib.util.find_spec("torch") is None:
        return False
    try:
        import torch
    except (ImportError, OSError):
        return False
    return bool(torch.cuda.is_available())


def format_dependency_error(
    status: RuntimeDependencyStatus,
    require_whisperx: bool = True,
    device: str | None = None,
) -> str:
    missing = status.missing(require_whisperx=require_whisperx)
    cuda_missing = require_whisperx and device == "cuda" and not status.cuda
    if not missing and not cuda_missing:
        return ""

    hints: list[str] = []
    if "ffmpeg" in missing or "ffprobe" in missing:
        hints.append("Install FFmpeg and add both ffmpeg and ffprobe to PATH.")
    if "whisperx" in missing:
        hints.append("Install WhisperX in this Python environment: python -m pip install whisperx")
    if cuda_missing:
        hints.append("CUDA was selected but this PyTorch build cannot use it. Run setup.bat again or select CPU.")
    missing_label = ", ".join(missing) if missing else "CUDA-enabled PyTorch"
    return f"Missing runtime dependencies: {missing_label}. {' '.join(hints)}"
