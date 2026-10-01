from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shlex
import sys
import time
import unittest
from collections import Counter
from pathlib import Path
from typing import Sequence, TypeGuard


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = REPO_ROOT / "tests" / "ci_test_groups.json"
DEFAULT_TESTS_DIR = REPO_ROOT / "tests"
DEFAULT_CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DEFAULT_GUI_TEST_PATH = DEFAULT_TESTS_DIR / "test_gui_editor.py"
WINDOWS_GUI_STEP_NAME = "Run Windows GUI regression tests"
WINDOWS_GUI_REQUIRED_MARKER = "Windows GUI CI必須"
REQUIRED_GROUPS = (
    "ffmpeg-runtime",
    "ffmpeg6-compat",
    "portable-unit",
    "qt-gui",
    "windows-ffmpeg-runtime",
    "windows-launcher-runtime",
    "windows-runtime",
)
MODULE_NAME_PATTERN = re.compile(r"^test_[A-Za-z0-9_]+$")


class ManifestError(ValueError):
    """Raised when the CI test group manifest is incomplete or invalid."""


def _string_object_dict(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def _object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def discover_test_modules(tests_dir: Path) -> list[str]:
    return sorted(path.stem for path in tests_dir.glob("test_*.py") if path.is_file())


def _validate_string_list(value: object, label: str, errors: list[str]) -> list[str]:
    if not _object_list(value) or not all(isinstance(item, str) for item in value):
        errors.append(f"{label} must be a list of strings")
        return []
    items = [item for item in value if isinstance(item, str)]
    if items != sorted(items):
        errors.append(f"{label} must be sorted")
    duplicates = sorted(item for item, count in Counter(items).items() if count > 1)
    if duplicates:
        errors.append(f"{label} contains duplicates: {', '.join(duplicates)}")
    return items


def validate_manifest(
    manifest: object,
    tests_dir: Path = DEFAULT_TESTS_DIR,
) -> dict[str, dict[str, list[str]]]:
    errors: list[str] = []
    if not _string_object_dict(manifest):
        raise ManifestError("manifest root must be an object")
    if manifest.get("schema_version") != 1:
        errors.append("schema_version must be 1")

    raw_groups = manifest.get("groups")
    if not _string_object_dict(raw_groups):
        errors.append("groups must be an object")
        raw_groups = {}

    group_names = set(raw_groups)
    required_group_names = set(REQUIRED_GROUPS)
    missing_groups = sorted(required_group_names - group_names)
    extra_groups = sorted(group_names - required_group_names)
    if missing_groups:
        errors.append(f"required groups are missing: {', '.join(missing_groups)}")
    if extra_groups:
        errors.append(f"unknown groups are present: {', '.join(extra_groups)}")

    groups: dict[str, dict[str, list[str]]] = {}
    module_owners: dict[str, list[str]] = {}
    all_selectors: list[str] = []
    for group_name in sorted(raw_groups):
        raw_group = raw_groups[group_name]
        if not _string_object_dict(raw_group):
            errors.append(f"groups.{group_name} must be an object")
            continue
        unknown_keys = sorted(set(raw_group) - {"modules", "selectors"})
        if unknown_keys:
            errors.append(f"groups.{group_name} has unknown keys: {', '.join(unknown_keys)}")
        modules = _validate_string_list(
            raw_group.get("modules", []),
            f"groups.{group_name}.modules",
            errors,
        )
        selectors = _validate_string_list(
            raw_group.get("selectors", []),
            f"groups.{group_name}.selectors",
            errors,
        )
        groups[group_name] = {"modules": modules, "selectors": selectors}
        all_selectors.extend(selectors)
        for module_name in modules:
            if not MODULE_NAME_PATTERN.fullmatch(module_name):
                errors.append(f"groups.{group_name}.modules has invalid module name: {module_name}")
            module_owners.setdefault(module_name, []).append(group_name)

    duplicated_modules = sorted(module_name for module_name, owners in module_owners.items() if len(owners) > 1)
    for module_name in duplicated_modules:
        errors.append(
            f"test module is assigned to multiple groups: {module_name} ({', '.join(module_owners[module_name])})"
        )

    discovered_modules = set(discover_test_modules(tests_dir))
    classified_modules = set(module_owners)
    missing_modules = sorted(discovered_modules - classified_modules)
    stale_modules = sorted(classified_modules - discovered_modules)
    if missing_modules:
        errors.append(f"unclassified test modules: {', '.join(missing_modules)}")
    if stale_modules:
        errors.append(f"manifest modules not found on disk: {', '.join(stale_modules)}")

    duplicate_selectors = sorted(selector for selector, count in Counter(all_selectors).items() if count > 1)
    if duplicate_selectors:
        errors.append(f"selectors are assigned more than once: {', '.join(duplicate_selectors)}")
    for selector in all_selectors:
        parts = selector.split(".")
        if (
            len(parts) < 4
            or parts[0] != "tests"
            or not MODULE_NAME_PATTERN.fullmatch(parts[1])
            or not parts[-1].startswith("test_")
        ):
            errors.append(f"invalid unittest selector: {selector}")
            continue
        if parts[1] not in discovered_modules:
            errors.append(f"selector module not found on disk: {selector}")
        elif parts[1] not in classified_modules:
            errors.append(f"selector module is unclassified: {selector}")

    if errors:
        raise ManifestError("\n- " + "\n- ".join(errors))
    return groups


def load_manifest(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    tests_dir: Path = DEFAULT_TESTS_DIR,
) -> dict[str, dict[str, list[str]]]:
    try:
        manifest: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"could not read {manifest_path}: {exc}") from exc
    return validate_manifest(manifest, tests_dir)


