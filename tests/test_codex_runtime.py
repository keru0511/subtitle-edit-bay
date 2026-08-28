from __future__ import annotations

import os
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
            return subprocess.CompletedProcess(command, 0, stdout="codex 0.27.1\n", stderr="")

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
        self.assertEqual(calls[0][1]["encoding"], "utf-8")
        self.assertEqual(calls[0][1]["errors"], "replace")

    def test_detection_finds_versioned_codex_desktop_without_path(self) -> None:
        calls: list[str] = []

        def run(command, **_kwargs):
            executable = str(command[0])
            calls.append(executable)
            if "new-build" in executable:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="not-codex 9.9.9\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="codex-cli 0.150.0-alpha.12.2\n",
                stderr="",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bin_root = root / "local-app-data" / "OpenAI" / "Codex" / "bin"
            older = bin_root / "old-build" / "codex.exe"
            newest = bin_root / "new-build" / "codex.exe"
            older.parent.mkdir(parents=True)
            newest.parent.mkdir(parents=True)
            older.write_bytes(b"older")
            newest.write_bytes(b"newer")
            os.utime(older, (100, 100))
            os.utime(newest, (200, 200))

            info = detect_codex(
                root,
                environment={"LOCALAPPDATA": str(root / "local-app-data"), "PATH": ""},
                which=lambda _name: None,
                run=run,
            )

        self.assertTrue(info.available)
        self.assertEqual(info.executable, str(older))
        self.assertEqual(calls, [str(newest), str(older)])

    def test_detection_finds_version_line_after_stderr_warning(self) -> None:
        def run(command, **_kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="",
                stderr="warning before version\ncodex-cli 0.150.0-alpha.12.2\n",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            info = detect_codex(
                temp_dir,
                environment={"CODEX_EXECUTABLE": "C:/Tools/codex.exe"},
                which=lambda _name: None,
                run=run,
            )

        self.assertTrue(info.available)
        self.assertEqual(info.version, "codex-cli 0.150.0-alpha.12.2")

    def test_desktop_detection_continues_after_newest_candidate_times_out(self) -> None:
        def run(command, **_kwargs):
            if "new-build" in str(command[0]):
                raise subprocess.TimeoutExpired(command, 5)
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="codex-cli 0.150.0-alpha.12.2\n",
                stderr="",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bin_root = root / "local-app-data" / "OpenAI" / "Codex" / "bin"
            older = bin_root / "old-build" / "codex.exe"
            newest = bin_root / "new-build" / "codex.exe"
            older.parent.mkdir(parents=True)
            newest.parent.mkdir(parents=True)
            older.write_bytes(b"older")
            newest.write_bytes(b"newer")
            os.utime(older, (100, 100))
            os.utime(newest, (200, 200))

            info = detect_codex(
                root,
                environment={"LOCALAPPDATA": str(root / "local-app-data"), "PATH": ""},
                which=lambda _name: None,
                run=run,
            )

        self.assertTrue(info.available)
        self.assertEqual(info.executable, str(older))

    def test_detection_requires_codex_identity_and_supported_version(self) -> None:
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="other-tool 1.2.3\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            unsupported = detect_codex(
                temp_dir,
                environment={"CODEX_EXECUTABLE": "C:/Tools/other.exe"},
                which=lambda _name: None,
                run=run,
            )
        self.assertFalse(unsupported.available)

        def supported_run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="Codex CLI 0.27.1\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            supported = detect_codex(
                temp_dir,
                environment={"CODEX_EXECUTABLE": "C:/Tools/codex.exe"},
                which=lambda _name: None,
                run=supported_run,
            )
        self.assertTrue(supported.available)

    def test_distribution_is_distinguished_for_git_zip_and_installer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".git").mkdir()
            self.assertEqual(classify_distribution(root), "git")
            (root / ".git").rmdir()
            self.assertEqual(classify_distribution(root), "zip")
            (root / "SubtitleEditBay.exe").write_bytes(b"launcher")
            self.assertEqual(classify_distribution(root), "installer")

            (root / "SubtitleEditBay.exe").unlink()
            (root / "SubtitleEditBayLauncher.exe").write_bytes(b"launcher")
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

    def test_diagnostic_redacts_json_credentials_and_paths_with_spaces(self) -> None:
        value = (
            '{"access_token":"json-secret","authorization":"Bearer json-bearer"} '
            r'C:\Users\Alice\My Projects\codex.json '
            "/home/Alice/My Projects/codex.json"
        )
        redacted = redact_codex_diagnostic(value)
        for secret in ("json-secret", "json-bearer"):
            self.assertNotIn(secret, redacted)
        self.assertNotIn("My Projects", redacted)
        self.assertNotIn("C:\\Users\\Alice", redacted)
        self.assertNotIn("/home/Alice", redacted)

    def test_diagnostic_redacts_basic_authorization_and_quoted_password(self) -> None:
        redacted = redact_codex_diagnostic(
            'Authorization: Basic Zm9vOmJhcg== password="two words"'
        )

        self.assertNotIn("Zm9vOmJhcg==", redacted)
        self.assertNotIn("two words", redacted)


if __name__ == "__main__":
    unittest.main()
