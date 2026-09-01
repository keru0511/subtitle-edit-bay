from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_helper_has_parent_wait_hash_version_result_and_restart_contract():
    helper = (ROOT / "scripts" / "apply_installer_update.ps1").read_text(encoding="utf-8")
    for marker in (
        "ParentPid",
        "Get-FileHash",
        "ExpectedVersion",
        "Write-UpdateResult",
        "Start-Process",
        "WindowStyle Hidden",
        "New-RecoverySnapshot",
        "Restore-RecoverySnapshot",
        "rollback_restored",
        "rollback_failed",
        "Resolve-RestartCommand",
        "scripts\\launch.ps1",
        "powershell.exe",
    ):
        assert marker in helper
    assert "rollback" in helper


def test_gui_update_dialog_has_download_progress_cancel_and_restart_actions():
    qml = (ROOT / "src" / "ui" / "screens" / "MainWorkflowScreen.qml").read_text(encoding="utf-8")
    for marker in ("updateDownloadProgressBar", "cancelUpdateDownloadButton", "再起動して更新", "updateDownloadActive"):
        assert marker in qml
