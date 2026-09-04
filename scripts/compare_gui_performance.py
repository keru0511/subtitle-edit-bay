from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any, Sequence


DEFAULT_MAX_REGRESSION_PERCENT = 20.0
DEFAULT_MAX_ACTION_MS = 45_000.0
DEFAULT_MAX_EVENT_LOOP_MS = 3_000.0
DEFAULT_MAX_PLAYHEAD_LAG_MS = 500.0


def _read_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read GUI performance report {path}: {error}") from error
    if not isinstance(report, dict) or report.get("schema_version") != 1:
        raise ValueError(f"unsupported GUI performance report schema: {path}")
    return report


def _summary_value(scenario: dict[str, Any], metric: str, statistic: str) -> float:
    value = float(scenario[metric][statistic])
    if not math.isfinite(value):
        raise ValueError(f"non-finite GUI performance value: {metric}.{statistic}")
    return value


def compare_reports(
    current: dict[str, Any],
    baseline: dict[str, Any] | None,
    *,
    max_regression_percent: float,
    max_action_ms: float,
    max_event_loop_ms: float,
    max_playhead_lag_ms: float,
) -> dict[str, Any]:
    current_fixtures = current["summary"]["fixtures"]
    baseline_fixtures = baseline["summary"]["fixtures"] if baseline is not None else {}
    checks: list[dict[str, Any]] = []

    absolute_limits = {
        "action_elapsed_ms": max_action_ms,
        "event_loop_max_ms": max_event_loop_ms,
        "ui_playhead_lag_p95_ms": max_playhead_lag_ms,
    }
    for fixture_name, fixture in sorted(current_fixtures.items(), key=lambda item: int(item[0])):
        baseline_fixture = baseline_fixtures.get(fixture_name)
        for scenario_name, scenario in fixture["scenarios"].items():
            baseline_scenario = (
                baseline_fixture["scenarios"].get(scenario_name) if baseline_fixture is not None else None
            )
            for metric, absolute_limit in absolute_limits.items():
                # Safety ceilings must catch even one frozen run. Relative
                # comparisons stay on p50 so runner noise does not dominate.
                current_value = _summary_value(scenario, metric, "p50")
                current_worst = _summary_value(scenario, metric, "max")
                absolute_passed = current_worst <= absolute_limit
                baseline_value = (
                    _summary_value(baseline_scenario, metric, "p50") if baseline_scenario is not None else None
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
        "current_revision": current.get("revision_label", "unknown"),
        "baseline_revision": baseline.get("revision_label", "none") if baseline else None,
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


def markdown_summary(comparison: dict[str, Any]) -> str:
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
        baseline = "—" if check["baseline"] is None else f"{check['baseline']:.3f}"
        change = "—" if check["regression_percent"] is None else f"{check['regression_percent']:+.1f}%"
        result = "PASS" if check["passed"] else "FAIL"
        lines.append(
            f"| {check['fixture']} | `{check['scenario']}` | `{check['metric']}` | "
            f"{check['current']:.3f} | {check['current_worst']:.3f} | {baseline} | {change} | "
            f"{check['absolute_limit']:.1f} / +{check['relative_limit_percent']:.1f}% | {result} |"
        )
    return "\n".join(lines) + "\n"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
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
    args = parser.parse_args(argv)
    if not math.isfinite(args.max_regression_percent) or args.max_regression_percent < 0:
        parser.error("--max-regression-percent must be finite and non-negative")
    for name in ("max_action_ms", "max_event_loop_ms", "max_playhead_lag_ms"):
        if not math.isfinite(getattr(args, name)) or getattr(args, name) <= 0:
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
