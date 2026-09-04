from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.generate_large_gui_fixture import (
    DEFAULT_MEDIA_DURATION_SECONDS,
    DEFAULT_SEGMENT_COUNTS,
    generate_synthetic_media,
    write_fixture_project,
)


REPORT_SCHEMA_VERSION = 1
SCENARIO_NAMES = (
    "project_initial_interactive",
    "main_preview_continuous_playback",
    "editor_open_close_without_media_reload",
    "list_and_timeline_selection",
    "subtitle_text_time_speaker_font_edit",
    "playback_selection_and_timeline_follow",
    "short_mode_selection_reorder_delete_settings",
    "short_visual_update_preserves_playback",
)


def _nearest_rank_summary(values: Sequence[float], *, digits: int = 3) -> dict[str, float | int]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {"count": 0, "p50": 0.0, "p95": 0.0, "max": 0.0}

    def percentile(fraction: float) -> float:
        rank = max(1, min(len(ordered), math.ceil(len(ordered) * fraction)))
        return ordered[rank - 1]

    return {
        "count": len(ordered),
        "p50": round(percentile(0.50), digits),
        "p95": round(percentile(0.95), digits),
        "max": round(ordered[-1], digits),
    }


def aggregate_runs(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        grouped[int(run["segment_count"])].append(run)

    fixtures: dict[str, Any] = {}
    for segment_count, fixture_runs in sorted(grouped.items()):
        scenarios: dict[str, Any] = {}
        for scenario_name in SCENARIO_NAMES:
            samples = [
                next(scenario for scenario in run["scenarios"] if scenario["name"] == scenario_name)
                for run in fixture_runs
            ]
            call_names = sorted({call_name for sample in samples for call_name in sample["python_qml_calls"]})
            diagnostic_names = sorted(
                {diagnostic_name for sample in samples for diagnostic_name in sample["diagnostic_counts"]}
            )
            scenarios[scenario_name] = {
                "action_elapsed_ms": _nearest_rank_summary([float(sample["action_elapsed_ms"]) for sample in samples]),
                "settled_elapsed_ms": _nearest_rank_summary(
                    [float(sample["settled_elapsed_ms"]) for sample in samples]
                ),
                "event_loop_p95_ms": _nearest_rank_summary(
                    [float(sample["event_loop_latency_ms"]["p95_ms"]) for sample in samples]
                ),
                "event_loop_max_ms": _nearest_rank_summary(
                    [float(sample["event_loop_latency_ms"]["max_ms"]) for sample in samples]
                ),
                "ui_playhead_lag_p95_ms": _nearest_rank_summary(
                    [float(sample.get("ui_playhead_lag_ms", {}).get("p95_ms", 0.0)) for sample in samples]
                ),
                "peak_rss_bytes": _nearest_rank_summary(
                    [float(sample["peak_rss_bytes"]) for sample in samples], digits=0
                ),
                "python_qml_calls": {
                    call_name: _nearest_rank_summary(
                        [float(sample["python_qml_calls"].get(call_name, 0)) for sample in samples], digits=0
                    )
                    for call_name in call_names
                },
                "diagnostic_counts": {
                    diagnostic_name: _nearest_rank_summary(
                        [float(sample["diagnostic_counts"].get(diagnostic_name, 0)) for sample in samples], digits=0
                    )
                    for diagnostic_name in diagnostic_names
                },
            }
        contracts: dict[str, Any] = {}
        contract_names = sorted({contract["name"] for run in fixture_runs for contract in run["contracts"]})
        for contract_name in contract_names:
            matching = [
                contract for run in fixture_runs for contract in run["contracts"] if contract["name"] == contract_name
            ]
            contracts[contract_name] = {
                "passed": all(bool(contract["passed"]) for contract in matching),
                "evidence": [str(contract["evidence"]) for contract in matching],
            }
        fixtures[str(segment_count)] = {
            "repetitions": len(fixture_runs),
            "scenarios": scenarios,
            "contracts": contracts,
            "peak_rss_bytes": _nearest_rank_summary([float(run["peak_rss_bytes"]) for run in fixture_runs], digits=0),
        }
    return {"fixtures": fixtures}


def _run_worker(args: argparse.Namespace) -> int:
    from tests.gui_performance_scenarios import GuiPerformanceScenarioRunner

    runner = GuiPerformanceScenarioRunner(
        args.project,
        playback_seconds=args.playback_seconds,
        settle_ms=args.settle_ms,
    )
    result = runner.run()
    args.worker_output.parent.mkdir(parents=True, exist_ok=True)
    args.worker_output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.enforce_contracts and not result["contracts_passed"]:
        return 2
    return 0


def _worker_command(
    args: argparse.Namespace,
    *,
    project_path: Path,
    worker_output: Path,
) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--project",
        str(project_path),
        "--worker-output",
        str(worker_output),
        "--playback-seconds",
        str(args.playback_seconds),
        "--settle-ms",
        str(args.settle_ms),
    ]
    command.append("--enforce-contracts" if args.enforce_contracts else "--no-enforce-contracts")
    return command


