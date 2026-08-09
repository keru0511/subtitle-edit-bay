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
        "--tests-only",
        action="store_true",
        help="Run only the unittest suite.",
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
    args = parser.parse_args(argv)
    if args.lint_only and args.tests_only:
        parser.error("--lint-only and --tests-only cannot be used together")
    if args.lint_only:
        args.skip_tests = True
    if args.tests_only:
        args.skip_lint = True
    if args.skip_lint and args.skip_tests:
        parser.error("all checks are disabled")
    return args


def build_steps(args: argparse.Namespace) -> list[list[str]]:
    steps: list[list[str]] = []
    if args.install_runtime:
        steps.append([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    if args.install_dev:
        steps.append([sys.executable, "-m", "pip", "install", "-r", "requirements-dev.txt"])
    if not args.skip_lint:
        steps.append([sys.executable, "-m", "ruff", "check", "."])
    if not args.skip_tests:
        steps.append([
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(args.test_dir),
            "-p",
            str(args.test_pattern),
            "-v",
        ])
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