def _windows_gui_selectors(workflow_text: str) -> list[str]:
    lines = workflow_text.splitlines()
    step_pattern = re.compile(rf"^(\s*)- name: {re.escape(WINDOWS_GUI_STEP_NAME)}\s*$")
    matches = [(index, len(match.group(1))) for index, line in enumerate(lines) if (match := step_pattern.match(line))]
    if len(matches) != 1:
        raise ManifestError(f"{WINDOWS_GUI_STEP_NAME} step must appear exactly once")

    step_index, step_indent = matches[0]
    step_lines: list[tuple[int, str]] = []
    for index in range(step_index + 1, len(lines)):
        line = lines[index]
        if line.strip() and len(line) - len(line.lstrip()) <= step_indent:
            break
        step_lines.append((index, line))

    run_lines = [(index, line) for index, line in step_lines if re.match(r"^\s+run:", line)]
    if len(run_lines) != 1 or run_lines[0][1].strip() != "run: >-":
        raise ManifestError(f"{WINDOWS_GUI_STEP_NAME} must use one folded run: >- command")

    run_index, run_line = run_lines[0]
    run_indent = len(run_line) - len(run_line.lstrip())
    command_lines: list[str] = []
    for line in lines[run_index + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= run_indent:
            break
        if line.strip():
            command_lines.append(line.strip())
    try:
        tokens = shlex.split(" ".join(command_lines))
    except ValueError as exc:
        raise ManifestError(f"{WINDOWS_GUI_STEP_NAME} has an invalid command: {exc}") from exc
    if tokens[:4] != ["python", "-m", "unittest", "-v"] or len(tokens) == 4:
        raise ManifestError(f"{WINDOWS_GUI_STEP_NAME} must run python -m unittest -v with selectors")
    return tokens[4:]


def _gui_test_methods(gui_test_path: Path) -> tuple[set[str], set[str]]:
    try:
        source = gui_test_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(gui_test_path))
    except (OSError, SyntaxError) as exc:
        raise ManifestError(f"could not parse {gui_test_path}: {exc}") from exc

    methods: set[str] = set()
    required: set[str] = set()
    for test_class in tree.body:
        if not isinstance(test_class, ast.ClassDef):
            continue
        if not any(
            (isinstance(base, ast.Name) and base.id.endswith("TestCase"))
            or (isinstance(base, ast.Attribute) and base.attr.endswith("TestCase"))
            for base in test_class.bases
        ):
            continue
        for method in test_class.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) or not method.name.startswith("test_"):
                continue
            selector = f"tests.test_gui_editor.{test_class.name}.{method.name}"
            methods.add(selector)
            if WINDOWS_GUI_REQUIRED_MARKER in (ast.get_docstring(method) or ""):
                required.add(selector)
    return methods, required


def validate_windows_gui_selection(
    workflow_path: Path = DEFAULT_CI_WORKFLOW_PATH,
    gui_test_path: Path = DEFAULT_GUI_TEST_PATH,
) -> tuple[int, int]:
    try:
        workflow_text = workflow_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"could not read {workflow_path}: {exc}") from exc
    selectors = _windows_gui_selectors(workflow_text)
    methods, required = _gui_test_methods(gui_test_path)
    errors: list[str] = []
    duplicates = sorted(selector for selector, count in Counter(selectors).items() if count > 1)
    if duplicates:
        errors.append(f"duplicate Windows GUI selectors: {', '.join(duplicates)}")
    stale = sorted(set(selectors) - methods)
    if stale:
        errors.append(f"Windows GUI selectors not found in {gui_test_path.name}: {', '.join(stale)}")
    missing = sorted(required - set(selectors))
    if missing:
        errors.append(f"Windows GUI CI必須 tests missing from workflow: {', '.join(missing)}")
    if errors:
        raise ManifestError("\n- " + "\n- ".join(errors))
    return len(selectors), len(required)


