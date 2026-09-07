from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
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
from scripts.gui_performance_report import REPORT_SCHEMA_VERSION, aggregate_runs


def _command_version(command: str) -> str | None:
    try:
        completed = subprocess.run(
            [command, "-version"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return None
    output = completed.stdout or completed.stderr
    return output.splitlines()[0].strip() if output else None


def environment_info() -> dict[str, Any]:
    packages: dict[str, str | None] = {}
    for name in ("PySide6", "shiboken6"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": packages,
        "ffmpeg": _command_version("ffmpeg"),
        "ffprobe": _command_version("ffprobe"),
        "runner": {
            "os": os.environ.get("RUNNER_OS"),
            "arch": os.environ.get("RUNNER_ARCH"),
            "name": os.environ.get("RUNNER_NAME"),
            "image_os": os.environ.get("ImageOS"),
            "image_version": os.environ.get("ImageVersion"),
        },
    }


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
        for local_repetition in range(1, args.repetitions + 1):
            repetition = (
                args.repetition_index
                if args.repetition_index is not None
                else local_repetition
            )
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
        "harness_revision": args.harness_revision,
        "provenance": {
            "run_id": args.run_id,
            "run_attempt": args.run_attempt,
            "shard_id": args.shard_id,
        },
        "environment": environment_info(),
        "configuration": {
            "segment_counts": args.segment_counts,
            "repetitions": args.repetitions,
            "total_repetitions": args.total_repetitions,
            "repetition_indices": sorted({int(run["repetition"]) for run in runs}),
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
    parser.add_argument(
        "--repetition-index",
        type=int,
        help="Global repetition number for a single-repetition CI shard.",
    )
    parser.add_argument(
        "--total-repetitions",
        type=int,
        help="Total repetitions planned across all CI shards.",
    )
    parser.add_argument("--playback-seconds", type=float, default=30.0)
    parser.add_argument("--settle-ms", type=int, default=100)
    parser.add_argument("--output", type=Path, default=Path("artifacts/gui-performance.json"))
    parser.add_argument("--fixture-dir", type=Path)
    parser.add_argument("--revision-label", default=os.environ.get("GITHUB_SHA", "local"))
    parser.add_argument("--harness-revision", default=os.environ.get("GITHUB_SHA", "local"))
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    parser.add_argument("--run-attempt", default=os.environ.get("GITHUB_RUN_ATTEMPT", "1"))
    parser.add_argument("--shard-id", default="local")
    parser.add_argument(
        "--enforce-contracts",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    args = parser.parse_args(argv)
    args.segment_counts = args.segment_counts or list(DEFAULT_SEGMENT_COUNTS)
    args.total_repetitions = args.total_repetitions or args.repetitions
    if args.repetitions <= 0:
        parser.error("--repetitions must be positive")
    if args.total_repetitions <= 0:
        parser.error("--total-repetitions must be positive")
    if args.repetition_index is not None:
        if args.repetitions != 1:
            parser.error("--repetition-index requires --repetitions 1")
        if not 1 <= args.repetition_index <= args.total_repetitions:
            parser.error("--repetition-index must be within --total-repetitions")
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
