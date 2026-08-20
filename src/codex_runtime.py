from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


@dataclass(frozen=True)
class CodexRuntimeInfo:
    available: bool
    executable: str = ""
    version: str = ""
    distribution: str = "unknown"
    error: str = ""

    @property
    def command(self) -> list[str]:
        if not self.available:
            return []
        return [self.executable, "app-server", "--listen", "stdio://"]


def classify_distribution(workspace_root: str | Path) -> str:
    root = Path(workspace_root)
    if (root / "SubtitleEditBay.exe").is_file() or (root / "installer").is_dir() and not (root / ".git").exists():
        return "installer"
    if (root / ".git").exists():
        return "git"
    return "zip"


def detect_codex(
    workspace_root: str | Path,
    *,
    environment: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> CodexRuntimeInfo:
    env = dict(os.environ if environment is None else environment)
    candidates: list[str] = []
    if env.get("CODEX_EXECUTABLE"):
        candidates.append(env["CODEX_EXECUTABLE"])
    root = Path(workspace_root)
    candidates.extend(
        str(path)
        for path in (
            root / ".venv" / "Scripts" / "codex.exe",
            root / ".venv" / "bin" / "codex",
            root / "codex.exe",
        )
        if path.is_file()
    )
    found = which("codex")
    if found:
        candidates.append(found)
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            completed = run(
                [candidate, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            continue
        version = (completed.stdout or completed.stderr or "").strip().splitlines()
        if completed.returncode == 0:
            return CodexRuntimeInfo(
                available=True,
                executable=candidate,
                version=version[0] if version else "unknown",
                distribution=classify_distribution(root),
            )
    return CodexRuntimeInfo(
        available=False,
        distribution=classify_distribution(root),
        error="Codex CLIが見つからないか、対応バージョンを確認できません",
    )


def redact_codex_diagnostic(value: object) -> str:
    text = str(value)
    text = re.sub(
        r"(?i)(api[_-]?key|token|password|secret|authorization)(\s*[:=]\s*)([^\s,;]+)",
        r"\1\2[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\)[^\s\"']+", "<local-path>", text)
    return text


def build_codex_diagnostic(info: CodexRuntimeInfo) -> dict[str, str | bool]:
    return {
        "available": info.available,
        "version": redact_codex_diagnostic(info.version),
        "distribution": info.distribution,
        "executable": redact_codex_diagnostic(info.executable),
        "error": redact_codex_diagnostic(info.error),
    }

