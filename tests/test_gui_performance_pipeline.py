from __future__ import annotations

import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts.aggregate_gui_performance import ReportValidationError, aggregate_shards
from scripts.compare_gui_performance import compare_reports, main as compare_main
from scripts.gui_performance_report import SCENARIO_NAMES
from scripts.plan_gui_performance import validate_inputs
from scripts.run_gui_performance import parse_args
from tests.workflow_contracts import load_workflow


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "gui-performance.yml"
CURRENT_SHA = "1" * 40
BASELINE_SHA = "2" * 40


def scenario(value: float) -> dict[str, object]:
    return {
        "action_elapsed_ms": value,
        "settled_elapsed_ms": value,
        "event_loop_latency_ms": {"p95_ms": value, "max_ms": value},
        "ui_playhead_lag_ms": {"p95_ms": value},
        "peak_rss_bytes": 1000,
        "python_qml_calls": {},
        "diagnostic_counts": {},
    }


def raw_run(segment_count: int, repetition: int, value: float = 10.0) -> dict[str, object]:
    return {
        "segment_count": segment_count,
        "repetition": repetition,
        "peak_rss_bytes": 1000,
        "scenarios": [{"name": name, **scenario(value)} for name in SCENARIO_NAMES],
        "contracts": [{"name": "sample_contract", "passed": True, "evidence": "ok"}],
        "contracts_passed": True,
    }


def shard_report(kind: str, repetition: int, *, repetitions: int = 3) -> dict[str, object]:
    current = kind == "current"
    segment_counts = [3000, 10000] if current else [3000]
    return {
        "schema_version": 1,
        "generated_at": "2026-09-07T00:00:00+00:00",
        "revision_label": CURRENT_SHA if current else BASELINE_SHA,
        "harness_revision": CURRENT_SHA,
        "provenance": {"run_id": "42", "run_attempt": "2", "shard_id": str(repetition)},
        "environment": {"runner": {"name": f"runner-{repetition}"}, "python": {"version": "3.10"}},
        "configuration": {
            "segment_counts": segment_counts,
            "repetitions": 1,
            "total_repetitions": repetitions,
            "repetition_indices": [repetition],
            "playback_seconds": 30.0,
            "settle_ms": 100,
            "contracts_enforced": current,
            "media_generated_at_runtime": True,
        },
        "runs": [raw_run(count, repetition) for count in segment_counts],
        "summary": {},
    }


