"""実音声CIのキャッシュ識別とCPU環境の検証・再構築。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Protocol, TypeGuard, cast


def _is_object_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


class _RuntimeArgs(Protocol):
    command: str
    venv: Path
    manifest: Path | None


def cache_key(root: Path, venv: Path, context: Mapping[str, str]) -> str:
    definitions = ["runtime/requirements-windows-cpu.lock", "runtime/runtime-contract.json"]
    payload: dict[str, object] = {
        "context": context,
        "venv": str(venv.resolve()),
        "definitions": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in definitions},
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return f"asr-runtime-v2-{digest}"


def runtime_context() -> dict[str, str]:
    # イメージの更新日では分けない。venvは配置先と元Pythonのパスが同じ場合だけ復元する。
    return {
        "os": sys.platform,
        "image_os": os.environ["ImageOS"],
        "architecture": platform.machine(),
        "python_version": sys.version,
        "python_executable": str(Path(sys.executable).resolve()),
        "python_home": str(Path(sys.base_prefix).resolve()),
    }


def prepare_runtime(root: Path, venv: Path, manifest: Path, cache_hit: bool) -> str:
    def run(*args: str | Path) -> None:
        subprocess.run([str(arg) for arg in args], cwd=root, check=True, timeout=300)

    script = root / "scripts/runtime_contract.py"
    python = venv / "Scripts/python.exe"

    def verify() -> None:
        run(python, "-m", "pip", "check")
        run(python, script, "verify-runtime", "--profile", "cpu", "--manifest-output", manifest)

    # 定義自体の不整合はキャッシュ障害として扱わない。
    run(sys.executable, script, "validate")
    if cache_hit:
        try:
            verify()
            return "restored"
        except (OSError, subprocess.SubprocessError) as error:
            print(f"復元環境の検証に失敗したため一度だけ再構築します: {error}", flush=True)
    contract: object = json.loads((root / "runtime/runtime-contract.json").read_text(encoding="utf-8"))
    if not _is_object_mapping(contract):
        raise ValueError("runtime contract must be an object")
    python_contract = contract.get("python")
    if not _is_object_mapping(python_contract):
        raise ValueError("runtime contract python section must be an object")
    pip_version = python_contract.get("pip_version")
    if not isinstance(pip_version, str):
        raise ValueError("runtime contract pip version must be a string")
    run(sys.executable, "-m", "venv", "--clear", venv)
    run(python, "-m", "pip", "install", "--no-cache-dir", f"pip=={pip_version}")
    run(
        python,
        "-m",
        "pip",
        "install",
        "--no-cache-dir",
        "--require-hashes",
        "--index-url",
        "https://pypi.org/simple",
        "-r",
        root / "runtime/requirements-windows-cpu.lock",
    )
    verify()
    return "rebuilt" if cache_hit else "installed"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands: tuple[str, str] = ("cache-key", "prepare")
    parser.add_argument("command", choices=commands)
    parser.add_argument("--venv", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    args = cast(_RuntimeArgs, parser.parse_args())
    root = Path(__file__).resolve().parents[1]
    if args.command == "cache-key":
        print(cache_key(root, args.venv, runtime_context()))
    else:
        if args.manifest is None:
            parser.error("prepareには--manifestが必要です")
        result = prepare_runtime(
            root, args.venv.resolve(), args.manifest, os.environ.get("RUNTIME_CACHE_HIT") == "true"
        )
        print(f"CPUランタイム: {result}", flush=True)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as handle:
                handle.write(f"CPUランタイム: `{result}`\n")


if __name__ == "__main__":
    main()
