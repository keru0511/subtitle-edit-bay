from __future__ import annotations

import sys
from pathlib import Path

from .codex_runtime import classify_distribution


VERSION_FILE_NAME = "VERSION"
DEVELOPMENT_VERSION = "development"


def normalize_version(value: str) -> str:
    version = value.strip()
    if not version:
        return DEVELOPMENT_VERSION
    return version if version.startswith("v") else f"v{version}"


def resolve_application_version(project_root: Path | None = None) -> str:
    root = project_root or Path(__file__).resolve().parent.parent
    version_file = Path(root) / VERSION_FILE_NAME
    if not version_file.is_file():
        return DEVELOPMENT_VERSION
    return normalize_version(version_file.read_text(encoding="utf-8").strip())


def resolve_application_info(project_root: Path | None = None) -> dict[str, str]:
    root = Path(project_root or Path(__file__).resolve().parent.parent)
    if project_root is None:
        root = root.resolve()
    return {
        "version": resolve_application_version(root),
        "distribution": classify_distribution(root),
        "executablePath": str(Path(sys.executable).resolve()),
        "applicationPath": str(root),
    }


def build_application_info_payload(project_root: Path | None = None) -> str:
    payload = resolve_application_info(project_root)
    return "\n".join(
        [
            f"Version: {payload['version']}",
            f"Distribution: {payload['distribution']}",
            f"Executable: {payload['executablePath']}",
            f"Application path: {payload['applicationPath']}",
        ]
    )
