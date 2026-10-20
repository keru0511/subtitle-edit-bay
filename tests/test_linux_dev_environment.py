"""CIとクラウドで異なる依存やセットアップ経路を使う回帰を防ぐ。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

from scripts.linux_dev_environment import check_inputs, check_versions, locked_versions
from src.data_boundary import decode_json, is_object_list, is_object_mapping
from tests.typed_case import TypedTestCase, typed_skip_unless


ROOT = Path(__file__).resolve().parents[1]


class LinuxEnvironmentTests(TypedTestCase):
    def test_input_drift_requires_lock_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dependency = root / "requirements.txt"
            dependency.write_bytes(b"budoux>=0.7,<1\n")
            checksums = hashlib.sha256(dependency.read_bytes()).hexdigest() + "  requirements.txt\n"
            check_inputs(root, checksums)
            dependency.write_bytes(b"budoux>=0.9,<1\n")
            with self.assertRaisesRegex(ValueError, "requirements.txt"):
                check_inputs(root, checksums)

    def test_rejects_changed_missing_and_extra_distributions(self) -> None:
        expected = {"budoux": "0.9.3", "mypy": "2.3.1"}
        check_versions(expected, dict(expected))
        for actual in (
            {"budoux": "0.7.0", "mypy": "2.3.1"},
            {"budoux": "0.9.3"},
            {**expected, "unexpected-package": "1.0"},
        ):
            with self.subTest(actual=actual), self.assertRaisesRegex(ValueError, "version mismatch"):
                check_versions(expected, actual)

    def test_lock_normalizes_distribution_names_and_preserves_cpu_build(self) -> None:
        self.assertEqual(
            locked_versions("PySide6_Addons==6.11.2 \\\n    --hash=sha256:abc\ntorch==2.8.0+cpu\n"),
            {"pyside6-addons": "6.11.2", "torch": "2.8.0+cpu"},
        )

    def test_committed_lock_inputs_are_current(self) -> None:
        check_inputs(ROOT, (ROOT / "runtime/linux-dev/inputs.sha256").read_text(encoding="utf-8"))

    def test_ci_and_release_delegate_to_the_shared_environment(self) -> None:
        cases = (
            ("ci.yml", "python-quality", "quality"),
            ("ci.yml", "portable-tests", "test"),
            ("release-prepare.yml", "test", "test"),
        )
        for filename, job_name, command in cases:
            with self.subTest(workflow=filename, job=job_name):
                workflow: object = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"))
                assert is_object_mapping(workflow)
                jobs = workflow["jobs"]
                assert is_object_mapping(jobs)
                job = jobs[job_name]
                assert is_object_mapping(job)
                steps = job["steps"]
                assert is_object_list(steps)
                commands = "\n".join(str(step.get("run", "")) for step in steps if is_object_mapping(step))
                self.assertIn(f"bash scripts/linux_dev.sh {command}", commands)
                self.assertNotIn("pip install", commands)
                self.assertNotIn("apt-get install", commands)


@typed_skip_unless(sys.platform != "win32", "Linux shell entrypoint")
class LinuxEntrypointTests(TypedTestCase):
    def _run_wrapper(self, *, build_failure: bool = False) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = root / "docker"
            docker.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "with open(os.environ['DOCKER_CALL_LOG'], 'a') as stream:\n"
                "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "if sys.argv[1:3] == ['image', 'inspect']: sys.exit(1)\n"
                "if sys.argv[1] == 'build' and os.environ.get('FAIL_BUILD'): sys.exit(9)\n",
                encoding="utf-8",
            )
            docker.chmod(0o755)
            log = root / "docker.jsonl"
            environment = {
                **os.environ,
                "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "XDG_CACHE_HOME": str(root),
                "DOCKER_CALL_LOG": str(log),
                "FAIL_BUILD": "1" if build_failure else "",
            }
            result = subprocess.run(
                ["bash", str(ROOT / "scripts/linux_dev.sh"), "python", "-c", "print('space preserved')"],
                env=environment,
                capture_output=True,
                text=True,
            )
            calls: list[list[str]] = []
            for line in log.read_text(encoding="utf-8").splitlines():
                call = decode_json(line)
                assert is_object_list(call) and all(isinstance(argument, str) for argument in call)
                calls.append([argument for argument in call if isinstance(argument, str)])
            return result, calls

    def test_missing_image_builds_and_runs_the_same_tag_with_original_arguments(self) -> None:
        result, calls = self._run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[0] for call in calls], ["image", "build", "run"])
        image = calls[0][-1]
        self.assertEqual(calls[1][calls[1].index("--tag") + 1], image)
        self.assertEqual(calls[2][-4:], [image, "python", "-c", "print('space preserved')"])
        self.assertIn("linux/amd64", calls[1])
        self.assertIn("linux/amd64", calls[2])

    def test_failed_build_stops_before_run_without_a_host_fallback(self) -> None:
        result, calls = self._run_wrapper(build_failure=True)
        self.assertEqual(result.returncode, 9)
        self.assertEqual([call[0] for call in calls], ["image", "build"])


if __name__ == "__main__":
    unittest.main()
