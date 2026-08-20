"""Prepare reliable Qt QProcess launches on Windows."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_ALIAS_ENVIRONMENT_VARIABLE = "SUBTITLE_EDIT_BAY_PROCESS_ALIAS_ROOT"


@dataclass(frozen=True)
class QProcessLaunch:
    """Program, arguments, and working directory passed to QProcess."""

    program: str
    arguments: tuple[str, ...]
    working_directory: str


def prepare_qprocess_launch(
    command: list[str],
    working_directory: str | Path,
    *,
    alias_base: str | Path | None = None,
) -> QProcessLaunch:
    """Return a launch description that avoids non-ASCII Qt paths on Windows.

    Qt's Windows process launcher can fail before CreateProcess when either the
    executable or working directory contains non-ASCII characters. Directory
    junctions preserve the original process and process tree while presenting
    Qt with ASCII-only paths.
    """

    if not command:
        raise ValueError("command must contain a program")

    working_path = Path(working_directory).resolve()
    if os.name != "nt":
        return QProcessLaunch(command[0], tuple(command[1:]), str(working_path))

    working_alias: Path | None = None
    safe_working_path = working_path
    if not str(working_path).isascii():
        working_alias = _create_directory_alias(
            working_path,
            "workspace",
            alias_base=alias_base,
        )
        safe_working_path = working_alias

    program = command[0]
    safe_program = program
    if not program.isascii():
        program_path = Path(program)
        if not program_path.is_absolute():
            raise OSError(
                "A non-ASCII QProcess program must use an absolute path: "
                f"{program}"
            )

        resolved_program = program_path.resolve()
        relative_program = _relative_to(resolved_program, working_path)
        if relative_program is not None and str(relative_program).isascii():
            if working_alias is None:
                working_alias = _create_directory_alias(
                    working_path,
                    "workspace",
                    alias_base=alias_base,
                )
            safe_program = str(working_alias / relative_program)
        else:
            executable_root = _find_ascii_relative_root(resolved_program)
            executable_alias = _create_directory_alias(
                executable_root,
                "python",
                alias_base=alias_base,
            )
            executable_relative = resolved_program.relative_to(executable_root)
            safe_program = str(executable_alias / executable_relative)

    return QProcessLaunch(
        safe_program,
        tuple(command[1:]),
        str(safe_working_path),
    )


def _find_ascii_relative_root(program: Path) -> Path:
    for parent in program.parents:
        relative_program = program.relative_to(parent)
        if (parent / "pyvenv.cfg").is_file() and str(relative_program).isascii():
            return parent

    for parent in program.parents:
        if str(program.relative_to(parent)).isascii():
            return parent

    raise OSError(f"Cannot create an ASCII alias for QProcess program: {program}")


def _relative_to(path: Path, root: Path) -> Path | None:
    try:
        return path.relative_to(root)
    except ValueError:
        return None


def _create_directory_alias(
    target: Path,
    kind: str,
    *,
    alias_base: str | Path | None,
) -> Path:
    target = target.resolve()
    if not target.is_dir():
        raise OSError(f"QProcess alias target is not a directory: {target}")

    digest = hashlib.sha256(os.path.normcase(str(target)).encode("utf-8")).hexdigest()[:16]
    errors: list[str] = []

    for root in _candidate_alias_roots(alias_base):
        if not str(root).isascii():
            continue
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            errors.append(f"{root}: {exc}")
            continue

        for suffix in range(4):
            suffix_text = "" if suffix == 0 else f"-{suffix}"
            link = root / f"{kind}-{digest}{suffix_text}"
            if os.path.lexists(link):
                try:
                    if link.is_dir() and os.path.samefile(link, target):
                        return link
                except OSError:
                    pass
                continue

            completed = subprocess.run(
                [
                    os.environ.get("COMSPEC", "cmd.exe"),
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    str(link),
                    str(target),
                ],
                check=False,
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode == 0:
                try:
                    if link.is_dir() and os.path.samefile(link, target):
                        return link
                except OSError as exc:
                    errors.append(f"{link}: {exc}")
                    continue

            detail = (completed.stderr or completed.stdout).strip()
            errors.append(f"{link}: {detail or 'mklink failed'}")

    detail = "; ".join(errors[-4:])
    raise OSError(
        f"Could not create an ASCII QProcess alias for {target}"
        + (f": {detail}" if detail else "")
    )


def _candidate_alias_roots(alias_base: str | Path | None) -> Iterable[Path]:
    if alias_base is not None:
        yield Path(alias_base)
        return

    configured = os.environ.get(_ALIAS_ENVIRONMENT_VARIABLE)
    values = [
        configured,
        os.environ.get("LOCALAPPDATA"),
        os.environ.get("PROGRAMDATA"),
        os.environ.get("PUBLIC"),
        tempfile.gettempdir(),
        str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Temp"),
    ]
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        root = Path(value) / "SubtitleEditBay" / "process-roots"
        key = os.path.normcase(str(root))
        if key in seen:
            continue
        seen.add(key)
        yield root
