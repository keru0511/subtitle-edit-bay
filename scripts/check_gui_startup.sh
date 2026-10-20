#!/usr/bin/env bash
set -euo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"
source runtime/linux-dev/env.sh
startup_result=$(mktemp)
trap 'rm -f "$startup_result"' EXIT
SUBTITLE_EDIT_BAY_STARTUP_SMOKE_RESULT="$startup_result" timeout --kill-after=5s 90s python -m src.gui
python - "$startup_result" <<'PY'
import json
import sys
from pathlib import Path

result = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert result.get("qmlLoaded") is True, result
print("GUI startup passed:", result.get("version"))
PY
