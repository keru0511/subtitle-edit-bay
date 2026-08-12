from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
QUALITY_SCRIPT = REPO_ROOT / "scripts" / "check_quality.py"


def load_quality_module():
    spec = importlib.util.spec_from_file_location("check_quality", QUALITY_SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Could not load {QUALITY_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class QualityEntrypointTests(unittest.TestCase):
    def test_lint_only_runs_only_ruff(self) -> None:
        quality = load_quality_module()

        args = quality.parse_args(["--lint-only"])
        steps = quality.build_steps(args)

        self.assertEqual(steps, [[sys.executable, "-m", "ruff", "check", "."]])

    def test_lint_only_can_be_scoped_to_explicit_paths(self) -> None:
        quality = load_quality_module()

        args = quality.parse_args(
            [
                "--lint-only",
                "--paths",
                "scripts/check_quality.py",
                "tests/test_quality_entrypoint.py",
            ]
        )
        steps = quality.build_steps(args)

        self.assertEqual(
            steps,
            [
                [
                    sys.executable,
                    "-m",
                    "ruff",
                    "check",
                    "scripts/check_quality.py",
                    "tests/test_quality_entrypoint.py",
                ]
            ],
        )

    def test_format_only_runs_only_ruff_format_check(self) -> None:
        quality = load_quality_module()

        args = quality.parse_args(["--format-only"])
        steps = quality.build_steps(args)

        self.assertEqual(steps, [[sys.executable, "-m", "ruff", "format", "--check", "."]])

    def test_format_only_can_be_scoped_to_explicit_paths(self) -> None:
        quality = load_quality_module()

        args = quality.parse_args(["--format-only", "--paths", "scripts/check_quality.py"])
        steps = quality.build_steps(args)

        self.assertEqual(
            steps,
            [
                [
                    sys.executable,
                    "-m",
                    "ruff",
                    "format",
                    "--check",
                    "scripts/check_quality.py",
                ]
            ],
        )

    def test_format_fix_runs_ruff_format_without_check(self) -> None:
        quality = load_quality_module()

        args = quality.parse_args(["--format-only", "--fix-format"])
        steps = quality.build_steps(args)

        self.assertEqual(steps, [[sys.executable, "-m", "ruff", "format", "."]])

    def test_type_only_runs_only_mypy(self) -> None:
        quality = load_quality_module()

        args = quality.parse_args(["--type-only"])
        steps = quality.build_steps(args)

        self.assertEqual(
            steps,
            [[sys.executable, "-m", "mypy", "--ignore-missing-imports", "."]],
        )

    def test_type_only_can_be_scoped_to_explicit_paths(self) -> None:
        quality = load_quality_module()

        args = quality.parse_args(["--type-only", "--paths", "scripts/check_quality.py"])
        steps = quality.build_steps(args)

        self.assertEqual(
            steps,
            [
                [
                    sys.executable,
                    "-m",
                    "mypy",
                    "--ignore-missing-imports",
                    "scripts/check_quality.py",
                ]
            ],
        )

    def test_default_runs_lint_then_unittest(self) -> None:
        quality = load_quality_module()

        args = quality.parse_args([])
        steps = quality.build_steps(args)

        self.assertEqual(steps[0], [sys.executable, "-m", "ruff", "check", "."])
        self.assertEqual(
            steps[1],
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                "test_*.py",
                "-v",
            ],
        )

    def test_include_format_runs_lint_format_then_unittest(self) -> None:
        quality = load_quality_module()

        args = quality.parse_args(["--include-format"])
        steps = quality.build_steps(args)

        self.assertEqual(steps[0], [sys.executable, "-m", "ruff", "check", "."])
        self.assertEqual(
            steps[1], [sys.executable, "-m", "ruff", "format", "--check", "."]
        )
        self.assertEqual(steps[2][1:4], ["-m", "unittest", "discover"])

    def test_include_type_check_runs_lint_mypy_then_unittest(self) -> None:
        quality = load_quality_module()

        args = quality.parse_args(["--include-type-check", "--paths", "scripts/check_quality.py"])
        steps = quality.build_steps(args)

        self.assertEqual(steps[0], [sys.executable, "-m", "ruff", "check", "scripts/check_quality.py"])
        self.assertEqual(
            steps[1],
            [
                sys.executable,
                "-m",
                "mypy",
                "--ignore-missing-imports",
                "scripts/check_quality.py",
            ],
        )
        self.assertEqual(steps[2][1:4], ["-m", "unittest", "discover"])

    def test_install_flags_prepend_dependency_steps(self) -> None:
        quality = load_quality_module()

        args = quality.parse_args(["--install-runtime", "--install-dev", "--tests-only"])
        steps = quality.build_steps(args)

        self.assertEqual(
            steps[0], [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]
        )
        self.assertEqual(
            steps[1],
            [sys.executable, "-m", "pip", "install", "-r", "requirements-dev.txt"],
        )
        self.assertEqual(steps[2][1:4], ["-m", "unittest", "discover"])

    def test_fix_format_requires_format_mode(self) -> None:
        quality = load_quality_module()

        with self.assertRaises(SystemExit):
            quality.parse_args(["--fix-format"])

    def test_type_only_cannot_be_combined_with_other_single_check_modes(self) -> None:
        quality = load_quality_module()

        with self.assertRaises(SystemExit):
            quality.parse_args(["--type-only", "--lint-only"])


if __name__ == "__main__":
    unittest.main()
