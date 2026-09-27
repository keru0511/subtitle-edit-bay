from __future__ import annotations

import argparse
import json
import math
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Sequence, TypeGuard, TypedDict


DEFAULT_MAX_REGRESSION_PERCENT = 20.0
DEFAULT_MAX_ACTION_MS = 45_000.0
DEFAULT_MAX_EVENT_LOOP_MS = 3_000.0
DEFAULT_MAX_PLAYHEAD_LAG_MS = 500.0


class PerformanceCheck(TypedDict):
    fixture: int
    scenario: str
    metric: str
    current: float
    current_worst: float
    absolute_statistic: str
    absolute_limit: float
    absolute_passed: bool
    baseline: float | None
    relative_statistic: str
    regression_percent: float | None
    relative_limit_percent: float
    relative_passed: bool
    passed: bool


class ComparisonLimits(TypedDict):
    max_regression_percent: float
    max_action_ms: float
    max_event_loop_ms: float
    max_playhead_lag_ms: float


class ComparisonReport(TypedDict):
    schema_version: int
    current_revision: str
    baseline_revision: str | None
    limits: ComparisonLimits
    compared_fixture_counts: list[int]
    passed: bool
    failed_checks: list[PerformanceCheck]
    checks: list[PerformanceCheck]


def _string_mapping(value: object) -> TypeGuard[Mapping[str, object]]:
    return isinstance(value, Mapping) and all(isinstance(key, str) for key in value)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not _string_mapping(value):
        raise ValueError(f"invalid {label} in GUI performance report")
    return value


def _number(value: object) -> float:
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        raise ValueError("invalid numeric GUI performance value")
    return float(value)


def _revision(value: object) -> str:
    return value if isinstance(value, str) else str(value)


def _read_report(path: Path) -> Mapping[str, object]:
    try:
        report: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read GUI performance report {path}: {error}") from error
    if not _string_mapping(report) or report.get("schema_version") != 1:
        raise ValueError(f"unsupported GUI performance report schema: {path}")
    return report


def _summary_value(scenario: Mapping[str, object], metric: str, statistic: str) -> float:
    value = _number(_mapping(scenario[metric], metric)[statistic])
    if not math.isfinite(value):
        raise ValueError(f"non-finite GUI performance value: {metric}.{statistic}")
    return value


def compare_reports(
    current: object,
    baseline: object | None,
    *,
    max_regression_percent: float,
    max_action_ms: float,
    max_event_loop_ms: float,
    max_playhead_lag_ms: float,
) -> ComparisonReport:
    current_data = _mapping(current, "current report")
    current_fixtures = _mapping(_mapping(current_data["summary"], "current summary")["fixtures"], "current fixtures")
    baseline_data = _mapping(baseline, "baseline report") if baseline is not None else None
    baseline_fixtures: Mapping[str, object] = (
        _mapping(_mapping(baseline_data["summary"], "baseline summary")["fixtures"], "baseline fixtures")
        if baseline_data is not None
        else {}
    )
    checks: list[PerformanceCheck] = []

    absolute_limits = {
        "action_elapsed_ms": max_action_ms,
        "event_loop_max_ms": max_event_loop_ms,
        "ui_playhead_lag_p95_ms": max_playhead_lag_ms,
    }
    for fixture_name in sorted(current_fixtures, key=int):
        raw_fixture = current_fixtures[fixture_name]
        fixture = _mapping(raw_fixture, "fixture")
        baseline_fixture = baseline_fixtures.get(fixture_name)
        current_scenarios = _mapping(fixture["scenarios"], "current scenarios")
        for scenario_name, raw_scenario in current_scenarios.items():
            scenario = _mapping(raw_scenario, "current scenario")
            baseline_scenario = (
                _mapping(_mapping(baseline_fixture, "baseline fixture")["scenarios"], "baseline scenarios").get(
                    scenario_name
                )
                if baseline_fixture is not None
                else None
            )
            for metric, absolute_limit in absolute_limits.items():
                # Safety ceilings must catch even one frozen run. Relative
                # comparisons stay on p50 so runner noise does not dominate.
                current_value = _summary_value(scenario, metric, "p50")
                current_worst = _summary_value(scenario, metric, "max")
                absolute_passed = current_worst <= absolute_limit
                baseline_value = (
                    _summary_value(_mapping(baseline_scenario, "baseline scenario"), metric, "p50")
                    if baseline_scenario is not None
                    else None
                )
                regression_percent = None
                relative_passed = True
                if baseline_value is not None and baseline_value > 0.0:
                    regression_percent = (current_value - baseline_value) / baseline_value * 100.0
                    relative_passed = regression_percent <= max_regression_percent
                checks.append(
                    {
                        "fixture": int(fixture_name),
                        "scenario": scenario_name,
                        "metric": metric,
                        "current": round(current_value, 3),
                        "current_worst": round(current_worst, 3),
                        "absolute_statistic": "max",
                        "absolute_limit": absolute_limit,
                        "absolute_passed": absolute_passed,
                        "baseline": round(baseline_value, 3) if baseline_value is not None else None,
                        "relative_statistic": "p50",
                        "regression_percent": (
                            round(regression_percent, 3) if regression_percent is not None else None
                        ),
                        "relative_limit_percent": max_regression_percent,
                        "relative_passed": relative_passed,
                        "passed": absolute_passed and relative_passed,
                    }
                )

    failed_checks = [check for check in checks if not check["passed"]]
    return {
        "schema_version": 1,
        "current_revision": _revision(current_data.get("revision_label", "unknown")),
        "baseline_revision": _revision(baseline_data.get("revision_label", "none")) if baseline_data else None,
        "limits": {
            "max_regression_percent": max_regression_percent,
            "max_action_ms": max_action_ms,
            "max_event_loop_ms": max_event_loop_ms,
            "max_playhead_lag_ms": max_playhead_lag_ms,
        },
        "compared_fixture_counts": sorted(int(name) for name in set(current_fixtures) & set(baseline_fixtures)),
        "passed": not failed_checks,
        "failed_checks": failed_checks,
        "checks": checks,
    }


