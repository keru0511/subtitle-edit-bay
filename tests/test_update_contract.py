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

if __name__ == "__main__":
    unittest.main()
