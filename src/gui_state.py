from __future__ import annotations

import sys
from pathlib import Path

from .gui_state_base import *  # noqa: F401,F403


def build_gui_transcribe_command(
    config_path: str | Path,
    *,
    video: str,
    audio_files: list[str] | tuple[str, ...],
    output_dir: str,
    reference_audio: str | None = None,
    reference_track: str | None = None,
    alignment_offset_adjustment: float = 0.0,
) -> list[str]:
    if not video or not audio_files or not output_dir:
        raise ValueError("video, audio_files, and output_dir are required")
    command = [sys.executable, "-u", "-m", "src.subtitle_workflow", "transcribe", "--video", video]
    for audio_file in audio_files:
        command.extend(["--audio-file", audio_file])
    command.extend(["--output-dir", output_dir])
    if reference_audio:
        command.extend(["--reference-audio", reference_audio])
    if reference_track:
        command.extend(["--reference-track", reference_track])
    command.extend(
        [
            "--alignment-offset-adjustment",
            str(alignment_offset_adjustment),
            "--config",
            str(config_path),
            "--run",
        ]
    )
    return command


def build_gui_render_command(
    config_path: str | Path,
    *,
    project_path: str,
    output_path: str | None = None,
) -> list[str]:
    if not project_path:
        raise ValueError("project_path is required")
    command = [
        sys.executable,
        "-u",
        "-m",
        "src.subtitle_workflow",
        "render",
        "--project",
        project_path,
        "--config",
        str(config_path),
    ]
    if output_path:
        command.extend(["--output", output_path])
    command.append("--run")
    return command
