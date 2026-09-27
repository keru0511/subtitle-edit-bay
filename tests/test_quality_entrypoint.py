from __future__ import annotations

import sys
import unittest

from scripts import check_quality as quality
from scripts.check_no_any import source_findings, tracked_python_files
from tests.typed_case import TypedTestCase


class QualityEntrypointTests(TypedTestCase):
    def test_no_any_gate_covers_tracked_sources_and_stubs(self) -> None:
        paths = tracked_python_files()
        names = {path.as_posix() for path in paths}
        self.assertTrue("scripts/check_quality.py" in names)
        self.assertTrue("typings/numpy/__init__.pyi" in names)

    def test_no_any_gate_rejects_explicit_any_and_type_check_suppression(self) -> None:
        source = (
            "from typing import Any as Unknown\n"
            "value: Unknown = 1  # type: ignore[assignment]\n"
            "qualified = typing.Any\n"
            "# mypy: ignore-errors\n"
        )
        findings = source_findings(source, "sample.py")
        self.assertEqual(len(findings), 4)
        self.assertTrue(any("sample.py:1: Any" in finding for finding in findings))
        self.assertTrue(any("sample.py:2: 型チェック" in finding for finding in findings))
        self.assertTrue(any("sample.py:3: Any" in finding for finding in findings))
        self.assertTrue(any("sample.py:4: 型チェック" in finding for finding in findings))

    def test_no_any_gate_ignores_text_without_type_use(self) -> None:
        self.assertEqual(source_findings('message = "Any is a word"\n', "sample.py"), [])

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
        self.assertEqual(
            steps[2],
            [sys.executable, "-m", "pip", "install", "--no-deps", "-r", "requirements-type-stubs.txt"],
        )
        self.assertEqual(steps[3][1:4], ["-m", "unittest", "discover"])

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
