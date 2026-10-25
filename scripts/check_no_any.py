"""追跡中の全PythonファイルでAnyの利用と型チェックの除外を拒否する。"""

from __future__ import annotations

import argparse
import ast
import io
import os
import re
import subprocess
import sys
import tempfile
import tokenize
from pathlib import Path
from typing import Literal


REPO_ROOT = Path(__file__).resolve().parents[1]
TYPE_IGNORE_PATTERN = re.compile(r"#\s*(?:type\s*:\s*ignore\b|mypy\s*:)")
Platform = Literal["linux", "win32", "darwin"]


class GateArgs(argparse.Namespace):
    platform: Platform = "linux"


def tracked_python_files() -> list[Path]:
    """Gitに追跡されるPythonソースと型スタブをすべて列挙する。"""

    output = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "*.py", "*.pyi", "*.pyw"],
        cwd=REPO_ROOT,
    )
    paths = sorted(Path(os.fsdecode(entry)) for entry in output.split(b"\0") if entry)
    if not paths:
        raise RuntimeError("Pythonファイルを列挙できませんでした")
    return paths


def source_findings(source: str, filename: str) -> list[str]:
    """明示的なAnyと、型エラーを隠すコメントを検出する。"""

    tree = ast.parse(source, filename=filename)
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "Any":
            lines.add(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == "Any":
            lines.add(node.lineno)
        elif isinstance(node, ast.ImportFrom) and any(alias.name == "Any" for alias in node.names):
            lines.add(node.lineno)

    findings = [f"{filename}:{line}: Anyの利用" for line in sorted(lines)]
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT and TYPE_IGNORE_PATTERN.match(token.string):
            findings.append(f"{filename}:{token.start[0]}: 型チェックの除外コメント")
    return sorted(findings)


def check_sources(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    for relative_path in paths:
        source = (REPO_ROOT / relative_path).read_text(encoding="utf-8-sig")
        try:
            findings.extend(source_findings(source, relative_path.as_posix()))
        except (SyntaxError, tokenize.TokenError) as error:
            findings.append(f"{relative_path}: Python構文を解析できません: {error}")
    return findings


def run_full_mypy(paths: list[Path], platform: Platform) -> int:
    """既存の限定設定を使わず、全追跡ファイルを厳格に検査する。"""

    with tempfile.TemporaryDirectory(prefix="no-any-mypy-") as directory:
        config = Path(directory) / "mypy.ini"
        config.write_text("[mypy]\n", encoding="utf-8")
        command = [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(config),
            "--python-version",
            "3.10",
            "--explicit-package-bases",
            "--platform",
            platform,
            "--strict",
            "--disallow-any-explicit",
            "--disallow-any-expr",
            "--disallow-any-unimported",
            "--disallow-any-decorated",
            *(path.as_posix() for path in paths),
        ]
        environment = {**os.environ, "MYPYPATH": str(REPO_ROOT / "typings")}
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if result.returncode:
        output_lines = result.stdout.splitlines()
        for line in output_lines[:80]:
            print(line)
        if len(output_lines) > 80:
            print(f"...ほか {len(output_lines) - 80} 行。ローカルで同じコマンドを実行して確認してください。")
            print(output_lines[-1])
    else:
        print(result.stdout, end="")
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="全PythonファイルからAnyと型チェック除外を禁止する")
    parser.add_argument("--platform", choices=("linux", "win32", "darwin"), required=True)
    args = parser.parse_args(namespace=GateArgs())
    paths = tracked_python_files()
    findings = check_sources(paths)
    if findings:
        for finding in findings[:80]:
            print(finding)
        if len(findings) > 80:
            print(f"...ほか {len(findings) - 80} 件")
        print(f"全 {len(paths)} ファイルに {len(findings)} 件の禁止事項があります")
        return 1
    return run_full_mypy(paths, args.platform)


if __name__ == "__main__":
    raise SystemExit(main())
