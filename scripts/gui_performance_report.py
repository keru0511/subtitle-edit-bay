from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypeGuard, TypedDict


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


class StatisticSummary(TypedDict):
    count: int
    p50: float
    p95: float
    max: float


class ScenarioSummary(TypedDict):
    action_elapsed_ms: StatisticSummary
    settled_elapsed_ms: StatisticSummary
    event_loop_p95_ms: StatisticSummary
    event_loop_max_ms: StatisticSummary
    ui_playhead_lag_p95_ms: StatisticSummary
    peak_rss_bytes: StatisticSummary
    python_qml_calls: dict[str, StatisticSummary]
    diagnostic_counts: dict[str, StatisticSummary]


class ContractSummary(TypedDict):
    passed: bool
    evidence: list[str]


class FixtureSummary(TypedDict):
    repetitions: int
    scenarios: dict[str, ScenarioSummary]
    contracts: dict[str, ContractSummary]
    peak_rss_bytes: StatisticSummary


class GuiPerformanceSummary(TypedDict):
    fixtures: dict[str, FixtureSummary]


@dataclass(frozen=True)
class _ScenarioSample:
    name: str
    action_elapsed_ms: float
    settled_elapsed_ms: float
    event_loop_p95_ms: float
    event_loop_max_ms: float
    ui_playhead_lag_p95_ms: float
    peak_rss_bytes: float
    python_qml_calls: dict[str, float]
    diagnostic_counts: dict[str, float]


@dataclass(frozen=True)
class _ContractSample:
    name: str
    passed: bool
    evidence: str


@dataclass(frozen=True)
class _RunSample:
    segment_count: int
    peak_rss_bytes: float
    scenarios: list[_ScenarioSample]
    contracts: list[_ContractSample]


def _is_object_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _is_string_object_mapping(value: object) -> TypeGuard[Mapping[str, object]]:
    return _is_object_mapping(value) and all(isinstance(key, str) for key in value)


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not _is_string_object_mapping(value):
        raise ValueError(f"{label}が辞書ではありません。")
    return value


def _items(value: object, label: str) -> list[object]:
    if not _is_object_list(value):
        raise ValueError(f"{label}が配列ではありません。")
    return value


def _number(value: object) -> float:
    if isinstance(value, (int, float, str, bytes, bytearray)):
        return float(value)
    raise TypeError("計測値を数値に変換できません。")


def _integer(value: object) -> int:
    if isinstance(value, (int, float, str, bytes, bytearray)):
        return int(value)
    raise TypeError("計測値を整数に変換できません。")


def _counts(value: object) -> dict[str, float]:
    return {name: _number(count) for name, count in _mapping(value, "計測回数").items()}


def _scenario(value: object) -> _ScenarioSample:
    item = _mapping(value, "シナリオ")
    latency = _mapping(item["event_loop_latency_ms"], "イベントループの遅延")
    playhead = _mapping(item.get("ui_playhead_lag_ms", {}), "再生位置の遅延")
    name = item["name"]
    if not isinstance(name, str):
        raise ValueError("シナリオ名が文字列ではありません。")
    return _ScenarioSample(
        name=name,
        action_elapsed_ms=_number(item["action_elapsed_ms"]),
        settled_elapsed_ms=_number(item["settled_elapsed_ms"]),
        event_loop_p95_ms=_number(latency["p95_ms"]),
        event_loop_max_ms=_number(latency["max_ms"]),
        ui_playhead_lag_p95_ms=_number(playhead.get("p95_ms", 0.0)),
        peak_rss_bytes=_number(item["peak_rss_bytes"]),
        python_qml_calls=_counts(item["python_qml_calls"]),
        diagnostic_counts=_counts(item["diagnostic_counts"]),
    )


def _contract(value: object) -> _ContractSample:
    item = _mapping(value, "契約結果")
    name = item["name"]
    if not isinstance(name, str):
        raise ValueError("契約名が文字列ではありません。")
    return _ContractSample(name=name, passed=bool(item["passed"]), evidence=str(item["evidence"]))


def _run(value: object) -> _RunSample:
    item = _mapping(value, "計測結果")
    return _RunSample(
        segment_count=_integer(item["segment_count"]),
        peak_rss_bytes=_number(item["peak_rss_bytes"]),
        scenarios=[_scenario(entry) for entry in _items(item["scenarios"], "シナリオ")],
        contracts=[_contract(entry) for entry in _items(item["contracts"], "契約結果")],
    )


def _nearest_rank_summary(
    values: Sequence[float],
    *,
    digits: int = 3,
) -> StatisticSummary:
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


def aggregate_runs(runs: Sequence[object]) -> GuiPerformanceSummary:
    grouped: dict[int, list[_RunSample]] = defaultdict(list)
    for raw_run in runs:
        run = _run(raw_run)
        grouped[run.segment_count].append(run)

    fixtures: dict[str, FixtureSummary] = {}
    for segment_count, fixture_runs in sorted(grouped.items()):
        scenarios: dict[str, ScenarioSummary] = {}
        for scenario_name in SCENARIO_NAMES:
            samples = [
                next(scenario for scenario in run.scenarios if scenario.name == scenario_name) for run in fixture_runs
            ]
            call_names = sorted({call_name for sample in samples for call_name in sample.python_qml_calls})
            diagnostic_names = sorted(
                {diagnostic_name for sample in samples for diagnostic_name in sample.diagnostic_counts}
            )
            scenarios[scenario_name] = {
                "action_elapsed_ms": _nearest_rank_summary([sample.action_elapsed_ms for sample in samples]),
                "settled_elapsed_ms": _nearest_rank_summary([sample.settled_elapsed_ms for sample in samples]),
                "event_loop_p95_ms": _nearest_rank_summary([sample.event_loop_p95_ms for sample in samples]),
                "event_loop_max_ms": _nearest_rank_summary([sample.event_loop_max_ms for sample in samples]),
                "ui_playhead_lag_p95_ms": _nearest_rank_summary([sample.ui_playhead_lag_p95_ms for sample in samples]),
                "peak_rss_bytes": _nearest_rank_summary(
                    [sample.peak_rss_bytes for sample in samples],
                    digits=0,
                ),
                "python_qml_calls": {
                    call_name: _nearest_rank_summary(
                        [sample.python_qml_calls.get(call_name, 0.0) for sample in samples],
                        digits=0,
                    )
                    for call_name in call_names
                },
                "diagnostic_counts": {
                    diagnostic_name: _nearest_rank_summary(
                        [sample.diagnostic_counts.get(diagnostic_name, 0.0) for sample in samples],
                        digits=0,
                    )
                    for diagnostic_name in diagnostic_names
                },
            }
        contracts: dict[str, ContractSummary] = {}
        contract_names = sorted({contract.name for run in fixture_runs for contract in run.contracts})
        for contract_name in contract_names:
            matching = [
                contract for run in fixture_runs for contract in run.contracts if contract.name == contract_name
            ]
            contracts[contract_name] = {
                "passed": all(contract.passed for contract in matching),
                "evidence": [contract.evidence for contract in matching],
            }
        fixtures[str(segment_count)] = {
            "repetitions": len(fixture_runs),
            "scenarios": scenarios,
            "contracts": contracts,
            "peak_rss_bytes": _nearest_rank_summary(
                [run.peak_rss_bytes for run in fixture_runs],
                digits=0,
            ),
        }
    return {"fixtures": fixtures}
