"""配布OSごとの更新パッケージ契約。未対応の形式は選択しない。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def installer_asset_name() -> str | None:
    return "SubtitleEditBay-Setup.exe" if sys.platform == "win32" else None


def installer_download_name(version: str) -> str:
    if installer_asset_name() is None:
        raise ValueError("このOSのインストーラー更新は未対応です")
    return f"SubtitleEditBay-{version.lstrip('v')}.exe"


def build_installer_command(
    project_root: Path,
    package_path: Path,
    *,
    expected_version: str,
    expected_sha256: str,
    result_path: Path,
    expected_signer_subject: str,
) -> list[str]:
    """対応OSのインストーラーへ検証情報を引き渡す。"""
    helper = project_root / "scripts" / "apply_installer_update.ps1"
    if sys.platform == "win32":
        powershell = "powershell.exe"
        return [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(helper),
            "-PackagePath",
            str(package_path),
            "-ParentPid",
            str(os.getpid()),
            "-InstallRoot",
            str(project_root),
            "-ExpectedVersion",
            expected_version,
            "-ExpectedSha256",
            expected_sha256,
            "-ExpectedSignerSubject",
            expected_signer_subject,
            "-ResultPath",
            str(result_path),
        ]
    raise ValueError("このOSのインストーラー更新は未対応です")
