from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run local quality checks using the same entrypoint as CI.",
    )
    parser.add_argument(
        "--lint-only",
        action="store_true",
        help="Run only Ruff lint checks.",
    )
    parser.add_argument(
        "--format-only",
        action="store_true",
        help="Run only Ruff format checks.",
    )
    parser.add_argument(
        "--type-only",
        action="store_true",
        help="Run only mypy type checks.",
    )
    parser.add_argument(
        "--tests-only",
        action="store_true",
        help="Run only the unittest suite.",
    )
    parser.add_argument(
        "--include-format",
        action="store_true",
        help="Include Ruff format checks in addition to the default checks.",
    )
    parser.add_argument(
        "--include-type-check",
        action="store_true",
        help="Include mypy type checks in addition to the default checks.",
    )
    parser.add_argument(
        "--fix-format",
        action="store_true",
        help="Run Ruff format instead of Ruff format --check. Requires --include-format or --format-only.",
    )
    parser.add_argument(
        "--skip-lint",
        action="store_true",
        help="Skip Ruff lint checks.",
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Skip the unittest suite.",
    )
    parser.add_argument(
        "--install-runtime",
        action="store_true",
        help="Install runtime dependencies from requirements.txt before checks.",
    )
    parser.add_argument(
        "--install-dev",
        action="store_true",
        help="Install development dependencies from requirements-dev.txt before checks.",
    )
    parser.add_argument(
        "--test-dir",
        default="tests",
        help="Directory passed to unittest discover.",
    )
    parser.add_argument(
        "--test-pattern",
        default="test_*.py",
        help="File pattern passed to unittest discover.",
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        metavar="PATH",
        help="Limit Ruff or mypy checks to specific files or directories.",
    )
    args = parser.parse_args(argv)

    exclusive_modes = (args.lint_only, args.format_only, args.type_only, args.tests_only)
    if sum(1 for enabled in exclusive_modes if enabled) > 1:
        parser.error("--lint-only, --format-only, --type-only, and --tests-only cannot be combined")
    if args.include_format and (args.lint_only or args.type_only or args.tests_only):
        parser.error("--include-format cannot be used with --lint-only, --type-only, or --tests-only")
    if args.include_type_check and (args.lint_only or args.format_only or args.tests_only):
        parser.error("--include-type-check cannot be used with --lint-only, --format-only, or --tests-only")
    if args.fix_format and not (args.include_format or args.format_only):
        parser.error("--fix-format requires --include-format or --format-only")

    if args.format_only:
        args.skip_lint = True
        args.skip_tests = True
        args.include_format = True
    elif args.type_only:
        args.skip_lint = True
        args.skip_tests = True
        args.include_type_check = True
    elif args.lint_only:
        args.skip_tests = True
    elif args.tests_only:
        args.skip_lint = True
    if args.skip_lint and args.skip_tests and not (args.include_format or args.include_type_check):
        parser.error("all checks are disabled")
    return args


def quality_targets(args: argparse.Namespace) -> list[str]:
    return list(args.paths or ["."])


def build_steps(args: argparse.Namespace) -> list[list[str]]:
    steps: list[list[str]] = []
    if args.install_runtime:
        steps.append([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    if args.install_dev:
        steps.append([sys.executable, "-m", "pip", "install", "-r", "requirements-dev.txt"])
    if not args.skip_lint:
        steps.append([sys.executable, "-m", "ruff", "check", *quality_targets(args)])
    if args.include_format:
        format_command = [sys.executable, "-m", "ruff", "format"]
        if not args.fix_format:
            format_command.append("--check")
        format_command.extend(quality_targets(args))
        steps.append(format_command)
    if args.include_type_check:
        steps.append([
            sys.executable,
            "-m",
            "mypy",
            "--ignore-missing-imports",
            *quality_targets(args),
        ])
    if not args.skip_tests:
        steps.append(
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                str(args.test_dir),
                "-p",
                str(args.test_pattern),
                "-v",
            ]
        )
    return steps


def run_step(command: Sequence[str]) -> int:
    print(f"> {subprocess.list2cmdline(list(command))}", flush=True)
    return subprocess.run(list(command), cwd=REPO_ROOT, check=False).returncode


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    for command in build_steps(args):
        exit_code = run_step(command)
        if exit_code != 0:
            return exit_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
