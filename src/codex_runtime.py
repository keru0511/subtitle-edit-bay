from __future__ import annotations

import re
from pathlib import Path

from .application_logging import redact_text


_CODEX_WINDOWS_PATH_PATTERN = re.compile(
    r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\)[^\r\n\"'<>|?*]*?\.[A-Za-z0-9]{1,12}(?![\w])"
)
_CODEX_WINDOWS_PATH_TOKEN_PATTERN = re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\)[^\s\"']+")
_CODEX_UNIX_PATH_PATTERN = re.compile(r"(?<![\w])/(?:[^\r\n\"'<>|?*]+/)*[^\r\n\"'<>|?*]*?\.[A-Za-z0-9]{1,12}(?![\w])")
_CODEX_UNIX_PATH_TOKEN_PATTERN = re.compile(r"(?<![\w])/(?:[^\s\"']+/)+[^\s\"']+")


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
    text = redact_text(value)
    text = _CODEX_WINDOWS_PATH_PATTERN.sub("<local-path>", text)
    text = _CODEX_WINDOWS_PATH_TOKEN_PATTERN.sub("<local-path>", text)
    text = _CODEX_UNIX_PATH_PATTERN.sub("<local-path>", text)
    text = _CODEX_UNIX_PATH_TOKEN_PATTERN.sub("<local-path>", text)
    return text
