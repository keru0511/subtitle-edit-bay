from __future__ import annotations

import sys
import unittest

from scripts import check_quality as quality
from tests.typed_case import TypedTestCase


class QualityEntrypointTests(TypedTestCase):
    def test_lint_only_runs_only_ruff(self) -> None:
        args = quality.parse_args(["--lint-only"])
        steps = quality.build_steps(args)

        self.assertEqual(steps, [[sys.executable, "-m", "ruff", "check", "."]])

    def test_lint_only_can_be_scoped_to_explicit_paths(self) -> None:
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
        args = quality.parse_args(["--format-only"])
        steps = quality.build_steps(args)

        self.assertEqual(steps, [[sys.executable, "-m", "ruff", "format", "--check", "."]])

    def test_format_only_can_be_scoped_to_explicit_paths(self) -> None:
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
        args = quality.parse_args(["--format-only", "--fix-format"])
        steps = quality.build_steps(args)

        self.assertEqual(steps, [[sys.executable, "-m", "ruff", "format", "."]])

    def test_type_only_uses_configured_mypy_targets(self) -> None:
        args = quality.parse_args(["--type-only"])
        steps = quality.build_steps(args)

        self.assertEqual(
            steps,
            [[sys.executable, "-m", "mypy"]],
        )

    def test_type_only_can_be_scoped_to_explicit_paths(self) -> None:
        args = quality.parse_args(["--type-only", "--paths", "scripts/check_quality.py"])
        steps = quality.build_steps(args)

        self.assertEqual(
            steps,
            [
                [
                    sys.executable,
                    "-m",
                    "mypy",
                    "scripts/check_quality.py",
                ]
            ],
        )

    def test_default_runs_lint_then_unittest(self) -> None:
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
        args = quality.parse_args(["--include-format"])
        steps = quality.build_steps(args)

        self.assertEqual(steps[0], [sys.executable, "-m", "ruff", "check", "."])
        self.assertEqual(steps[1], [sys.executable, "-m", "ruff", "format", "--check", "."])
        self.assertEqual(steps[2][1:4], ["-m", "unittest", "discover"])

    def test_include_type_check_runs_lint_mypy_then_unittest(self) -> None:
        args = quality.parse_args(["--include-type-check", "--paths", "scripts/check_quality.py"])
        steps = quality.build_steps(args)

        self.assertEqual(steps[0], [sys.executable, "-m", "ruff", "check", "scripts/check_quality.py"])
        self.assertEqual(
            steps[1],
            [
                sys.executable,
                "-m",
                "mypy",
                "scripts/check_quality.py",
            ],
        )
        self.assertEqual(steps[2][1:4], ["-m", "unittest", "discover"])

    def test_install_flags_prepend_dependency_steps(self) -> None:
        args = quality.parse_args(["--install-runtime", "--install-dev", "--tests-only"])
        steps = quality.build_steps(args)

        self.assertEqual(steps[0], [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        self.assertEqual(
            steps[1],
            [sys.executable, "-m", "pip", "install", "-r", "requirements-dev.txt"],
        )
        self.assertEqual(steps[2][1:4], ["-m", "unittest", "discover"])

    def test_fix_format_requires_format_mode(self) -> None:
        with self.assertRaises(SystemExit):
            quality.parse_args(["--fix-format"])

    def test_type_platform_is_forwarded_and_requires_type_checks(self) -> None:
        for platform in ("linux", "win32", "darwin"):
            with self.subTest(platform=platform):
                args = quality.parse_args(
                    [
                        "--type-only",
                        "--type-platform",
                        platform,
                        "--paths",
                        "src/process_utils.py",
                    ]
                )
                self.assertEqual(
                    quality.build_steps(args),
                    [
                        [
                            sys.executable,
                            "-m",
                            "mypy",
                            "--platform",
                            platform,
                            "src/process_utils.py",
                        ]
                    ],
                )
        with self.assertRaises(SystemExit):
            quality.parse_args(["--lint-only", "--type-platform", "win32"])

    def test_type_only_cannot_be_combined_with_other_single_check_modes(self) -> None:
        with self.assertRaises(SystemExit):
            quality.parse_args(["--type-only", "--lint-only"])


if __name__ == "__main__":
    unittest.main()
