from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from scripts.asr_ci_runtime import cache_key, prepare_runtime, runtime_context
from tests.typed_case import TypedTestCase


class AsrCiRuntimeTests(TypedTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "runtime").mkdir()
        contract: dict[str, object] = {"python": {"pip_version": "26.2.1"}}
        (self.root / "runtime/runtime-contract.json").write_text(json.dumps(contract))
        self.lock = self.root / "runtime/requirements-windows-cpu.lock"
        self.lock.write_text("fixed-lock")
        self.venv = self.root / ".asr-venv"
        self.manifest = self.root / "manifest.json"

    def test_image_date_does_not_invalidate_cache(self) -> None:
        old_image: dict[str, str] = {"ImageOS": "win25", "ImageVersion": "old"}
        new_image: dict[str, str] = {"ImageOS": "win25", "ImageVersion": "new"}
        with patch.dict(os.environ, old_image):
            before = cache_key(self.root, self.venv, runtime_context())
        with patch.dict(os.environ, new_image):
            self.assertEqual(before, cache_key(self.root, self.venv, runtime_context()))

    def test_cache_key_runs_as_standalone_script(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts/asr_ci_runtime.py"
        completed = subprocess.run(
            [sys.executable, str(script), "cache-key", "--venv", str(self.venv)],
            cwd=self.root,
            env={**os.environ, "ImageOS": "win25"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(completed.stdout.startswith("asr-runtime-v2-"))

    def test_incompatible_runtime_and_paths_invalidate_cache(self) -> None:
        environment: dict[str, str] = {"ImageOS": "win25"}
        with patch.dict(os.environ, environment):
            context = runtime_context()
        original = cache_key(self.root, self.venv, context)
        for key in context:
            with self.subTest(key=key):
                self.assertNotEqual(original, cache_key(self.root, self.venv, {**context, key: "changed"}))
        self.assertNotEqual(original, cache_key(self.root, self.root / "other", context))
        self.lock.write_text("changed-lock")
        self.assertNotEqual(original, cache_key(self.root, self.venv, context))
        self.lock.write_text("fixed-lock")
        (self.root / "runtime/runtime-contract.json").write_text("changed-contract")
        self.assertNotEqual(original, cache_key(self.root, self.venv, context))

    def test_valid_cache_skips_install_but_verifies(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(args=command, returncode=0)

        with patch("scripts.asr_ci_runtime.subprocess.run", side_effect=fake_run):
            self.assertEqual(prepare_runtime(self.root, self.venv, self.manifest, True), "restored")
        self.assertEqual(len(commands), 3)
        self.assertEqual(commands[1][-2:], ["pip", "check"])
        self.assertIn("verify-runtime", commands[2])

    def test_cache_failure_rebuilds_once_and_rechecks(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            if len(commands) == 2:
                raise subprocess.CalledProcessError(1, "pip check")
            return subprocess.CompletedProcess(args=command, returncode=0)

        with patch("scripts.asr_ci_runtime.subprocess.run", side_effect=fake_run):
            self.assertEqual(prepare_runtime(self.root, self.venv, self.manifest, True), "rebuilt")
        self.assertEqual(sum("--clear" in command for command in commands), 1)
        self.assertTrue(any("--require-hashes" in command for command in commands))
        self.assertIn("verify-runtime", commands[-1])

    def test_failed_rebuild_is_not_success_or_infinite_retry(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            if len(commands) == 2:
                raise FileNotFoundError()
            if len(commands) == 7:
                raise subprocess.CalledProcessError(1, "verify-runtime")
            return subprocess.CompletedProcess(args=command, returncode=0)

        with patch("scripts.asr_ci_runtime.subprocess.run", side_effect=fake_run):
            with self.assertRaises(subprocess.CalledProcessError):
                prepare_runtime(self.root, self.venv, self.manifest, True)
        self.assertEqual(len(commands), 7)

    def test_invalid_contract_does_not_rebuild(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            raise subprocess.CalledProcessError(1, "validate")

        with patch("scripts.asr_ci_runtime.subprocess.run", side_effect=fake_run):
            with self.assertRaises(subprocess.CalledProcessError):
                prepare_runtime(self.root, self.venv, self.manifest, True)
        self.assertEqual(len(commands), 1)

    def test_cache_miss_installs_and_verifies(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(args=command, returncode=0)

        with patch("scripts.asr_ci_runtime.subprocess.run", side_effect=fake_run):
            self.assertEqual(prepare_runtime(self.root, self.venv, self.manifest, False), "installed")
        self.assertEqual(len(commands), 6)
        self.assertIn("verify-runtime", commands[-1])
