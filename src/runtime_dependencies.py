from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RuntimeDependencyStatus:
    ffmpeg: bool
    ffprobe: bool
    whisperx: bool
    cuda: bool = False
    nvenc: bool = False

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


def check_runtime_dependencies(*, probe_nvenc: bool = False) -> RuntimeDependencyStatus:
    ffmpeg_path = shutil.which("ffmpeg")
    return RuntimeDependencyStatus(
        ffmpeg=ffmpeg_path is not None,
        ffprobe=shutil.which("ffprobe") is not None,
        whisperx=importlib.util.find_spec("whisperx") is not None,
        cuda=_torch_cuda_available(),
        nvenc=probe_nvenc and _ffmpeg_nvenc_available(ffmpeg_path),
    )


def runtime_diagnostic_info() -> dict[str, object]:
    info: dict[str, object] = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "ffmpeg": _ffmpeg_version(shutil.which("ffmpeg")),
        "pytorch": "not installed",
        "pytorch_cuda_build": "none",
        "cuda_available": False,
    }
    if importlib.util.find_spec("torch") is None:
        return info
    try:
        import torch
    except (ImportError, OSError):
        return info

    info["pytorch"] = str(torch.__version__)
    info["pytorch_cuda_build"] = str(torch.version.cuda or "none")
    cuda_available = bool(torch.cuda.is_available())
    info["cuda_available"] = cuda_available
    if cuda_available:
        try:
            info["cuda_device"] = str(torch.cuda.get_device_name(0))
        except (OSError, RuntimeError):
            pass
    return info


def _ffmpeg_version(ffmpeg_path: str | None) -> str:
    if not ffmpeg_path:
        return "not found"
    try:
        result = subprocess.run(
            [ffmpeg_path, "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    lines = (result.stdout or result.stderr or "").strip().splitlines()
    return lines[0] if result.returncode == 0 and lines else "unavailable"


def _ffmpeg_nvenc_available(ffmpeg_path: str | None) -> bool:
    if not ffmpeg_path:
        return False
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=256x144:r=1",
        "-frames:v",
        "1",
        "-an",
        "-c:v",
        "h264_nvenc",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


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
