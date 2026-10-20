"""Linux CIとクラウドの共通セットアップ契約を検証する。"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

from src.data_boundary import is_object_list, is_object_mapping
from tests.typed_case import TypedTestCase, typed_skip_unless


ROOT = Path(__file__).resolve().parents[1]


class LinuxWorkflowTests(TypedTestCase):
    def test_ci_and_release_use_the_same_setup_and_environment(self) -> None:
        for filename, job_name, prefix in (
            ("ci.yml", "python-quality", ""),
            ("ci.yml", "portable-tests", ""),
            ("release-prepare.yml", "test", "source/"),
        ):
            with self.subTest(workflow=filename, job=job_name):
                workflow: object = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"))
                assert is_object_mapping(workflow)
                jobs = workflow["jobs"]
                assert is_object_mapping(jobs)
                job = jobs[job_name]
                assert is_object_mapping(job)
                environment = job["env"]
                assert is_object_mapping(environment)
                self.assertEqual(
                    environment["BASH_ENV"], "${{ github.workspace }}/" + prefix + "runtime/linux-dev/env.sh"
                )
                steps = job["steps"]
                assert is_object_list(steps)
                commands = "\n".join(str(step.get("run", "")) for step in steps if is_object_mapping(step))
                self.assertIn("bash scripts/setup_linux.sh", commands)
                self.assertNotIn("pip install", commands)
                self.assertNotIn("apt-get install", commands)
                self.assertNotIn("docker", commands)


@typed_skip_unless(sys.platform == "linux", "Linux shell setup")
class LinuxSetupTests(TypedTestCase):
    def _copy_setup(self, directory: str) -> Path:
        root = Path(directory) / "repository with spaces"
        for filename in ("scripts/setup_linux.sh", "runtime/linux-dev/env.sh", "runtime/linux-dev/toolchain.env"):
            target = root / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / filename, target)
        return root

    def _environment(self, root: Path) -> dict[str, str]:
        return {
            **os.environ,
            "BASH_ENV": "/dev/null",
            "VIRTUAL_ENV": "/unrelated/venv",
            "XDG_CACHE_HOME": str(root / "cache"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_DATA_HOME": str(root / "data"),
            "XDG_RUNTIME_DIR": str(root / "run"),
        }

    def _settings(self, root: Path) -> dict[str, str]:
        return dict(
            (line.partition("=")[0], line.partition("=")[2])
            for line in (root / "runtime/linux-dev/toolchain.env").read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")
        )

    def test_activation_selects_repository_tools_from_another_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._copy_setup(directory)
            uv_version = self._settings(root)["UV_VERSION"]
            pinned_uv = root / ".local/linux-dev" / ("uv-" + uv_version) / "uv"
            for executable in (root / ".venv/bin/python", root / ".venv/bin/uv", pinned_uv):
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
                executable.chmod(0o755)
            result = subprocess.run(
                [
                    "bash",
                    "-ec",
                    'source "$1"; printf "%s\\n" "$VIRTUAL_ENV" "$(command -v python)" "${LD_LIBRARY_PATH%%:*}" "$QT_QPA_PLATFORM" "$(command -v uv)"',
                    "bash",
                    str(root / "runtime/linux-dev/env.sh"),
                ],
                cwd=directory,
                env=self._environment(root),
                capture_output=True,
                text=True,
                check=True,
            )
            lines = result.stdout.splitlines()
            self.assertEqual(lines[:2], [str(root / ".venv"), str(root / ".venv/bin/python")])
            self.assertTrue(lines[2].startswith(str(root / ".local/linux-dev/ffmpeg-")))
            self.assertEqual(lines[3], "offscreen")
            self.assertEqual(lines[4], str(pinned_uv))

    def test_hash_mismatch_stops_before_installing_or_reusing_a_stale_download(self) -> None:
        for cached in (False, True):
            with self.subTest(cached=cached):
                self._check_hash_mismatch(cached=cached)

    def _check_hash_mismatch(self, *, cached: bool) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._copy_setup(directory)
            uv_directory = root / ".local/linux-dev" / ("uv-" + self._settings(root)["UV_VERSION"])
            if cached:
                uv_directory.mkdir(parents=True)
                (uv_directory / ".installed").write_text("old-checksum\n", encoding="utf-8")
                uv = uv_directory / "uv"
                uv.write_text("#!/bin/bash\nexit 97\n", encoding="utf-8")
                uv.chmod(0o755)
            tools = root / "fake-tools"
            tools.mkdir()
            for name, body in (
                ("apt-get", "exit 0"),
                ("sudo", 'exec "$@"'),
                ("curl", 'while (( $# )); do if [[ "$1" == -o ]]; then printf corrupt > "$2"; exit 0; fi; shift; done'),
            ):
                executable = tools / name
                executable.write_text("#!/bin/bash\n" + body + "\n", encoding="utf-8")
                executable.chmod(0o755)
            environment = self._environment(root)
            environment["PATH"] = str(tools) + os.pathsep + os.environ["PATH"]
            result = subprocess.run(
                ["bash", str(root / "scripts/setup_linux.sh")],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SHA-256 mismatch:", result.stderr)
            self.assertIn("uv-x86_64-unknown-linux-gnu.tar.gz", result.stderr)
            if cached:
                self.assertEqual((uv_directory / ".installed").read_text(encoding="utf-8"), "old-checksum\n")
            else:
                self.assertFalse((uv_directory / ".installed").exists())
            self.assertEqual(list((root / ".local/linux-dev").glob("download.*")), [])
            self.assertFalse((root / ".venv").exists())

    def test_missing_cached_executable_fails_before_using_host_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._copy_setup(directory)
            settings = self._settings(root)
            state = root / ".local/linux-dev"
            for name, checksum in (
                ("uv-" + settings["UV_VERSION"], settings["UV_SHA256"]),
                ("node-" + settings["NODE_VERSION"], settings["NODE_SHA256"]),
                ("ffmpeg-" + settings["FFMPEG_BUILD"], settings["FFMPEG_SHA256"]),
            ):
                destination = state / name
                destination.mkdir(parents=True)
                (destination / ".installed").write_text(checksum + "\n", encoding="utf-8")
            uv = state / ("uv-" + settings["UV_VERSION"]) / "uv"
            uv.write_text("#!/bin/bash\necho unexpected-uv-execution >&2\nexit 97\n", encoding="utf-8")
            uv.chmod(0o755)
            result = subprocess.run(
                ["bash", str(root / "scripts/setup_linux.sh")],
                env=self._environment(root),
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Missing tool executable:", result.stderr)
            self.assertIn(str(state / ("node-" + settings["NODE_VERSION"]) / "bin/node"), result.stderr)
            self.assertNotIn("unexpected-uv-execution", result.stderr)


if __name__ == "__main__":
    unittest.main()
