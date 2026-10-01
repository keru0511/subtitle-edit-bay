from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.gui_performance_report import (
    GuiPerformanceSummary,
    REPORT_SCHEMA_VERSION,
    SCENARIO_NAMES,
    aggregate_runs,
)
from src.data_boundary import is_object_list, is_string_object_mapping


SEGMENT_COUNTS = {"current": 3000, "large": 10000, "baseline": 3000}


class ReportValidationError(ValueError):
    pass


class _ExpectedConfiguration(TypedDict):
    segment_counts: list[int]
    repetitions: int
    total_repetitions: int
    playback_seconds: float
    settle_ms: int
    contracts_enforced: bool
    media_generated_at_runtime: bool


class _MergedConfiguration(_ExpectedConfiguration):
    repetition_indices: list[int]


class _MergedProvenance(TypedDict):
    run_id: str
    run_attempt: str
    source_attempts_by_repetition: dict[str, int]
    source_attempts_by_fixture: dict[str, dict[str, int]]


class MergedGuiReport(TypedDict):
    schema_version: int
    generated_at: object
    revision_label: str
    harness_revision: str
    provenance: _MergedProvenance
    configuration: _MergedConfiguration
    environments_by_fixture: dict[str, dict[str, Mapping[str, object]]]
    runs: list[Mapping[str, object]]
    summary: GuiPerformanceSummary


@dataclass(frozen=True)
class _RawRun:
    data: Mapping[str, object]
    segment_count: int
    repetition: int
    contracts_passed: bool


@dataclass(frozen=True)
class _Shard:
    data: Mapping[str, object]
    runs: list[_RawRun]
    environment: Mapping[str, object]


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not is_string_object_mapping(value):
        raise ReportValidationError(f"invalid {label}")
    return value


def _items(value: object, label: str) -> list[object]:
    if not is_object_list(value):
        raise ReportValidationError(f"invalid {label}")
    return value


def _load_report(path: Path) -> Mapping[str, object]:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReportValidationError(f"could not read shard report {path}: {error}") from error
    if not is_string_object_mapping(value) or value.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise ReportValidationError(f"unsupported shard report schema: {path}")
    return value