class GuiPerformanceInputPlanTests(unittest.TestCase):
    def test_manual_repetition_bounds_create_complete_matrix(self) -> None:
        for repetitions in (1, 3, 10):
            with self.subTest(repetitions=repetitions):
                values = validate_inputs(str(repetitions), "30", "20", "false")
                self.assertEqual(values["matrix"], {"repetition": list(range(1, repetitions + 1))})

    def test_invalid_manual_inputs_are_rejected(self) -> None:
        invalid = (
            ("0", "30", "20", "false"),
            ("11", "30", "20", "false"),
            ("1.0", "30", "20", "false"),
            ("3", "nan", "20", "false"),
            ("3", "31", "20", "false"),
            ("3", "30", "inf", "false"),
            ("3", "30", "20", "yes"),
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_inputs(*values)

    def test_shard_index_must_describe_one_planned_repetition(self) -> None:
        args = parse_args(["--repetitions", "1", "--repetition-index", "3", "--total-repetitions", "3"])
        self.assertEqual(args.repetition_index, 3)
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parse_args(["--repetitions", "2", "--repetition-index", "1"])
            with self.assertRaises(SystemExit):
                parse_args(
                    ["--repetitions", "1", "--repetition-index", "4", "--total-repetitions", "3"]
                )


class GuiPerformanceAggregationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write_reports(self, reports: list[dict[str, object]]) -> list[Path]:
        paths = []
        for index, report in enumerate(reports):
            path = self.root / f"gui-performance-{index}.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            paths.append(path)
        return paths

    def complete_reports(self) -> list[dict[str, object]]:
        return [shard_report(kind, repetition) for repetition in range(1, 4) for kind in ("current", "baseline")]

    def test_aggregation_cli_imports_without_site_packages(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-S", "scripts/aggregate_gui_performance.py", "--help"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def aggregate(self, reports: list[dict[str, object]]):
        return aggregate_shards(
            self.write_reports(reports),
            repetitions=3,
            playback_seconds=30.0,
            current_revision=CURRENT_SHA,
            baseline_revision=BASELINE_SHA,
            harness_revision=CURRENT_SHA,
            run_id="42",
            run_attempt="2",
        )

    def test_all_raw_samples_are_aggregated_with_original_statistics(self) -> None:
        current, baseline = self.aggregate(self.complete_reports())

        self.assertEqual(len(current["runs"]), 6)
        self.assertEqual(len(baseline["runs"]), 3)
        self.assertEqual(current["summary"]["fixtures"]["3000"]["repetitions"], 3)
        self.assertEqual(current["configuration"]["repetition_indices"], [1, 2, 3])
        comparison = compare_reports(
            current,
            baseline,
            max_regression_percent=20,
            max_action_ms=45_000,
            max_event_loop_ms=3_000,
            max_playhead_lag_ms=500,
        )
        self.assertTrue(comparison["passed"])

    def test_missing_duplicate_and_mismatched_reports_are_rejected(self) -> None:
        cases: dict[str, list[dict[str, object]]] = {}
        complete = self.complete_reports()
        cases["missing"] = complete[:-1]
        cases["duplicate"] = [*complete, copy.deepcopy(complete[0])]
        mismatch = copy.deepcopy(complete)
        mismatch[0]["configuration"]["playback_seconds"] = 29.0
        cases["configuration"] = mismatch
        environment = copy.deepcopy(complete)
        environment[1]["environment"] = {"runner": {"name": "different"}}
        cases["environment"] = environment
        provenance = copy.deepcopy(complete)
        provenance[0]["provenance"]["run_attempt"] = "1"
        cases["provenance"] = provenance

        for name, reports in cases.items():
            with self.subTest(name=name), self.assertRaises(ReportValidationError):
                self.aggregate(reports)

    def test_invalid_json_is_rejected(self) -> None:
        path = self.root / "gui-performance-invalid.json"
        path.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(ReportValidationError, "could not read shard report"):
            aggregate_shards(
                [path],
                repetitions=1,
                playback_seconds=30.0,
                current_revision=CURRENT_SHA,
                baseline_revision=BASELINE_SHA,
                harness_revision=CURRENT_SHA,
                run_id="42",
                run_attempt="2",
            )

    def test_regression_report_and_fail_mode_keep_existing_meaning(self) -> None:
        reports = self.complete_reports()
        for report in reports:
            if report["revision_label"] == CURRENT_SHA:
                for run in report["runs"]:
                    run["scenarios"] = [
                        {"name": name, **scenario(20.0)} for name in SCENARIO_NAMES
                    ]
        current, baseline = self.aggregate(reports)
        current_path = self.root / "current.json"
        baseline_path = self.root / "baseline.json"
        output_path = self.root / "comparison.json"
        current_path.write_text(json.dumps(current), encoding="utf-8")
        baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
        arguments = [
            "--current",
            str(current_path),
            "--baseline",
            str(baseline_path),
            "--output",
            str(output_path),
            "--max-regression-percent",
            "20",
        ]

        with redirect_stdout(io.StringIO()):
            report_only_status = compare_main(arguments)
            failing_status = compare_main([*arguments, "--fail-on-regression"])
        self.assertEqual(report_only_status, 0)
        self.assertFalse(json.loads(output_path.read_text(encoding="utf-8"))["passed"])
        self.assertEqual(failing_status, 1)

    def test_rerun_reuses_successful_shards_and_selects_latest_complete_pair(self) -> None:
        reports = self.complete_reports()
        for report in reports:
            report["provenance"]["run_attempt"] = "1"
        rerun_current = shard_report("current", 2)
        rerun_baseline = shard_report("baseline", 2)
        for report in (rerun_current, rerun_baseline):
            report["provenance"]["run_attempt"] = "2"
            report["environment"] = {"runner": {"name": "rerun-runner"}, "python": {"version": "3.10"}}
        reports.extend((rerun_current, rerun_baseline))

        current, baseline = self.aggregate(reports)

        expected_attempts = {"1": 1, "2": 2, "3": 1}
        self.assertEqual(current["provenance"]["source_attempts_by_repetition"], expected_attempts)
        self.assertEqual(len(current["runs"]), 6)
        self.assertEqual(len(baseline["runs"]), 3)


class GuiPerformanceWorkflowContractTests(unittest.TestCase):
    def test_workflow_pairs_revisions_in_repetition_matrix_and_has_strict_gate(self) -> None:
        workflow = load_workflow(WORKFLOW)
        jobs = workflow["jobs"]
        benchmark = jobs["benchmark"]
        aggregate = jobs["aggregate"]

        self.assertEqual(benchmark["strategy"]["fail-fast"], False)
        self.assertEqual(benchmark["strategy"]["max-parallel"], 3)
        self.assertEqual(benchmark["needs"], "prepare")
        self.assertNotIn("continue-on-error", str(workflow))
        benchmark_commands = "\n".join(str(step.get("run", "")) for step in benchmark["steps"])
        self.assertIn("--repetitions 1", benchmark_commands)
        self.assertIn("--repetition-index", benchmark_commands)
        self.assertIn("--segment-count 10000", benchmark_commands)
        self.assertIn("--no-enforce-contracts", benchmark_commands)
        self.assertEqual(set(aggregate["needs"]), {"prepare", "benchmark"})
        self.assertIn("always()", aggregate["if"])
        aggregate_commands = "\n".join(str(step.get("run", "")) for step in aggregate["steps"])
        self.assertIn("aggregate_gui_performance.py", aggregate_commands)
        self.assertIn("BENCHMARK_JOB_RESULT", aggregate_commands)
        self.assertIn("compare_gui_performance.py", aggregate_commands)

    def test_workflow_tracks_pipeline_code_and_tests(self) -> None:
        paths = set(load_workflow(WORKFLOW)["on"]["pull_request"]["paths"])
        for expected in (
            "scripts/aggregate_gui_performance.py",
            "scripts/gui_performance_report.py",
            "scripts/plan_gui_performance.py",
            "scripts/run_gui_performance.py",
            "tests/test_gui_performance_pipeline.py",
        ):
            self.assertIn(expected, paths)


if __name__ == "__main__":
    unittest.main()
