from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class LauncherContractTests(unittest.TestCase):
    def test_native_launcher_resolves_module_directory_and_hides_console(self) -> None:
        source = (ROOT / "launcher" / "SubtitleEditBayLauncher.c").read_text(encoding="utf-8")

        self.assertIn("GetModuleFileNameW", source)
        self.assertNotIn("GetCurrentDirectory", source)
        self.assertIn(r"scripts\\launch.ps1", source)
        self.assertIn("CreateProcessW", source)
        self.assertIn("CREATE_NO_WINDOW", source)
        self.assertIn("STARTF_USESHOWWINDOW", source)
        self.assertIn("SW_HIDE", source)
        build_script = (ROOT / "scripts" / "build_launcher.ps1").read_text(encoding="utf-8")
        self.assertIn("/SUBSYSTEM:WINDOWS", build_script)

    def test_installer_has_native_launcher_and_power_shell_fallback(self) -> None:
        installer = (ROOT / "installer" / "SubtitleEditBay.iss").read_text(encoding="utf-8")

        self.assertIn("SubtitleEditBayLauncher.exe", installer)
        self.assertIn("skipifsourcedoesntexist", installer)
        self.assertIn("WindowsPowerShell", installer)


if __name__ == "__main__":
    unittest.main()
