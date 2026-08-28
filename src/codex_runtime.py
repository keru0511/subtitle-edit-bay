from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


CODEX_MIN_VERSION = (0, 1, 0)
CODEX_MAX_VERSION = (1, 0, 0)
_CODEX_VERSION_PATTERN = re.compile(
    r"(?i)\bcodex(?:[-_ ]cli)?\b[^0-9]*v?(\d+)\.(\d+)(?:\.(\d+))?"
)
_CODEX_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)([^\s,;&}\]]+)"),
    re.compile(
        r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret|authorization)"
        r"[\"']?\s*[:=]\s*[\"']?)([^\"'\s,;&}\]]+)"
    ),
)
_CODEX_WINDOWS_PATH_PATTERN = re.compile(
    r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\)[^\r\n\"'<>|?*]*?\.[A-Za-z0-9]{1,12}(?![\w])"
)
_CODEX_WINDOWS_PATH_TOKEN_PATTERN = re.compile(
    r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\)[^\s\"']+"
)
_CODEX_UNIX_PATH_PATTERN = re.compile(
    r"(?<![\w])/(?:[^\r\n\"'<>|?*]+/)*[^\r\n\"'<>|?*]*?\.[A-Za-z0-9]{1,12}(?![\w])"
)
_CODEX_UNIX_PATH_TOKEN_PATTERN = re.compile(r"(?<![\w])/(?:[^\s\"']+/)+[^\s\"']+")


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
    if (root / ".git").exists():
        return "git"
    if any(
        (root / executable_name).is_file()
        for executable_name in ("SubtitleEditBayLauncher.exe", "SubtitleEditBay.exe")
    ):
        return "installer"
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
    candidates.extend(_codex_desktop_executables(env))
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
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            continue
        output_lines = [
            line.strip()
            for output in (completed.stdout, completed.stderr)
            for line in (output or "").splitlines()
            if line.strip()
        ]
        version_line = next(
            (line for line in output_lines if _parse_codex_version(line) is not None),
            "",
        )
        parsed_version = _parse_codex_version(version_line)
        if completed.returncode == 0 and parsed_version is not None and _is_supported_codex_version(parsed_version):
            return CodexRuntimeInfo(
                available=True,
                executable=candidate,
                version=version_line,
                distribution=classify_distribution(root),
            )
    return CodexRuntimeInfo(
        available=False,
        distribution=classify_distribution(root),
        error="Codex CLIが見つからないか、対応バージョンを確認できません",
    )


def _codex_desktop_executables(environment: Mapping[str, str]) -> list[str]:
    """Return verified-later Codex Desktop CLI candidates newest first.

    Codex Desktop adds its versioned bin directory to child processes, but it
    does not add that directory to the persistent Windows user PATH. Apps
    started from Explorer therefore need this narrow fallback discovery path.
    Every returned executable still goes through the normal identity and
    supported-version probe in :func:`detect_codex`.
    """

    local_app_data = str(environment.get("LOCALAPPDATA", "")).strip()
    if not local_app_data:
        return []
    bin_root = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
    try:
        executables = [
            child / "codex.exe"
            for child in bin_root.iterdir()
            if child.is_dir() and (child / "codex.exe").is_file()
        ]
    except OSError:
        return []

    def modified_time(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return -1

    executables.sort(
        key=lambda path: (modified_time(path), str(path).casefold()),
        reverse=True,
    )
    return [str(path) for path in executables]


def redact_codex_diagnostic(value: object) -> str:
    text = str(value)
    for pattern in _CODEX_SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    text = _CODEX_WINDOWS_PATH_PATTERN.sub("<local-path>", text)
    text = _CODEX_WINDOWS_PATH_TOKEN_PATTERN.sub("<local-path>", text)
    text = _CODEX_UNIX_PATH_PATTERN.sub("<local-path>", text)
    text = _CODEX_UNIX_PATH_TOKEN_PATTERN.sub("<local-path>", text)
    return text


def _parse_codex_version(value: str) -> tuple[int, int, int] | None:
    match = _CODEX_VERSION_PATTERN.search(value)
    if match is None:
        return None
    return tuple(int(group or 0) for group in match.groups())  # type: ignore[return-value]


def _is_supported_codex_version(version: tuple[int, int, int]) -> bool:
    return CODEX_MIN_VERSION <= version < CODEX_MAX_VERSION


def build_codex_diagnostic(info: CodexRuntimeInfo) -> dict[str, str | bool]:
    return {
        "available": info.available,
        "version": redact_codex_diagnostic(info.version),
        "distribution": info.distribution,
        "executable": redact_codex_diagnostic(info.executable),
        "error": redact_codex_diagnostic(info.error),
    }

