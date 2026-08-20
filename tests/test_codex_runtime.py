from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from src.codex_runtime import (
    build_codex_diagnostic,
    classify_distribution,
    detect_codex,
    redact_codex_diagnostic,
)


class CodexRuntimeTests(unittest.TestCase):
    def test_detection_prefers_explicit_executable_and_uses_no_shell(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, stdout="codex 1.2.3\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            info = detect_codex(
                temp_dir,
                environment={"CODEX_EXECUTABLE": "C:/Tools/codex.exe"},
                which=lambda _name: "C:/Path/codex.exe",
                run=run,
            )

        self.assertTrue(info.available)
        self.assertEqual(info.executable, "C:/Tools/codex.exe")
        self.assertEqual(info.command[-2:], ["--listen", "stdio://"])
        self.assertFalse(calls[0][1]["shell"])

    def test_distribution_is_distinguished_for_git_zip_and_installer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".git").mkdir()
            self.assertEqual(classify_distribution(root), "git")
            (root / ".git").rmdir()
            self.assertEqual(classify_distribution(root), "zip")
            (root / "SubtitleEditBay.exe").write_bytes(b"launcher")
            self.assertEqual(classify_distribution(root), "installer")

    def test_diagnostic_redacts_secret_and_local_path(self) -> None:
        redacted = redact_codex_diagnostic(
            "token=secret C:\\Users\\name\\project.json Bearer private"
        )
        self.assertNotIn("secret", redacted)
        self.assertNotIn("private", redacted)
        self.assertIn("<local-path>", redacted)
        info = detect_codex(".", environment={}, which=lambda _name: None, run=lambda *a, **k: None)
        self.assertNotIn("C:\\", str(build_codex_diagnostic(info)))


if __name__ == "__main__":
    unittest.main()
