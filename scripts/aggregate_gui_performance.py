from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.gui_performance_report import (
    REPORT_SCHEMA_VERSION,
    SCENARIO_NAMES,
    aggregate_runs,
)


class ReportValidationError(ValueError):
    pass


def _load_report(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReportValidationError(f"could not read shard report {path}: {error}") from error
    if not isinstance(value, dict) or value.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise ReportValidationError(f"unsupported shard report schema: {path}")
    return value


def _expected_configuration(
    kind: str,
    *,
    repetitions: int,
    playback_seconds: float,
) -> dict[str, object]:
    return {
        "segment_counts": [3000, 10000] if kind == "current" else [3000],
        "repetitions": 1,
        "total_repetitions": repetitions,
        "playback_seconds": playback_seconds,
        "settle_ms": 100,
        "contracts_enforced": kind == "current",
        "media_generated_at_runtime": True,
    }


def aggregate_shards(
    paths: Sequence[Path],
    *,
    repetitions: int,
    playback_seconds: float,
    current_revision: str,
    baseline_revision: str,
    harness_revision: str,
    run_id: str,
    run_attempt: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    candidates: dict[tuple[str, int, int], dict[str, Any]] = {}
    for path in paths:
        report = _load_report(path)
        configuration = report.get("configuration")
        if not isinstance(configuration, dict):
            raise ReportValidationError(f"missing configuration in {path}")
        expected_by_kind = {
            kind: _expected_configuration(
                kind,
                repetitions=repetitions,
                playback_seconds=playback_seconds,
            )
            for kind in ("current", "baseline")
        }
        matching_kinds = [
            kind
            for kind, candidate in expected_by_kind.items()
            if all(configuration.get(key) == value for key, value in candidate.items())
        ]
        if len(matching_kinds) != 1:
            raise ReportValidationError(f"could not identify shard kind from configuration in {path}")
        kind = matching_kinds[0]
        expected_revision = current_revision if kind == "current" else baseline_revision
        if report.get("revision_label") != expected_revision:
            raise ReportValidationError(f"unexpected {kind} revision in {path}")
        expected = _expected_configuration(
            kind,
            repetitions=repetitions,
            playback_seconds=playback_seconds,
        )
        for key, expected_value in expected.items():
            if configuration.get(key) != expected_value:
                raise ReportValidationError(
                    f"configuration mismatch in {path}: {key}={configuration.get(key)!r}, expected {expected_value!r}"
                )
        indices = configuration.get("repetition_indices")
        if not isinstance(indices, list) or len(indices) != 1 or not isinstance(indices[0], int):
            raise ReportValidationError(f"invalid repetition_indices in {path}")
        repetition = indices[0]
        if not 1 <= repetition <= repetitions:
            raise ReportValidationError(f"out-of-range repetition in {path}: {repetition}")
        if report.get("harness_revision") != harness_revision:
            raise ReportValidationError(f"harness revision mismatch in {path}")
        provenance = report.get("provenance")
        if not isinstance(provenance, dict):
            raise ReportValidationError(f"missing provenance in {path}")
        try:
            source_attempt = int(str(provenance.get("run_attempt")))
            requested_attempt = int(run_attempt)
        except ValueError as error:
            raise ReportValidationError(f"invalid run attempt in {path}") from error
        if (
            provenance.get("run_id") != run_id
            or provenance.get("shard_id") != str(repetition)
            or not 1 <= source_attempt <= requested_attempt
        ):
            raise ReportValidationError(f"provenance mismatch in {path}")
        key = (kind, repetition, source_attempt)
        if key in candidates:
            raise ReportValidationError(f"duplicate {kind} repetition {repetition} in attempt {source_attempt}")
        runs = report.get("runs")
        expected_segments = expected["segment_counts"]
        if not isinstance(runs, list) or len(runs) != len(expected_segments):
            raise ReportValidationError(f"unexpected raw run count in {path}")
        actual_pairs = [(run.get("segment_count"), run.get("repetition")) for run in runs if isinstance(run, dict)]
        expected_pairs = [(segment_count, repetition) for segment_count in expected_segments]
        if sorted(actual_pairs) != sorted(expected_pairs):
            raise ReportValidationError(f"raw run coverage mismatch in {path}")
        for run in runs:
            if not isinstance(run, dict):
                raise ReportValidationError(f"invalid raw run in {path}")
            scenarios = run.get("scenarios")
            if not isinstance(scenarios, list):
                raise ReportValidationError(f"missing raw scenarios in {path}")
            if not all(isinstance(item, dict) and isinstance(item.get("name"), str) for item in scenarios):
                raise ReportValidationError(f"invalid raw scenario in {path}")
            scenario_names = [item["name"] for item in scenarios]
            if len(scenario_names) != len(SCENARIO_NAMES) or set(scenario_names) != set(SCENARIO_NAMES):
                raise ReportValidationError(f"raw scenario coverage mismatch in {path}")
            if not isinstance(run.get("contracts"), list) or not isinstance(run.get("contracts_passed"), bool):
                raise ReportValidationError(f"invalid contract results in {path}")
            if kind == "current" and not run["contracts_passed"]:
                raise ReportValidationError(f"current revision contract failure in {path}")
        if not isinstance(report.get("environment"), dict):
            raise ReportValidationError(f"missing environment information in {path}")
        candidates[key] = report

    reports: dict[tuple[str, int], dict[str, Any]] = {}
    source_attempts: dict[int, int] = {}
    for repetition in range(1, repetitions + 1):
        complete_attempts = sorted(
            attempt
            for attempt in range(1, int(run_attempt) + 1)
            if ("current", repetition, attempt) in candidates and ("baseline", repetition, attempt) in candidates
        )
        if not complete_attempts:
            raise ReportValidationError(f"missing complete shard pair for repetition {repetition}")
        source_attempt = complete_attempts[-1]
        source_attempts[repetition] = source_attempt
        for kind in ("current", "baseline"):
            reports[(kind, repetition)] = candidates[(kind, repetition, source_attempt)]
        if reports[("current", repetition)]["environment"] != reports[("baseline", repetition)]["environment"]:
            raise ReportValidationError(f"paired environment mismatch for repetition {repetition}")

    def merge(kind: str, revision: str) -> dict[str, Any]:
        shard_reports = [reports[(kind, repetition)] for repetition in range(1, repetitions + 1)]
        runs = [run for report in shard_reports for run in report["runs"]]
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "generated_at": shard_reports[-1].get("generated_at"),
            "revision_label": revision,
            "harness_revision": harness_revision,
            "provenance": {
                "run_id": run_id,
                "run_attempt": run_attempt,
                "source_attempts_by_repetition": {
                    str(repetition): source_attempts[repetition] for repetition in range(1, repetitions + 1)
                },
            },
            "configuration": {
                **_expected_configuration(kind, repetitions=repetitions, playback_seconds=playback_seconds),
                "repetitions": repetitions,
                "repetition_indices": list(range(1, repetitions + 1)),
            },
            "environments_by_repetition": {
                str(repetition): reports[(kind, repetition)]["environment"] for repetition in range(1, repetitions + 1)
            },
            "runs": sorted(runs, key=lambda run: (int(run["segment_count"]), int(run["repetition"]))),
            "summary": aggregate_runs(runs),
        }

    try:
        return merge("current", current_revision), merge("baseline", baseline_revision)
    except (KeyError, TypeError, ValueError, StopIteration) as error:
        raise ReportValidationError(f"invalid raw measurement data: {error}") from error


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and aggregate all GUI performance matrix shards.")
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, required=True)
    parser.add_argument("--playback-seconds", type=float, required=True)
    parser.add_argument("--current-revision", required=True)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--harness-revision", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    paths = sorted(args.input_dir.rglob("gui-performance-*.json"))
    try:
        current, baseline = aggregate_shards(
            paths,
            repetitions=args.repetitions,
            playback_seconds=args.playback_seconds,
            current_revision=args.current_revision,
            baseline_revision=args.baseline_revision,
            harness_revision=args.harness_revision,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
        )
    except ReportValidationError as error:
        print(f"GUI performance aggregation error: {error}")
        return 2
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, report in (("current", current), ("reference", baseline)):
        path = args.output_dir / f"gui-performance-{name}.json"
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"Aggregated GUI performance report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
