from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.compare_gui_performance import compare_reports
from scripts.generate_large_gui_fixture import (
    FIXTURE_TIMESTAMP,
    generate_segments,
    write_fixture_project,
)
from scripts.run_gui_performance import SCENARIO_NAMES, aggregate_runs


REPO_ROOT = Path(__file__).resolve().parents[1]


def _scenario_sample(name: str, elapsed_ms: float) -> dict[str, object]:
    return {
        "name": name,
        "action_elapsed_ms": elapsed_ms,
        "settled_elapsed_ms": elapsed_ms + 100,
        "event_loop_latency_ms": {
            "count": 3,
            "p50_ms": 1.0,
            "p95_ms": 2.0,
            "max_ms": 3.0,
        },
        "ui_playhead_lag_ms": {
            "count": 3,
            "p50_ms": 10.0,
            "p95_ms": 20.0,
            "max_ms": 30.0,
        },
        "python_qml_calls": {"activeSubtitleSegments": 2},
        "diagnostic_counts": {"segment_views": 2},
        "media": {},
        "peak_rss_bytes": 100_000_000,
    }


def _performance_report(elapsed_ms: float) -> dict[str, object]:
    run = {
        "segment_count": 3_000,
        "scenarios": [_scenario_sample(name, elapsed_ms) for name in SCENARIO_NAMES],
        "contracts": [{"name": "fixture", "passed": True, "evidence": "ok"}],
        "contracts_passed": True,
        "peak_rss_bytes": 100_000_000,
    }
    return {
        "schema_version": 1,
        "revision_label": "fixture",
        "runs": [run],
        "summary": aggregate_runs([run]),
    }


def _performance_report_with_event_loop_maxima(
    maxima_ms: list[float],
) -> dict[str, object]:
    runs: list[dict[str, object]] = []
    for maximum_ms in maxima_ms:
        scenarios = [_scenario_sample(name, 100.0) for name in SCENARIO_NAMES]
        for scenario in scenarios:
            scenario["event_loop_latency_ms"]["max_ms"] = maximum_ms
        runs.append(
            {
                "segment_count": 3_000,
                "scenarios": scenarios,
                "contracts": [{"name": "fixture", "passed": True, "evidence": "ok"}],
                "contracts_passed": True,
                "peak_rss_bytes": 100_000_000,
            }
        )
    return {
        "schema_version": 1,
        "revision_label": "fixture",
        "runs": runs,
        "summary": aggregate_runs(runs),
    }


class GuiPerformanceFixtureTests(unittest.TestCase):
    def test_required_large_fixture_sizes_are_repeatable_and_varied(self) -> None:
        three_thousand = generate_segments(3_000)
        ten_thousand = generate_segments(10_000)

        self.assertEqual(len(three_thousand), 3_000)
        self.assertEqual(len(ten_thousand), 10_000)
        self.assertEqual(three_thousand, generate_segments(3_000))
        self.assertGreater(len({item["speaker"] for item in ten_thousand}), 1)
        self.assertTrue(any(item["words"] for item in ten_thousand))
        self.assertTrue(any("\n" in str(item["text"]) for item in ten_thousand))
        self.assertTrue(any(item["subtitle_font_family"] for item in ten_thousand))

    def test_project_writer_is_byte_repeatable_for_the_same_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media = root / "fixture.mp4"
            project_path = root / "large.subtitle-project.json"

            write_fixture_project(project_path, media_path=media, segment_count=25)
            first = project_path.read_bytes()
            write_fixture_project(project_path, media_path=media, segment_count=25)
            second = project_path.read_bytes()
            payload = json.loads(second)

        self.assertEqual(first, second)
        self.assertEqual(payload["created_at"], FIXTURE_TIMESTAMP)
        self.assertEqual(len(payload["segments"]), 25)
        self.assertEqual(len(payload["short_video"]["clips"]), 25)
        self.assertEqual(payload["video"]["duration_seconds"], payload["segments"][-1]["end"])

    def test_report_aggregation_and_regression_diagnostics_name_the_scenario(self) -> None:
        baseline = _performance_report(100.0)
        current = _performance_report(130.0)

        comparison = compare_reports(
            current,
            baseline,
            max_regression_percent=20.0,
            max_action_ms=45_000.0,
            max_event_loop_ms=3_000.0,
            max_playhead_lag_ms=500.0,
        )

        self.assertFalse(comparison["passed"])
        failed = comparison["failed_checks"]
        self.assertTrue(failed)
        self.assertEqual(failed[0]["fixture"], 3_000)
        self.assertIn(failed[0]["scenario"], SCENARIO_NAMES)
        self.assertEqual(failed[0]["metric"], "action_elapsed_ms")
        self.assertEqual(failed[0]["regression_percent"], 30.0)

    def test_absolute_budget_uses_worst_repetition_instead_of_median(self) -> None:
        current = _performance_report_with_event_loop_maxima([100.0, 100.0, 100_000.0])

        comparison = compare_reports(
            current,
            None,
            max_regression_percent=20.0,
            max_action_ms=45_000.0,
            max_event_loop_ms=3_000.0,
            max_playhead_lag_ms=500.0,
        )

        self.assertFalse(comparison["passed"])
        event_loop_failure = next(
            check for check in comparison["failed_checks"] if check["metric"] == "event_loop_max_ms"
        )
        self.assertEqual(event_loop_failure["current"], 100.0)
        self.assertEqual(event_loop_failure["current_worst"], 100_000.0)
        self.assertEqual(event_loop_failure["absolute_statistic"], "max")
        self.assertEqual(event_loop_failure["relative_statistic"], "p50")
        self.assertFalse(event_loop_failure["absolute_passed"])

    def test_heavy_windows_benchmark_is_separate_from_regular_ci(self) -> None:
        regular_ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        performance_ci = (REPO_ROOT / ".github" / "workflows" / "gui-performance.yml").read_text(encoding="utf-8")

        self.assertNotIn("run_gui_performance.py", regular_ci)
        self.assertIn("runs-on: windows-latest", performance_ci)
        self.assertIn("--segment-count 3000", performance_ci)
        self.assertIn("--segment-count 10000", performance_ci)
        self.assertIn('default: "b600e90"', performance_ci)
        self.assertIn("actions/upload-artifact", performance_ci)
        self.assertIn("git rev-parse --verify", performance_ci)
        self.assertIn("--end-of-options", performance_ci)
        self.assertIn("[double]::IsNaN", performance_ci)
        self.assertNotIn(
            'git worktree add --detach $referenceRoot "${{',
            performance_ci,
        )


if __name__ == "__main__":
    unittest.main()
