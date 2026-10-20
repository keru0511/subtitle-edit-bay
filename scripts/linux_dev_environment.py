"""Reject a cloud/CI runtime that differs from the committed Linux environment."""

from __future__ import annotations

import hashlib
import importlib
from importlib import metadata
import platform
from pathlib import Path
import re
import subprocess
import sys


def locked_versions(contents: str) -> dict[str, str]:
    return {
        re.sub(r"[-_.]+", "-", match.group(1)).lower(): match.group(2)
        for match in re.finditer(r"^([\w.-]+)==([^\s\\]+)", contents, re.MULTILINE)
    }


def check_inputs(root: Path, checksums: str) -> None:
    for line in checksums.splitlines():
        digest, name = line.split("  ", 1)
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Linux environment input changed: {name}; regenerate the shared lock and checksums")


def check_versions(expected: dict[str, str], actual: dict[str, str]) -> None:
    if actual != expected:
        differences = {
            name: (expected.get(name), actual.get(name))
            for name in sorted(expected.keys() | actual.keys())
            if expected.get(name) != actual.get(name)
        }
        raise ValueError(f"Linux environment version mismatch (expected, actual): {differences}")


def main() -> None:
    contract = Path("/opt/linux-dev")
    if (
        platform.python_implementation() != "CPython"
        or platform.python_version() != "3.10.22"
        or sys.platform != "linux"
        or platform.machine() != "x86_64"
    ):
        raise ValueError("Use scripts/linux_dev.sh: the environment requires CPython 3.10.22 on linux/amd64")
    check_inputs(Path.cwd(), (contract / "inputs.sha256").read_text(encoding="utf-8"))
    expected = locked_versions((contract / "requirements.lock").read_text(encoding="utf-8"))
    actual = {
        re.sub(r"[-_.]+", "-", dist.metadata["Name"]).lower(): dist.version
        for dist in metadata.distributions()
    }
    check_versions(expected, actual)
    installed = subprocess.check_output(["dpkg-query", "-W", "-f=${binary:Package}=${Version}\n"], text=True)
    if installed != (contract / "apt.lock").read_text(encoding="utf-8"):
        raise ValueError("Linux system packages differ from runtime/linux-dev/apt.lock")
    # Import ASR and repository scripts together to catch dependency namespace collisions.
    sys.path.insert(0, str(Path.cwd()))
    for module in ("scripts.run_ci_tests", "whisperx.asr", "whisperx.alignment", "torchcodec", "PySide6.QtCore"):
        importlib.import_module(module)
    print("Verified shared Linux CPU environment: Python 3.10.22, system packages, Python lock, ASR imports", flush=True)


if __name__ == "__main__":
    main()
