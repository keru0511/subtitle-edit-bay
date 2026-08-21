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
    video_audio_track: str | None = None,
    alignment_offset_adjustment: float = 0.0,
    overwrite_project: bool = False,
    project_path: str | None = None,
) -> list[str]:
    if not video or not output_dir or (not audio_files and not video_audio_track):
        raise ValueError("video, either audio_files/video_audio_track, and output_dir are required")
    command = [sys.executable, "-u", "-m", "src.subtitle_workflow", "transcribe", "--video", video]
    for audio_file in audio_files:
        command.extend(["--audio-file", audio_file])
    if video_audio_track:
        command.extend(["--video-audio-track", video_audio_track])
    command.extend(["--output-dir", output_dir])
    if project_path:
        command.extend(["--project-path", project_path])
    if reference_audio:
        command.extend(["--reference-audio", reference_audio])
    if reference_track:
        command.extend(["--reference-track", reference_track])
    if overwrite_project:
        command.append("--overwrite-project")
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


def build_gui_command(
    config_path: str | Path,
    *,
    video: str,
    audio_files: list[str] | tuple[str, ...],
    output_dir: str,
    reference_audio: str | None = None,
    reference_track: str | None = None,
    alignment_offset_adjustment: float = 0.0,
    overwrite_project: bool = False,
    project_path: str | None = None,
) -> list[str]:
    """Build the GUI transcription command using the editable workflow path."""
    return build_gui_transcribe_command(
        config_path,
        video=video,
        audio_files=audio_files,
        output_dir=output_dir,
        reference_audio=reference_audio,
        reference_track=reference_track,
        alignment_offset_adjustment=alignment_offset_adjustment,
        overwrite_project=overwrite_project,
        project_path=project_path,
    )


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


def build_gui_short_video_command(
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
        "render-short",
        "--project",
        project_path,
        "--config",
        str(config_path),
    ]
    if output_path:
        command.extend(["--output", output_path])
    command.append("--run")
    return command
