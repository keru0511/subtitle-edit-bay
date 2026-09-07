from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path
from typing import Sequence


def validate_inputs(
    repetitions: str,
    playback_seconds: str,
    max_regression_percent: str,
    fail_on_regression: str,
) -> dict[str, object]:
    try:
        parsed_repetitions = int(repetitions)
    except ValueError as error:
        raise ValueError("repetitions must be an integer from 1 through 10") from error
    if str(parsed_repetitions) != repetitions.strip() or not 1 <= parsed_repetitions <= 10:
        raise ValueError("repetitions must be an integer from 1 through 10")

    try:
        parsed_playback = float(playback_seconds)
    except ValueError as error:
        raise ValueError("playback_seconds must be from 0.1 through 30") from error
    if not math.isfinite(parsed_playback) or not 0.1 <= parsed_playback <= 30:
        raise ValueError("playback_seconds must be from 0.1 through 30")

    try:
        parsed_regression = float(max_regression_percent)
    except ValueError as error:
        raise ValueError("max_regression_percent must be from 0 through 1000") from error
    if not math.isfinite(parsed_regression) or not 0 <= parsed_regression <= 1000:
        raise ValueError("max_regression_percent must be from 0 through 1000")

    parsed_fail = fail_on_regression.strip().lower()
    if parsed_fail not in {"true", "false"}:
        raise ValueError("fail_on_regression must be true or false")
    return {
        "repetitions": parsed_repetitions,
        "playback_seconds": format(parsed_playback, "g"),
        "max_regression_percent": format(parsed_regression, "g"),
        "fail_on_regression": parsed_fail,
        "matrix": {"repetition": list(range(1, parsed_repetitions + 1))},
    }


def resolve_commit(revision: str, *, repository: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    resolved = completed.stdout.strip()
    invalid_hash = len(resolved) != 40 or any(
        character not in "0123456789abcdef" for character in resolved
    )
    if completed.returncode != 0 or invalid_hash:
        raise ValueError("compare_ref does not resolve to a commit")
    return resolved


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate GUI benchmark inputs and create its repetition matrix.")
    parser.add_argument("--repetitions", default="3")
    parser.add_argument("--playback-seconds", default="30")
    parser.add_argument("--compare-ref", default="b600e90")
    parser.add_argument("--max-regression-percent", default="20")
    parser.add_argument("--fail-on-regression", default="false")
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--github-output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        values = validate_inputs(
            args.repetitions,
            args.playback_seconds,
            args.max_regression_percent,
            args.fail_on_regression,
        )
        values["compare_ref"] = resolve_commit(args.compare_ref, repository=args.repository)
    except ValueError as error:
        print(f"GUI performance input error: {error}")
        return 2

    lines = [
        f"repetitions={values['repetitions']}",
        f"playback_seconds={values['playback_seconds']}",
        f"compare_ref={values['compare_ref']}",
        f"max_regression={values['max_regression_percent']}",
        f"fail_on_regression={values['fail_on_regression']}",
        f"matrix={json.dumps(values['matrix'], separators=(',', ':'))}",
    ]
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
