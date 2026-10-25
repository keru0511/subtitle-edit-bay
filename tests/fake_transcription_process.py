from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path
from typing import Mapping, cast


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", required=True)
    parser.add_argument("--project-path", required=True)
    args = cast(Mapping[str, object], vars(parser.parse_args()))
    template = args.get("template")
    project_path_arg = args.get("project_path")
    if not isinstance(template, str) or not isinstance(project_path_arg, str):
        parser.error("template and project path must be strings")

    time.sleep(0.5)
    for marker in (
        "Resolving alignment",
        "Starting WhisperX",
        "Refining merged subtitle segments",
        "Building waveform",
    ):
        print(f"[subtitle_workflow] {marker}", flush=True)
        time.sleep(0.02)

    project_path = Path(project_path_arg)
    project_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template, project_path)
    print(f"[subtitle_workflow] Project ready: {project_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
