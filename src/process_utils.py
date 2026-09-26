from __future__ import annotations

import os
import subprocess
from typing import Literal, Protocol, TypedDict


class SubprocessOptions(TypedDict, total=False):
    """外部プロセス起動に渡すOS依存オプション。"""

    creationflags: int
    start_new_session: bool


class VersionProbeRun(Protocol):
    """CLI のバージョン確認に必要な subprocess.run の最小契約。"""

    def __call__(
        self,
        command: list[str],
        /,
        *,
        capture_output: Literal[True],
        text: Literal[True],
        encoding: str,
        errors: str,
        timeout: int,
        check: Literal[False],
        shell: Literal[False],
        creationflags: int,
    ) -> subprocess.CompletedProcess[str]: ...


def hidden_subprocess_kwargs() -> SubprocessOptions:
    """Return subprocess options that keep background Windows commands hidden."""
    if os.name != "nt":
        return {}
    creation_flag: object = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": creation_flag if isinstance(creation_flag, int) else 0}


def detached_subprocess_kwargs() -> SubprocessOptions:
    """GUIから独立して継続するプロセスの起動オプション。"""
    return {"start_new_session": True, **hidden_subprocess_kwargs()}


class StoppableProcess(Protocol):
    """Qtの型に依存せず停止を要求する最小インターフェース。"""

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


def stop_process(process: StoppableProcess, process_id: int, *, force: bool = False) -> None:
    """停止方法を隠蔽する。POSIXの子孫停止はまだ保証しない。"""
    if os.name == "nt" and process_id:
        command = ["taskkill", "/PID", str(process_id), "/T"]
        if force:
            command.append("/F")
        subprocess.run(command, capture_output=True, check=False, **hidden_subprocess_kwargs())
    elif force:
        process.kill()
    else:
        process.terminate()
