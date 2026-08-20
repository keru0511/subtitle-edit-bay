from __future__ import annotations

import os
import sys
import tempfile
import unittest
import venv
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QProcess

from src.qprocess_launcher import prepare_qprocess_launch


class QProcessLauncherTest(unittest.TestCase):
    def test_non_windows_launch_is_unchanged(self) -> None:
        if os.name == "nt":
            self.skipTest("Non-Windows behavior")

        working_directory = Path.cwd().resolve()
        command = [sys.executable, "-c", "print('ok')"]

        launch = prepare_qprocess_launch(command, working_directory)

        self.assertEqual(launch.program, command[0])
        self.assertEqual(launch.arguments, tuple(command[1:]))
        self.assertEqual(launch.working_directory, str(working_directory))

    @unittest.skipUnless(os.name == "nt", "Windows QProcess integration test")
    def test_windows_launches_unicode_venv_workspace_and_media_path(self) -> None:
        application = QCoreApplication.instance() or QCoreApplication([])
        self.assertIsNotNone(application)
        with tempfile.TemporaryDirectory(prefix="edit-bay-qprocess-") as temporary:
            root = Path(temporary)
            workspace = root / "日本語ワークスペース"
            workspace.mkdir()
            environment = workspace / ".venv"
            venv.EnvBuilder(with_pip=False).create(environment)
            python = environment / "Scripts" / "python.exe"
            media = workspace / "素材_日本語.txt"
            media.write_text("bound", encoding="utf-8")
            alias_base = root / "ascii-aliases"
            command = [
                str(python),
                "-c",
                (
                    "from pathlib import Path; import sys; "
                    "print(Path(sys.argv[1]).read_text(encoding='utf-8'))"
                ),
                str(media),
            ]

            launch = prepare_qprocess_launch(
                command,
                workspace,
                alias_base=alias_base,
            )
            process = QProcess()
            process.setWorkingDirectory(launch.working_directory)
            process.start(launch.program, list(launch.arguments))

            self.assertTrue(process.waitForStarted(10_000), process.errorString())
            self.assertTrue(process.waitForFinished(30_000), process.errorString())
            output = bytes(process.readAllStandardOutput()).decode("utf-8").strip()
            error = bytes(process.readAllStandardError()).decode("utf-8").strip()
            self.assertEqual(process.exitCode(), 0, error)
            self.assertEqual(output, "bound")
            self.assertTrue(launch.program.isascii())
            self.assertTrue(launch.working_directory.isascii())


if __name__ == "__main__":
    unittest.main()
