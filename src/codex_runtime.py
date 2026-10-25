from __future__ import annotations

from pathlib import Path

from .application_logging import redact_text


def classify_distribution(workspace_root: str | Path) -> str:
    root = Path(workspace_root)
    if (root / ".git").exists():
        return "git"
    if any(
        (root / executable_name).is_file() for executable_name in ("SubtitleEditBayLauncher.exe", "SubtitleEditBay.exe")
    ):
        return "installer"
    return "zip"


def redact_codex_diagnostic(value: object) -> str:
    return redact_text(value, paths=True)
