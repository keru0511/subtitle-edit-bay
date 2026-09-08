from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Sequence


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


def _nearest_rank_summary(
    values: Sequence[float],
    *,
    digits: int = 3,
) -> dict[str, float | int]:
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
                    [float(sample["peak_rss_bytes"]) for sample in samples],
                    digits=0,
                ),
                "python_qml_calls": {
                    call_name: _nearest_rank_summary(
                        [float(sample["python_qml_calls"].get(call_name, 0)) for sample in samples],
                        digits=0,
                    )
                    for call_name in call_names
                },
                "diagnostic_counts": {
                    diagnostic_name: _nearest_rank_summary(
                        [float(sample["diagnostic_counts"].get(diagnostic_name, 0)) for sample in samples],
                        digits=0,
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
            "peak_rss_bytes": _nearest_rank_summary(
                [float(run["peak_rss_bytes"]) for run in fixture_runs],
                digits=0,
            ),
        }
    return {"fixtures": fixtures}
