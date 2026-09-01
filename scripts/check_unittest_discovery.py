from __future__ import annotations

import argparse
import ast
import importlib
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TESTS_DIR = REPO_ROOT / "tests"


@dataclass(frozen=True)
class ModuleDiscoveryResult:
    module_name: str
    discovered_count: int
    errors: tuple[str, ...]


def discover_test_files(tests_dir: Path) -> list[Path]:
    return sorted(path for path in tests_dir.glob("test_*.py") if path.is_file())


def _module_level_tests(path: Path) -> tuple[str, ...]:
    try:
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise ValueError(f"could not parse {path}: {exc}") from exc
    return tuple(
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    )


def audit_test_modules(
    tests_dir: Path = DEFAULT_TESTS_DIR,
    *,
    package_name: str = "tests",
) -> list[ModuleDiscoveryResult]:
    if not tests_dir.is_dir():
        raise ValueError(f"tests directory does not exist: {tests_dir}")

    test_files = discover_test_files(tests_dir)
    if not test_files:
        raise ValueError(f"no test modules found in: {tests_dir}")

    importlib.invalidate_caches()
    results: list[ModuleDiscoveryResult] = []
    search_root = str(tests_dir.parent.resolve())
    sys.path.insert(0, search_root)
    try:
        for path in test_files:
            module_name = f"{package_name}.{path.stem}"
            errors: list[str] = []
            try:
                module_level_tests = _module_level_tests(path)
            except ValueError as exc:
                results.append(ModuleDiscoveryResult(module_name, 0, (str(exc),)))
                continue
            if module_level_tests:
                errors.append("module-level tests are not supported: " + ", ".join(module_level_tests))

            try:
                module = importlib.import_module(module_name)
                module_file = getattr(module, "__file__", None)
                imported_path = Path(module_file).resolve() if module_file else None
                if imported_path != path.resolve():
                    errors.append(f"import resolved to unexpected file: {imported_path}")
                loader = unittest.TestLoader()
                suite = loader.loadTestsFromModule(module)
                discovered_count = suite.countTestCases()
                for loader_error in loader.errors:
                    lines = [line.strip() for line in loader_error.splitlines() if line.strip()]
                    summary = lines[-1] if lines else "unknown loader error"
                    errors.append(f"unittest loader failed: {summary}")
            except unittest.SkipTest:
                # Standard unittest discovery represents a skipped module as one
                # synthetic skipped test, so it is discovered rather than empty.
                discovered_count = 1
            except (Exception, SystemExit) as exc:  # noqa: BLE001 - report import failures by module
                errors.append(f"import failed: {type(exc).__name__}: {exc}")
                discovered_count = 0
            if discovered_count == 0:
                errors.append("unittest discovered 0 tests")
            results.append(ModuleDiscoveryResult(module_name, discovered_count, tuple(errors)))
    finally:
        sys.path.remove(search_root)
    return results


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reject test modules that standard unittest discovery cannot collect.",
    )
    parser.add_argument(
        "--tests-dir",
        type=Path,
        default=DEFAULT_TESTS_DIR,
        help="Directory containing test_*.py modules.",
    )
    parser.add_argument(
        "--package",
        default="tests",
        help="Import package corresponding to --tests-dir.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        results = audit_test_modules(args.tests_dir, package_name=args.package)
    except ValueError as exc:
        print(f"unittest discovery check failed: {exc}", file=sys.stderr)
        return 2

    failed = False
    total_tests = 0
    for result in results:
        total_tests += result.discovered_count
        if result.errors:
            failed = True
            print(
                f"{result.module_name}: {result.discovered_count} tests [ERROR] " + "; ".join(result.errors),
                file=sys.stderr,
            )
        else:
            print(f"{result.module_name}: {result.discovered_count} tests")
    if failed:
        return 1
    print(f"unittest discovery is complete: {len(results)} modules, {total_tests} tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
