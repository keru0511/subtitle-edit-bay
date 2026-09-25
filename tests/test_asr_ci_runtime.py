from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.asr_ci_runtime import cache_key, prepare_runtime, runtime_context


class AsrCiRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "runtime").mkdir()
        (self.root / "runtime/runtime-contract.json").write_text(json.dumps({"python": {"pip_version": "26.2.1"}}))
        self.lock = self.root / "runtime/requirements-windows-cpu.lock"
        self.lock.write_text("fixed-lock")
        self.venv = self.root / ".asr-venv"
        self.manifest = self.root / "manifest.json"

    def test_image_date_does_not_invalidate_cache(self):
        with patch.dict(os.environ, {"ImageOS": "win25", "ImageVersion": "old"}):
            before = cache_key(self.root, self.venv, runtime_context())
        with patch.dict(os.environ, {"ImageOS": "win25", "ImageVersion": "new"}):
            self.assertEqual(before, cache_key(self.root, self.venv, runtime_context()))

    def test_incompatible_runtime_and_paths_invalidate_cache(self):
        with patch.dict(os.environ, {"ImageOS": "win25"}):
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

    @patch("scripts.asr_ci_runtime.subprocess.run")
    def test_valid_cache_skips_install_but_verifies(self, run):
        self.assertEqual(prepare_runtime(self.root, self.venv, self.manifest, True), "restored")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 3)
        self.assertEqual(commands[1][-2:], ["pip", "check"])
        self.assertIn("verify-runtime", commands[2])

    @patch("scripts.asr_ci_runtime.subprocess.run")
    def test_cache_failure_rebuilds_once_and_rechecks(self, run):
        run.side_effect = [None, subprocess.CalledProcessError(1, "pip check"), None, None, None, None, None]
        self.assertEqual(prepare_runtime(self.root, self.venv, self.manifest, True), "rebuilt")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(sum("--clear" in command for command in commands), 1)
        self.assertTrue(any("--require-hashes" in command for command in commands))
        self.assertIn("verify-runtime", commands[-1])

    @patch("scripts.asr_ci_runtime.subprocess.run")
    def test_failed_rebuild_is_not_success_or_infinite_retry(self, run):
        run.side_effect = [
            None,
            FileNotFoundError(),
            None,
            None,
            None,
            None,
            subprocess.CalledProcessError(1, "verify-runtime"),
        ]
        with self.assertRaises(subprocess.CalledProcessError):
            prepare_runtime(self.root, self.venv, self.manifest, True)
        self.assertEqual(run.call_count, 7)

    @patch("scripts.asr_ci_runtime.subprocess.run")
    def test_invalid_contract_does_not_rebuild(self, run):
        run.side_effect = subprocess.CalledProcessError(1, "validate")
        with self.assertRaises(subprocess.CalledProcessError):
            prepare_runtime(self.root, self.venv, self.manifest, True)
        self.assertEqual(run.call_count, 1)

    @patch("scripts.asr_ci_runtime.subprocess.run")
    def test_cache_miss_installs_and_verifies(self, run):
        self.assertEqual(prepare_runtime(self.root, self.venv, self.manifest, False), "installed")
        self.assertEqual(run.call_count, 6)
        self.assertIn("verify-runtime", run.call_args.args[0])