def _expected_configuration(
    kind: str,
    *,
    repetitions: int,
    playback_seconds: float,
) -> _ExpectedConfiguration:
    return {
        "segment_counts": [SEGMENT_COUNTS[kind]],
        "repetitions": 1,
        "total_repetitions": repetitions,
        "playback_seconds": playback_seconds,
        "settle_ms": 100,
        "contracts_enforced": kind != "baseline",
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
) -> tuple[MergedGuiReport, MergedGuiReport]:
    candidates: dict[tuple[str, int, int], _Shard] = {}
    for path in paths:
        report = _load_report(path)
        raw_configuration = report.get("configuration")
        if not is_string_object_mapping(raw_configuration):
            raise ReportValidationError(f"missing configuration in {path}")
        configuration = raw_configuration
        expected_by_kind = {
            kind: _expected_configuration(
                kind,
                repetitions=repetitions,
                playback_seconds=playback_seconds,
            )
            for kind in ("current", "large", "baseline")
        }
        matching_kinds = [
            kind
            for kind, candidate in expected_by_kind.items()
            if all(configuration.get(key) == value for key, value in candidate.items())
        ]
        if len(matching_kinds) != 1:
            raise ReportValidationError(f"could not identify shard kind from configuration in {path}")
        kind = matching_kinds[0]
        expected_revision = baseline_revision if kind == "baseline" else current_revision
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
        if not is_object_list(indices) or len(indices) != 1:
            raise ReportValidationError(f"invalid repetition_indices in {path}")
        repetition = indices[0]
        if not isinstance(repetition, int):
            raise ReportValidationError(f"invalid repetition_indices in {path}")
        if not 1 <= repetition <= repetitions:
            raise ReportValidationError(f"out-of-range repetition in {path}: {repetition}")
        if report.get("harness_revision") != harness_revision:
            raise ReportValidationError(f"harness revision mismatch in {path}")
        raw_provenance = report.get("provenance")
        if not is_string_object_mapping(raw_provenance):
            raise ReportValidationError(f"missing provenance in {path}")
        provenance = raw_provenance
        try:
            source_attempt = int(str(provenance.get("run_attempt")))
            requested_attempt = int(run_attempt)
        except ValueError as error:
            raise ReportValidationError(f"invalid run attempt in {path}") from error
        if (
            provenance.get("run_id") != run_id
            or provenance.get("shard_id") != f"{'large' if kind == 'large' else 'paired'}-{repetition}"
            or not 1 <= source_attempt <= requested_attempt
        ):
            raise ReportValidationError(f"provenance mismatch in {path}")
        candidate_key = (kind, repetition, source_attempt)
        if candidate_key in candidates:
            raise ReportValidationError(f"duplicate {kind} repetition {repetition} in attempt {source_attempt}")
        runs = report.get("runs")
        expected_segments = expected["segment_counts"]
        if not is_object_list(runs) or len(runs) != len(expected_segments):
            raise ReportValidationError(f"unexpected raw run count in {path}")
        parsed_runs: list[_RawRun] = []
        actual_pairs: list[tuple[int, int]] = []
        for raw_run in runs:
            if not is_string_object_mapping(raw_run):
                raise ReportValidationError(f"invalid raw run in {path}")
            segment_count = raw_run.get("segment_count")
            run_repetition = raw_run.get("repetition")
            if not isinstance(segment_count, int) or not isinstance(run_repetition, int):
                raise ReportValidationError(f"invalid raw run coverage in {path}")
            actual_pairs.append((segment_count, run_repetition))
            scenarios = raw_run.get("scenarios")
            if not is_object_list(scenarios):
                raise ReportValidationError(f"missing raw scenarios in {path}")
            scenario_names: list[str] = []
            for raw_scenario in scenarios:
                if not is_string_object_mapping(raw_scenario):
                    raise ReportValidationError(f"invalid raw scenario in {path}")
                scenario_name = raw_scenario.get("name")
                if not isinstance(scenario_name, str):
                    raise ReportValidationError(f"invalid raw scenario in {path}")
                scenario_names.append(scenario_name)
            if len(scenario_names) != len(SCENARIO_NAMES) or set(scenario_names) != set(SCENARIO_NAMES):
                raise ReportValidationError(f"raw scenario coverage mismatch in {path}")
            contracts = raw_run.get("contracts")
            contracts_passed = raw_run.get("contracts_passed")
            if not is_object_list(contracts) or not isinstance(contracts_passed, bool):
                raise ReportValidationError(f"invalid contract results in {path}")
            parsed_runs.append(_RawRun(raw_run, segment_count, run_repetition, contracts_passed))
        expected_pairs = [(segment_count, repetition) for segment_count in expected_segments]
        if sorted(actual_pairs) != sorted(expected_pairs):
            raise ReportValidationError(f"raw run coverage mismatch in {path}")
        environment = report.get("environment")
        if not is_string_object_mapping(environment):
            raise ReportValidationError(f"missing environment information in {path}")
        candidates[candidate_key] = _Shard(report, parsed_runs, environment)

    reports: dict[tuple[str, int], _Shard] = {}
    source_attempts: dict[str, dict[str, int]] = {kind: {} for kind in ("current", "large", "baseline")}
    for repetition in range(1, repetitions + 1):
        for kind in ("current", "large", "baseline"):
            attempts = [
                attempt
                for candidate_kind, index, attempt in candidates
                if candidate_kind == kind and index == repetition
            ]
            if not attempts:
                raise ReportValidationError(f"missing {kind} report for repetition {repetition}")
            attempt = max(attempts)
            reports[(kind, repetition)] = candidates[(kind, repetition, attempt)]
            if kind != "baseline" and any(not run.contracts_passed for run in reports[(kind, repetition)].runs):
                raise ReportValidationError(f"current revision contract failure for {kind} repetition {repetition}")
            source_attempts[kind][str(repetition)] = attempt
        if source_attempts["current"][str(repetition)] != source_attempts["baseline"][str(repetition)]:
            raise ReportValidationError(f"incomplete latest shard pair for repetition {repetition}")
        if reports[("current", repetition)].environment != reports[("baseline", repetition)].environment:
            raise ReportValidationError(f"paired environment mismatch for repetition {repetition}")

    def run_order(run: _RawRun) -> tuple[int, int]:
        return run.segment_count, run.repetition

    def merge(kind: str, revision: str) -> MergedGuiReport:
        kinds = ("current", "large") if kind == "current" else ("baseline",)
        shard_reports = [reports[(part, repetition)] for part in kinds for repetition in range(1, repetitions + 1)]
        runs = [run for report in shard_reports for run in report.runs]
        sorted_runs = sorted(runs, key=run_order)
        merged_runs = [run.data for run in sorted_runs]
        configuration: _MergedConfiguration = {
            "segment_counts": [3000, 10000] if kind == "current" else [3000],
            "repetitions": repetitions,
            "total_repetitions": repetitions,
            "repetition_indices": list(range(1, repetitions + 1)),
            "playback_seconds": playback_seconds,
            "settle_ms": 100,
            "contracts_enforced": kind != "baseline",
            "media_generated_at_runtime": True,
        }
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "generated_at": shard_reports[-1].data.get("generated_at"),
            "revision_label": revision,
            "harness_revision": harness_revision,
            "provenance": {
                "run_id": run_id,
                "run_attempt": run_attempt,
                "source_attempts_by_repetition": source_attempts[kind],
                "source_attempts_by_fixture": {str(SEGMENT_COUNTS[part]): source_attempts[part] for part in kinds},
            },
            "configuration": configuration,
            "environments_by_fixture": {
                str(SEGMENT_COUNTS[part]): {
                    str(repetition): reports[(part, repetition)].environment for repetition in range(1, repetitions + 1)
                }
                for part in kinds
            },
            "runs": merged_runs,
            "summary": aggregate_runs(merged_runs),
        }

    try:
        return merge("current", current_revision), merge("baseline", baseline_revision)
    except (KeyError, TypeError, ValueError, StopIteration) as error:
        raise ReportValidationError(f"invalid raw measurement data: {error}") from error


class _AggregateArgs(argparse.Namespace):
    input_dir: Path = Path(".")
    output_dir: Path = Path(".")
    repetitions: int = 1
    playback_seconds: float = 30.0
    current_revision: str = ""
    baseline_revision: str = ""
    harness_revision: str = ""
    run_id: str = ""
    run_attempt: str = ""


def parse_args(argv: Sequence[str] | None = None) -> _AggregateArgs:
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
    return parser.parse_args(argv, namespace=_AggregateArgs())


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
