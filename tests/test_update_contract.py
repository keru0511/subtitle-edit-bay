from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class UpdateContractTests(unittest.TestCase):
    def test_helper_has_parent_wait_hash_version_result_and_restart_contract(self) -> None:
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
            "rollback",
        ):
            self.assertIn(marker, helper)

    def test_gui_update_dialog_has_download_progress_cancel_and_restart_actions(self) -> None:
        qml = (ROOT / "src" / "ui" / "screens" / "MainWorkflowScreen.qml").read_text(encoding="utf-8")

        for marker in (
            "updateDownloadProgressBar",
            "cancelUpdateDownloadButton",
            "再起動して更新",
            "updateDownloadActive",
        ):
            self.assertIn(marker, qml)


if __name__ == "__main__":
    unittest.main()
