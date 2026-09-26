"""Gemini CLI runtime discovery for the stdio ACP provider.

The application does not install Gemini CLI or manage its credentials.  This
module only verifies that an executable supplied by the user is the official
``gemini`` command and can report a supported version.  The ACP command itself
is intentionally fixed to ``gemini --acp``; callers cannot opt into the
deprecated experimental or automatic-approval modes through this boundary.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from .application_logging import redact_text
from .process_utils import VersionProbeRun, hidden_subprocess_kwargs


GEMINI_MIN_VERSION = (0, 1, 0)
_GEMINI_VERSION_PATTERN = re.compile(
    r"(?i)\bgemini(?:[-_ ]cli)?\b[^0-9]*v?(\d+)\.(\d+)(?:\.(\d+))?"
)
_PLAIN_VERSION_PATTERN = re.compile(r"(?<![0-9])v?(\d+)\.(\d+)(?:\.(\d+))(?![0-9])")
_GEMINI_IDENTITY_PATTERN = re.compile(r"(?i)\bgemini(?:[-_ ]cli)?\b")


@dataclass(frozen=True)
class GeminiRuntimeInfo:
    """Verified Gemini CLI metadata used to start the ACP subprocess."""

    available: bool
    executable: str = ""
    version: str = ""
    distribution: str = "unknown"
    error: str = ""

    @property
    def command(self) -> list[str]:
        """Return the only supported ACP command line."""

        if not self.available:
            return []
        return [self.executable, "--acp"]


def classify_distribution(workspace_root: str | Path) -> str:
    """Classify the current application distribution for diagnostics."""

    root = Path(workspace_root)
    if (root / ".git").exists():
        return "git"
    if any(
        (root / executable_name).is_file()
        for executable_name in ("SubtitleEditBayLauncher.exe", "SubtitleEditBay.exe")
    ):
        return "installer"
    return "zip"


def detect_gemini(
    workspace_root: str | Path,
    *,
    environment: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    run: VersionProbeRun = subprocess.run,
) -> GeminiRuntimeInfo:
    """Find and verify an installed Gemini CLI without starting ACP.

    The explicit ``GEMINI_EXECUTABLE`` override is useful for portable and
    Windows installations.  Every candidate is still checked with
    ``--version`` and must identify itself as Gemini; an arbitrary executable
    with a semver-looking output is not accepted.
    """

    env = dict(os.environ if environment is None else environment)
    candidates: list[str] = []
    for key in ("GEMINI_EXECUTABLE", "GEMINI_CLI_EXECUTABLE"):
        if env.get(key):
            candidates.append(env[key])

    root = Path(workspace_root)
    candidates.extend(
        str(path)
        for path in (
            root / ".venv" / "Scripts" / "gemini.exe",
            root / ".venv" / "Scripts" / "gemini.cmd",
            root / ".venv" / "bin" / "gemini",
            root / "gemini.exe",
            root / "gemini.cmd",
            root / "gemini",
        )
        if path.is_file()
    )
    found = which("gemini")
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
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
                shell=False,
                creationflags=hidden_subprocess_kwargs().get("creationflags", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            continue

        output_lines = [
            line.strip()
            for output in (completed.stdout, completed.stderr)
            for line in (output or "").splitlines()
            if line.strip()
        ]
        version_line = next(
            (
                line
                for line in output_lines
                if _parse_gemini_version(line, candidate) is not None
            ),
            "",
        )
        parsed_version = _parse_gemini_version(version_line, candidate)
        if (
            completed.returncode == 0
            and parsed_version is not None
            and _is_supported_gemini_version(parsed_version)
        ):
            return GeminiRuntimeInfo(
                available=True,
                executable=candidate,
                version=version_line,
                distribution=classify_distribution(root),
            )

    return GeminiRuntimeInfo(
        available=False,
        distribution=classify_distribution(root),
        error="Gemini CLIが見つからないか、対応バージョンを確認できません",
    )


def redact_gemini_diagnostic(value: object) -> str:
    """Redact credentials and local paths before exposing diagnostics."""

    return redact_text(value, paths=True)


def build_gemini_diagnostic(info: GeminiRuntimeInfo) -> dict[str, str | bool]:
    """Return a safe diagnostic mapping without exposing local paths."""

    return {
        "available": info.available,
        "version": redact_gemini_diagnostic(info.version),
        "distribution": info.distribution,
        "executable": redact_gemini_diagnostic(info.executable),
        "error": redact_gemini_diagnostic(info.error),
    }


def _parse_gemini_version(value: str, candidate: str) -> tuple[int, int, int] | None:
    match = _GEMINI_VERSION_PATTERN.search(value)
    if match is None:
        candidate_name = Path(candidate).name.casefold()
        if not re.fullmatch(r"gemini(?:\.exe|\.cmd)?", candidate_name):
            return None
        match = _PLAIN_VERSION_PATTERN.search(value)
    if match is None:
        return None
    if not _GEMINI_IDENTITY_PATTERN.search(value) and not re.fullmatch(
        r"gemini(?:\.exe|\.cmd)?", Path(candidate).name.casefold()
    ):
        return None
    return tuple(int(group or 0) for group in match.groups())  # type: ignore[return-value]


def _is_supported_gemini_version(version: tuple[int, int, int]) -> bool:
    return version >= GEMINI_MIN_VERSION


__all__ = [
    "GEMINI_MIN_VERSION",
    "GeminiRuntimeInfo",
    "build_gemini_diagnostic",
    "classify_distribution",
    "detect_gemini",
    "redact_gemini_diagnostic",
]
