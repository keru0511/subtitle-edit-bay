from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from src.gemini_runtime import (
    build_gemini_diagnostic,
    classify_distribution,
    detect_gemini,
    redact_gemini_diagnostic,
)


class GeminiRuntimeTests(unittest.TestCase):
    def test_detection_uses_official_acp_command_and_no_shell(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, stdout="gemini-cli 0.59.0\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            info = detect_gemini(
                temp_dir,
                environment={"GEMINI_EXECUTABLE": "C:/Tools/gemini.exe"},
                which=lambda _name: "C:/Path/gemini.exe",
                run=run,
            )

        self.assertTrue(info.available)
        self.assertEqual(info.executable, "C:/Tools/gemini.exe")
        self.assertEqual(info.command, ["C:/Tools/gemini.exe", "--acp"])
        self.assertNotIn("--experimental-acp", info.command)
        self.assertNotIn("--yolo", info.command)
        self.assertFalse(calls[0][1]["shell"])
        self.assertEqual(calls[0][1]["encoding"], "utf-8")

    def test_detection_accepts_plain_version_from_a_gemini_named_binary(self) -> None:
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="0.59.0\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            info = detect_gemini(
                temp_dir,
                environment={"GEMINI_EXECUTABLE": "C:/Tools/gemini.exe"},
                which=lambda _name: None,
                run=run,
            )
        self.assertTrue(info.available)

    def test_missing_and_unrelated_executables_are_not_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = detect_gemini(
                temp_dir,
                environment={},
                which=lambda _name: None,
                run=lambda *args, **kwargs: None,
            )
        self.assertFalse(missing.available)

        def run_unrelated(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="other-tool 9.9.9\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            unrelated = detect_gemini(
                temp_dir,
                environment={"GEMINI_EXECUTABLE": "C:/Tools/other.exe"},
                which=lambda _name: None,
                run=run_unrelated,
            )
        self.assertFalse(unrelated.available)

    def test_distribution_and_diagnostic_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".git").mkdir()
            self.assertEqual(classify_distribution(root), "git")
            (root / ".git").rmdir()
            self.assertEqual(classify_distribution(root), "zip")
            (root / "SubtitleEditBay.exe").write_bytes(b"launcher")
            self.assertEqual(classify_distribution(root), "installer")

        redacted = redact_gemini_diagnostic(
            r'api_key="gemini-secret" C:\Users\name\gemini.json /home/name/gemini.json'
        )
        self.assertNotIn("gemini-secret", redacted)
        self.assertNotIn("C:\\Users\\name", redacted)
        self.assertNotIn("/home/name", redacted)
        info = detect_gemini(".", environment={}, which=lambda _name: None, run=lambda *a, **k: None)
        self.assertNotIn("C:\\", str(build_gemini_diagnostic(info)))


if __name__ == "__main__":
    unittest.main()