def markdown_summary(comparison: ComparisonReport) -> str:
    status = "PASS" if comparison["passed"] else "FAIL"
    lines = [
        "### GUI performance comparison",
        "",
        f"Result: **{status}**",
        "",
        f"Current: `{comparison['current_revision']}`  ",
        f"Baseline: `{comparison['baseline_revision'] or 'not supplied'}`",
        "",
        "| Fixture | Scenario | Metric | Current p50 | Current max | Baseline p50 | Change | Limit | Result |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for check in comparison["checks"]:
        baseline_value = check["baseline"]
        regression_percent = check["regression_percent"]
        baseline = "—" if baseline_value is None else f"{baseline_value:.3f}"
        change = "—" if regression_percent is None else f"{regression_percent:+.1f}%"
        result = "PASS" if check["passed"] else "FAIL"
        lines.append(
            f"| {check['fixture']} | `{check['scenario']}` | `{check['metric']}` | "
            f"{check['current']:.3f} | {check['current_worst']:.3f} | {baseline} | {change} | "
            f"{check['absolute_limit']:.1f} / +{check['relative_limit_percent']:.1f}% | {result} |"
        )
    return "\n".join(lines) + "\n"


class _CompareArgs(argparse.Namespace):
    current: Path = Path(".")
    baseline: Path | None = None
    output: Path = Path(".")
    max_regression_percent: float = DEFAULT_MAX_REGRESSION_PERCENT
    max_action_ms: float = DEFAULT_MAX_ACTION_MS
    max_event_loop_ms: float = DEFAULT_MAX_EVENT_LOOP_MS
    max_playhead_lag_ms: float = DEFAULT_MAX_PLAYHEAD_LAG_MS
    fail_on_regression: bool = False


def parse_args(argv: Sequence[str] | None = None) -> _CompareArgs:
    parser = argparse.ArgumentParser(
        description="Evaluate absolute GUI latency limits and relative baseline regressions.",
    )
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--max-regression-percent",
        type=float,
        default=DEFAULT_MAX_REGRESSION_PERCENT,
    )
    parser.add_argument("--max-action-ms", type=float, default=DEFAULT_MAX_ACTION_MS)
    parser.add_argument(
        "--max-event-loop-ms",
        type=float,
        default=DEFAULT_MAX_EVENT_LOOP_MS,
    )
    parser.add_argument(
        "--max-playhead-lag-ms",
        type=float,
        default=DEFAULT_MAX_PLAYHEAD_LAG_MS,
    )
    parser.add_argument("--fail-on-regression", action="store_true")
    args = parser.parse_args(argv, namespace=_CompareArgs())
    if not math.isfinite(args.max_regression_percent) or args.max_regression_percent < 0:
        parser.error("--max-regression-percent must be finite and non-negative")
    for name, value in (
        ("max_action_ms", args.max_action_ms),
        ("max_event_loop_ms", args.max_event_loop_ms),
        ("max_playhead_lag_ms", args.max_playhead_lag_ms),
    ):
        if not math.isfinite(value) or value <= 0:
            parser.error(f"--{name.replace('_', '-')} must be finite and positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    current = _read_report(args.current)
    baseline = _read_report(args.baseline) if args.baseline else None
    comparison = compare_reports(
        current,
        baseline,
        max_regression_percent=args.max_regression_percent,
        max_action_ms=args.max_action_ms,
        max_event_loop_ms=args.max_event_loop_ms,
        max_playhead_lag_ms=args.max_playhead_lag_ms,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = markdown_summary(comparison)
    print(summary, end="")
    github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if github_summary:
        with Path(github_summary).open("a", encoding="utf-8") as output:
            output.write(summary)
    return 1 if args.fail_on_regression and not comparison["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
