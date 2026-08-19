from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", required=True)
    parser.add_argument("--project-path", required=True)
    args = parser.parse_args()

    time.sleep(0.5)
    for marker in (
        "Resolving alignment",
        "Starting WhisperX",
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
