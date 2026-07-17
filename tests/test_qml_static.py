from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


class QmlStaticTests(unittest.TestCase):
    def test_main_qml_passes_qmllint_without_warnings(self) -> None:
        executable_name = "pyside6-qmllint.exe" if os.name == "nt" else "pyside6-qmllint"
        bundled = Path(sys.executable).with_name(executable_name)
        executable = str(bundled) if bundled.is_file() else shutil.which(executable_name)
        self.assertTrue(executable, "pyside6-qmllint is required with PySide6")

        qml_path = Path(__file__).resolve().parents[1] / "src" / "ui" / "Main.qml"
        result = subprocess.run(
            [str(executable), str(qml_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

        output = (result.stdout + result.stderr).strip()
        self.assertEqual(result.returncode, 0, output)
        self.assertNotIn("Warning:", output)


if __name__ == "__main__":
    unittest.main()