def _run_controller(args: argparse.Namespace) -> int:
    output_path = args.output.resolve()
    fixture_dir = (args.fixture_dir or output_path.parent / "gui-performance-fixtures").resolve()
    fixture_dir.mkdir(parents=True, exist_ok=True)
    media_path = fixture_dir / "synthetic-gui-performance.mp4"
    generate_synthetic_media(
        media_path,
        duration_seconds=max(DEFAULT_MEDIA_DURATION_SECONDS, args.playback_seconds + 3.0),
    )
    project_paths = {
        segment_count: write_fixture_project(
            fixture_dir / f"large-{segment_count}.subtitle-project.json",
            media_path=media_path,
            segment_count=segment_count,
        )
        for segment_count in args.segment_counts
    }

    worker_dir = fixture_dir / "worker-results"
    worker_dir.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []
    contract_failure = False
    for segment_count, project_path in project_paths.items():
        for repetition in range(1, args.repetitions + 1):
            worker_output = worker_dir / f"{segment_count}-{repetition}.json"
            completed = subprocess.run(
                _worker_command(
                    args,
                    project_path=project_path,
                    worker_output=worker_output,
                ),
                cwd=REPO_ROOT,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if completed.returncode not in {0, 2}:
                raise RuntimeError(
                    f"GUI performance worker failed for {segment_count} subtitles "
                    f"(repetition {repetition}) with exit code {completed.returncode}."
                )
            run = json.loads(worker_output.read_text(encoding="utf-8"))
            run["repetition"] = repetition
            runs.append(run)
            contract_failure = contract_failure or not bool(run["contracts_passed"])

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "revision_label": args.revision_label,
        "configuration": {
            "segment_counts": args.segment_counts,
            "repetitions": args.repetitions,
            "playback_seconds": args.playback_seconds,
            "settle_ms": args.settle_ms,
            "contracts_enforced": args.enforce_contracts,
            "media_generated_at_runtime": True,
        },
        "runs": runs,
        "summary": aggregate_runs(runs),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"GUI performance report: {output_path}")
    for segment_count in args.segment_counts:
        fixture = report["summary"]["fixtures"][str(segment_count)]
        failed = [name for name, value in fixture["contracts"].items() if not value["passed"]]
        status = "PASS" if not failed else f"FAIL ({', '.join(failed)})"
        print(f"- {segment_count} subtitles: {status}")
    return 1 if args.enforce_contracts and contract_failure else 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run repeatable Qt/QML and Qt Multimedia GUI performance scenarios.",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--project", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--segment-count",
        type=int,
        action="append",
        dest="segment_counts",
        help="Subtitle count; may be specified more than once (default: 3000 and 10000).",
    )
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--playback-seconds", type=float, default=30.0)
    parser.add_argument("--settle-ms", type=int, default=100)
    parser.add_argument("--output", type=Path, default=Path("artifacts/gui-performance.json"))
    parser.add_argument("--fixture-dir", type=Path)
    parser.add_argument("--revision-label", default=os.environ.get("GITHUB_SHA", "local"))
    parser.add_argument(
        "--enforce-contracts",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    args = parser.parse_args(argv)
    args.segment_counts = args.segment_counts or list(DEFAULT_SEGMENT_COUNTS)
    if args.repetitions <= 0:
        parser.error("--repetitions must be positive")
    if not 0.1 <= args.playback_seconds <= 30.0:
        parser.error("--playback-seconds must be between 0.1 and 30")
    if args.settle_ms < 0:
        parser.error("--settle-ms must not be negative")
    if any(count <= 0 for count in args.segment_counts):
        parser.error("--segment-count must be positive")
    if args.worker and (args.project is None or args.worker_output is None):
        parser.error("--worker requires --project and --worker-output")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    return _run_worker(args) if args.worker else _run_controller(args)


if __name__ == "__main__":
    raise SystemExit(main())
