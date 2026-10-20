#!/usr/bin/env bash
set -euo pipefail

mkdir -p "$HOME" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
python /opt/linux-dev/linux_dev_environment.py

case "${1:-verify}" in
    verify)
        python -m pip check
        ;;
    quality)
        python scripts/run_ci_tests.py --validate
        python scripts/check_quality.py --lint-only
        python scripts/check_quality.py --format-only --paths scripts/check_quality.py
        for target_platform in linux win32 darwin; do
            python scripts/check_quality.py --type-only --type-platform "$target_platform"
            python scripts/check_no_any.py --platform "$target_platform"
        done
        ;;
    test)
        python scripts/check_unittest_discovery.py
        python scripts/run_ci_tests.py --group portable-unit
        python scripts/run_ci_tests.py --group qt-gui
        RUN_FFMPEG_SMOKE=1 python scripts/run_ci_tests.py --group ffmpeg-runtime
        bash /opt/linux-dev/entrypoint.sh startup
        ;;
    startup)
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
        ;;
    *)
        exec "$@"
        ;;
esac