def load_module_suite(module_name: str, loader: unittest.TestLoader) -> unittest.TestSuite:
    return loader.loadTestsFromName(f"tests.{module_name}")


def load_selector_suite(selector: str, loader: unittest.TestLoader) -> unittest.TestSuite:
    return loader.loadTestsFromName(selector)


def build_suite(
    groups: dict[str, dict[str, list[str]]],
    selected_groups: Sequence[str],
) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for group_name in selected_groups:
        group = groups[group_name]
        for module_name in group["modules"]:
            suite.addTests(load_module_suite(module_name, loader))
        for selector in group["selectors"]:
            suite.addTests(load_selector_suite(selector, loader))
    return suite


def _format_summary(
    selected_groups: Sequence[str],
    result: unittest.TestResult,
    elapsed_seconds: float,
) -> str:
    skip_reasons = Counter(reason for _test, reason in result.skipped)
    lines = [
        "### CI test group summary",
        "",
        f"Groups: `{', '.join(selected_groups)}`",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Tests run | {result.testsRun} |",
        f"| Failures | {len(result.failures)} |",
        f"| Errors | {len(result.errors)} |",
        f"| Skipped | {len(result.skipped)} |",
        f"| Elapsed | {elapsed_seconds:.2f}s |",
    ]
    if skip_reasons:
        lines.extend(["", "#### Skip reasons", ""])
        for reason, count in sorted(skip_reasons.items()):
            escaped_reason = str(reason).replace("|", "\\|").replace("\n", " ")
            lines.append(f"- {count} × `{escaped_reason}`")
    return "\n".join(lines) + "\n"


def write_github_summary(summary: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    try:
        with Path(summary_path).open("a", encoding="utf-8") as summary_file:
            summary_file.write(summary)
    except OSError as exc:
        print(f"warning: could not write GitHub step summary: {exc}", file=sys.stderr)


class _RunnerArgs(argparse.Namespace):
    manifest: Path = DEFAULT_MANIFEST_PATH
    tests_dir: Path = DEFAULT_TESTS_DIR
    groups: list[str] | None = None
    validate: bool = False


def parse_args(argv: Sequence[str] | None = None) -> _RunnerArgs:
    parser = argparse.ArgumentParser(
        description="Validate and run explicitly classified CI test groups.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="Path to the CI test group JSON manifest.",
    )
    parser.add_argument(
        "--tests-dir",
        type=Path,
        default=DEFAULT_TESTS_DIR,
        help="Directory containing test_*.py modules.",
    )
    parser.add_argument(
        "--group",
        action="append",
        dest="groups",
        help="Test group to run. May be specified more than once.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate classification without importing test modules.",
    )
    args = parser.parse_args(argv, namespace=_RunnerArgs())
    if args.validate and args.groups:
        parser.error("--validate cannot be combined with --group")
    if not args.validate and not args.groups:
        parser.error("at least one --group is required unless --validate is used")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        groups = load_manifest(args.manifest, args.tests_dir)
    except ManifestError as exc:
        print(f"CI test manifest error: {exc}", file=sys.stderr)
        return 2

    if args.validate:
        try:
            windows_gui_counts = validate_windows_gui_selection()
        except ManifestError as exc:
            print(f"Windows GUI CI selection error: {exc}", file=sys.stderr)
            return 2
        total_modules = sum(len(group["modules"]) for group in groups.values())
        print(f"CI test manifest is valid: {total_modules} modules across {len(groups)} groups.")
        for group_name in sorted(groups):
            group = groups[group_name]
            print(f"- {group_name}: {len(group['modules'])} modules, {len(group['selectors'])} selectors")
        selected, required = windows_gui_counts
        print(f"- Windows GUI CI: {selected} selectors, {required} required by test docstrings")
        return 0

    selected_groups: list[str] = []
    for group_name in args.groups or []:
        if group_name not in selected_groups:
            selected_groups.append(group_name)
    unknown_groups = sorted(set(selected_groups) - set(groups))
    if unknown_groups:
        print(
            f"Unknown CI test groups: {', '.join(unknown_groups)}. Choose from: {', '.join(sorted(groups))}",
            file=sys.stderr,
        )
        return 2

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    print(f"Running CI test groups: {', '.join(selected_groups)}", flush=True)
    suite = build_suite(groups, selected_groups)
    if suite.countTestCases() == 0:
        print("Selected CI test groups contain no tests.", file=sys.stderr)
        return 2
    started = time.perf_counter()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    elapsed_seconds = time.perf_counter() - started
    summary = _format_summary(selected_groups, result, elapsed_seconds)
    print("\n" + summary, flush=True)
    write_github_summary(summary)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
