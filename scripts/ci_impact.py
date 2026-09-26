"""変更ファイルからCIジョブを選ぶ。未分類の変更は全検証へ倒す。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data_boundary import decode_json, is_object_list, is_object_mapping

JOBS = (
    "python-quality",
    "windows-tests",
    "windows-launcher-tests",
    "ffmpeg6-compat",
    "portable-tests",
    "windows-installer-smoke",
)
GROUP_JOBS = {
    "portable-unit": "portable-tests",
    "qt-gui": "portable-tests",
    "ffmpeg-runtime": "portable-tests",
    "windows-runtime": "windows-tests",
    "windows-ffmpeg-runtime": "windows-tests",
    "windows-launcher-runtime": "windows-launcher-tests",
    "ffmpeg6-compat": "ffmpeg6-compat",
}


class TestGroup(TypedDict):
    modules: list[str]
    selectors: list[str]


@dataclass
class CIImpactArgs(argparse.Namespace):
    base_sha: str = ""
    source_sha: str = ""
    event_name: str = ""
    github_output: Path = Path(".")


def _string_list(value: object) -> list[str]:
    if not is_object_list(value) or any(not isinstance(item, str) for item in value):
        raise ValueError("CIテスト分類の配列には文字列のみ指定できます")
    return [item for item in value if isinstance(item, str)]


def _load_test_groups() -> dict[str, TestGroup]:
    payload = decode_json((ROOT / "tests/ci_test_groups.json").read_text(encoding="utf-8"))
    if not is_object_mapping(payload):
        raise ValueError("CIテスト分類の形式が不正です")
    raw_groups = payload.get("groups")
    if not is_object_mapping(raw_groups):
        raise ValueError("CIテスト分類にgroupsがありません")
    groups: dict[str, TestGroup] = {}
    for name, value in raw_groups.items():
        if not isinstance(name, str) or not is_object_mapping(value):
            raise ValueError("CIテスト分類のグループが不正です")
        groups[name] = {
            "modules": _string_list(value.get("modules")),
            "selectors": _string_list(value.get("selectors")),
        }
    return groups


def plan_changes(paths: list[str], *, full: bool = False) -> dict[str, bool]:
    """複数領域は和集合にする。削除・改名されたテストも安全側へ倒す。"""
    selected = {"python-quality"}
    groups = _load_test_groups()
    for path in paths:
        if (path.startswith("docs/") and path.endswith(".md")) or path in {"README.md", "AGENTS.md", "LICENSE"}:
            continue
        if path.startswith((".github/", "installer/", "launcher/", "runtime/")) or path in {
            "VERSION",
            "requirements.txt",
            "requirements-dev.txt",
            "pyproject.toml",
            "tests/ci_test_groups.json",
        }:
            full = True
        elif path.startswith("src/"):
            selected.update({"portable-tests", "windows-tests", "ffmpeg6-compat", "windows-launcher-tests"})
        elif path.startswith(("assets/", "schemas/")):
            selected.update({"portable-tests", "windows-tests", "ffmpeg6-compat"})
        elif path.startswith("tests/test_") and path.endswith(".py"):
            module = Path(path).stem
            matched = False
            for group, config in groups.items():
                if module in config["modules"] or any(
                    selector.startswith(f"tests.{module}.") for selector in config["selectors"]
                ):
                    selected.add(GROUP_JOBS[group])
                    matched = True
            if not matched:
                full = True
        else:
            full = True
    return {job: full or job in selected for job in JOBS}


def changed_paths(base: str, source: str) -> list[str]:
    """改名を削除＋追加として扱い、旧パスの影響も含める。"""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", base, source],
        check=True,
        capture_output=True,
        cwd=ROOT,
    )
    return [path.decode("utf-8") for path in result.stdout.split(b"\0") if path]


def main() -> None:
    parser = argparse.ArgumentParser(description="変更範囲に応じたCI計画を出力する。")
    parser.add_argument("--base-sha", default="")
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args(namespace=CIImpactArgs())
    full = args.event_name not in {"pull_request", "push"} or not args.base_sha or set(args.base_sha) == {"0"}
    paths: list[str] = []
    if not full:
        try:
            paths = changed_paths(args.base_sha, args.source_sha)
        except (subprocess.CalledProcessError, UnicodeDecodeError):
            full = True
    plan = plan_changes(paths, full=full)
    summary: dict[str, object] = {"changed_files": paths, "full": full, "plan": plan}
    print(json.dumps(summary, ensure_ascii=False))
    with args.github_output.open("a", encoding="utf-8") as output:
        output.write(f"codeql={str(any(value for job, value in plan.items() if job != 'python-quality')).lower()}\n")
        output.write("impact_plan=" + json.dumps(plan, separators=(",", ":")) + "\n")
        for job, enabled in plan.items():
            output.write(f"{job}={str(enabled).lower()}\n")


if __name__ == "__main__":
    main()
