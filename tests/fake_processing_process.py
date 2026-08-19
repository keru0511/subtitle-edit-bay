from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("wait", "fail", "success"), required=True)
    parser.add_argument("--template")
    parser.add_argument("--project-path")
    args = parser.parse_args()

    print("[subtitle_workflow] Starting WhisperX", flush=True)
    if args.mode == "wait":
        print("[subtitle_workflow] Waiting for cancellation", flush=True)
        while True:
            time.sleep(1)

    if args.mode == "fail":
        print(
            "Synthetic workflow failure: input audio became unavailable",
            file=sys.stderr,
            flush=True,
        )
        return 23

    if not args.template or not args.project_path:
        print("success mode requires --template and --project-path", file=sys.stderr)
        return 2

    for marker in (
        "Refining merged subtitle segments",
        "Building waveform",
    ):
        print(f"[subtitle_workflow] {marker}", flush=True)
        time.sleep(0.02)
    project_path = Path(args.project_path)
    project_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.template, project_path)
    print(f"[subtitle_workflow] Project ready: {project_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
