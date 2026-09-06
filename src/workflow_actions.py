"""Shared preflight for transcription tools and normal/short output actions.

Dependency probing and processing state belong to the caller. This boundary uses
one snapshot for capabilities, encoder selection and the command to execute.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Mapping

from .gui_state import build_gui_render_command, build_gui_short_video_command
from .runtime_dependencies import RuntimeDependencyStatus
from .subtitle_project import derive_render_path, derive_short_render_path
from .video_encoding import select_automatic_video_codec


@dataclass(frozen=True)
class ActionCapability:
    reason: str = ""

    @property
    def enabled(self) -> bool:
        return not self.reason


@dataclass(frozen=True)
class RenderRequest:
    job: str
    command: list[str]
    output_path: Path
    video_codec: str


def transcription_capability(
    dependencies: RuntimeDependencyStatus,
    *,
    device: str,
    has_video: bool,
    has_audio: bool,
    output_dir: str,
    running: bool = False,
) -> ActionCapability:
    if running:
        return ActionCapability("処理の完了または停止を待ってください")
    missing = dependencies.missing()
    if missing:
        return ActionCapability("文字起こしに必要なツールがありません: " + ", ".join(missing))
    if device == "cuda" and not dependencies.cuda:
        return ActionCapability("CUDA版PyTorchが利用できません。文字起こし設定の処理デバイスをCPUへ変更してください")
    if not has_video:
        return ActionCapability("素材設定で動画を指定してください")
    if not has_audio:
        return ActionCapability("動画内に音声トラックが見つかりません。外部音声を追加するか、音声付きの動画を選択してください。")
    if not output_dir:
        return ActionCapability("素材設定で出力先フォルダを指定してください")
    return ActionCapability()


def render_output_path(project_path: str | Path, *, short: bool) -> Path:
    """Keep the existing save model; #272 owns output directory separation."""
    return derive_short_render_path(project_path) if short else derive_render_path(project_path)


def validate_render_output(output: Path, project: Mapping[str, Any], project_path: str) -> None:
    """Validate without creating files, for both capability display and execution."""
    target = output.resolve()
    sources = [project_path, str(project.get("video", {}).get("path", ""))]
    sources.extend(str(item.get("path", "")) for item in project.get("audio_sources", []))
    if any(source and target == Path(source).resolve() for source in sources):
        raise ValueError("出力先が入力素材またはプロジェクトと同じです")
    if target.exists() and (not target.is_file() or not os.access(target, os.W_OK)):
        raise ValueError("出力先のファイルへ書き込めません")
    parent = target.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
        raise ValueError("出力先フォルダへ書き込めません")


def render_capability(
    dependencies: RuntimeDependencyStatus,
    project: Mapping[str, Any] | None,
    project_path: str,
    *,
    short: bool = False,
    running: bool = False,
) -> ActionCapability:
    if running:
        return ActionCapability("処理の完了または停止を待ってください")
    if project is None or not project_path:
        return ActionCapability("編集プロジェクトを作成または開いてください")
    missing = dependencies.missing(require_whisperx=False)
    if missing:
        return ActionCapability("書き出しに必要なツールがありません: " + ", ".join(missing))
    try:
        if not Path(str(project.get("video", {}).get("path", ""))).is_file():
            return ActionCapability("書き出す動画素材が見つかりません。素材を再指定してください")
        if short:
            short_video = project.get("short_video", {})
            if not short_video.get("enabled") or not short_video.get("clips"):
                return ActionCapability("ショート動画のクリップを追加してください")
        validate_render_output(render_output_path(project_path, short=short), project, project_path)
    except (OSError, ValueError) as error:
        return ActionCapability(str(error))
    return ActionCapability()


def prepare_render_request(
    dependencies: RuntimeDependencyStatus,
    project: Mapping[str, Any] | None,
    project_path: str,
    config_path: str | Path,
    *,
    short: bool = False,
    running: bool = False,
) -> RenderRequest:
    capability = render_capability(dependencies, project, project_path, short=short, running=running)
    if not capability.enabled:
        raise ValueError(capability.reason)
    output = render_output_path(project_path, short=short)
    builder = build_gui_short_video_command if short else build_gui_render_command
    return RenderRequest(
        job="render_short" if short else "render",
        command=builder(config_path, project_path=project_path, output_path=str(output)),
        output_path=output,
        video_codec=select_automatic_video_codec(nvenc_available=dependencies.nvenc),
    )
