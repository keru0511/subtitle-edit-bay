import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class WindowsLauncherTests(unittest.TestCase):
    def test_start_uses_project_virtual_environment(self) -> None:
        launcher = (ROOT / "start.bat").read_text(encoding="utf-8")

        self.assertIn(r".venv\Scripts\python.exe", launcher)
        self.assertIn("-m src.gui", launcher)
        self.assertIn(r".local\ffmpeg_path.txt", launcher)

    def test_setup_uses_module_pip_and_winget_fallbacks(self) -> None:
        setup = (ROOT / "scripts" / "setup.ps1").read_text(encoding="utf-8")

        self.assertIn('"Python.Python.3.10"', setup)
        self.assertIn('"Gyan.FFmpeg"', setup)
        self.assertIn("-m pip install", setup)
        self.assertIn("check_runtime_dependencies", setup)

    def test_local_install_artifacts_are_ignored(self) -> None:
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn(".venv/", ignore)
        self.assertIn(".local/", ignore)

    def test_documentation_uses_launchers_and_virtual_environment(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        usage = (ROOT / "docs" / "USAGE.md").read_text(encoding="utf-8")

        self.assertIn("setup.bat", readme)
        self.assertIn("start.bat", readme)
        self.assertIn(r".\.venv\Scripts\python.exe", readme)
        self.assertIn(r".\.venv\Scripts\python.exe", usage)
        self.assertNotIn("\npython -m src.gui", readme)
        self.assertNotIn("\npython -m src.gui", usage)


if __name__ == "__main__":
    unittest.main()