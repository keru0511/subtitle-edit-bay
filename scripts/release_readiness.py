from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.release_contract import ReleaseContractError, release_version_from_tag


RELEASE_INFRASTRUCTURE_PREFIXES = (
    ".github/workflows/release",
    "installer/",
)
RELEASE_INFRASTRUCTURE_FILES = {
    "scripts/build_installer.ps1",
    "scripts/build_release_package.ps1",
    "scripts/release_candidate.py",
    "scripts/release_contract.py",
    "scripts/release_readiness.py",
    "scripts/release_state.py",
    "scripts/run_ci_tests.py",
    "scripts/test_installer.ps1",
    "tests/ci_test_groups.json",
}


class ReleaseReadinessError(ValueError):
    """Raised when a release candidate does not satisfy the release policy."""


@dataclass(frozen=True)
class Classification:
    kind: str
    release_version: str
    requires_preparation: bool
    changed_files: tuple[str, ...]


def _git(*args: str) -> str:
    completed = subprocess.run(
        ("git", *args),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ReleaseReadinessError(f"git {' '.join(args)} failed: {detail}")
    return completed.stdout


def _read_version_at(commit: str) -> str:
    value = _git("show", f"{commit}:VERSION").strip()
    try:
        release_version_from_tag(value)
    except ReleaseContractError as exc:
        raise ReleaseReadinessError(f"VERSION at {commit} is invalid: {exc}") from exc
    return value


def _changed_files(base_sha: str, source_sha: str) -> tuple[str, ...]:
    records = _git("diff", "--name-status", "--no-renames", base_sha, source_sha).splitlines()
    changed: list[str] = []
    for record in records:
        columns = record.split("\t")
        if len(columns) != 2:
            raise ReleaseReadinessError(f"unexpected git diff record: {record}")
        status, path = columns
        if status not in {"A", "M", "D"}:
            raise ReleaseReadinessError(f"unsupported change status for {path}: {status}")
        changed.append(path)
    return tuple(changed)


def _version_tuple(tag: str) -> tuple[int, int, int]:
    version = release_version_from_tag(tag)
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


def _is_release_infrastructure(path: str) -> bool:
    return path in RELEASE_INFRASTRUCTURE_FILES or any(
        path.startswith(prefix) for prefix in RELEASE_INFRASTRUCTURE_PREFIXES
    )


def classify_values(
    changed_files: tuple[str, ...],
    base_version: str,
    source_version: str,
) -> Classification:
    try:
        release_version_from_tag(source_version)
    except ReleaseContractError as exc:
        raise ReleaseReadinessError(f"source VERSION is invalid: {exc}") from exc
    version_path_changed = "VERSION" in changed_files
    if version_path_changed:
        try:
            release_version_from_tag(base_version)
        except ReleaseContractError as exc:
            raise ReleaseReadinessError(f"base VERSION is invalid: {exc}") from exc
        if source_version == base_version:
            raise ReleaseReadinessError("VERSION was modified without changing its value")
        if changed_files != ("VERSION",):
            raise ReleaseReadinessError("a release PR must change only VERSION; changed: " + ", ".join(changed_files))
        if _version_tuple(source_version) <= _version_tuple(base_version):
            raise ReleaseReadinessError(
                f"release version must increase: previous={base_version} requested={source_version}"
            )
        return Classification("release", source_version, True, changed_files)
    if any(_is_release_infrastructure(path) for path in changed_files):
        return Classification("infrastructure", source_version, True, changed_files)
    return Classification("normal", source_version, False, changed_files)


def classify_changes(base_sha: str, source_sha: str) -> Classification:
    changed_files = _changed_files(base_sha, source_sha)
    source_version = _read_version_at(source_sha)
    base_version = _read_version_at(base_sha) if "VERSION" in changed_files else source_version
    return classify_values(changed_files, base_version, source_version)


def write_github_outputs(path: Path, classification: Classification) -> None:
    values = {
        "kind": classification.kind,
        "release_version": classification.release_version,
        "requires_preparation": str(classification.requires_preparation).lower(),
        "changed_files_json": json.dumps(classification.changed_files, ensure_ascii=False),
    }
    with path.open("a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def assert_readiness_result(
    kind: str,
    classify_result: str,
    requires_preparation: bool,
    prepare_result: str,
) -> None:
    if classify_result != "success":
        raise ReleaseReadinessError("classification did not succeed")
    if kind in {"release", "infrastructure"}:
        if not requires_preparation or prepare_result != "success":
            raise ReleaseReadinessError(f"required release preparation did not succeed: {prepare_result}")
        return
    if kind == "normal":
        if requires_preparation or prepare_result != "skipped":
            raise ReleaseReadinessError(f"unexpected preparation state for a normal PR: {prepare_result}")
        return
    raise ReleaseReadinessError(f"unexpected release classification: {kind}")


def assert_preparation_results(results: Sequence[str]) -> None:
    if len(results) != 4:
        raise ReleaseReadinessError("exactly four preparation stage results are required")
    unsuccessful = [result for result in results if result != "success"]
    if unsuccessful:
        raise ReleaseReadinessError("preparation stage failed or skipped: " + ", ".join(unsuccessful))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify and aggregate release readiness checks.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    classify = subparsers.add_parser("classify")
    classify.add_argument("--base-sha", required=True)
    classify.add_argument("--source-sha", required=True)
    classify.add_argument("--github-output", type=Path)
    readiness = subparsers.add_parser("assert-readiness")
    readiness.add_argument("--kind", required=True)
    readiness.add_argument("--classify-result", required=True)
    readiness.add_argument("--requires-preparation", required=True, choices=("true", "false"))
    readiness.add_argument("--prepare-result", required=True)
    preparation = subparsers.add_parser("assert-preparation")
    preparation.add_argument("--result", action="append", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "classify":
            classification = classify_changes(args.base_sha, args.source_sha)
            print(json.dumps(asdict(classification), ensure_ascii=False))
            if args.github_output:
                write_github_outputs(args.github_output, classification)
        elif args.command == "assert-readiness":
            assert_readiness_result(
                args.kind,
                args.classify_result,
                args.requires_preparation == "true",
                args.prepare_result,
            )
        else:
            assert_preparation_results(args.result)
    except (ReleaseReadinessError, ReleaseContractError) as exc:
        print(f"Release readiness error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
