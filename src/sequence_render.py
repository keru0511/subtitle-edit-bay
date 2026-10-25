"""Production render boundary for normal multi-clip sequences.

The sequence domain owns ordering, trim ranges, transition overlap and clip
audio state.  This module translates that validated contract into one FFmpeg
filter graph.  It deliberately has no Qt or QML dependency so the render
boundary can be exercised by semantic and unit tests independently of the UI.
"""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .data_boundary import coerce_float, coerce_int, is_object_list, is_object_mapping
from .ffmpeg_execution import run_atomic_ffmpeg_export
from .video_encoding import DEFAULT_NVENC_CQ, DEFAULT_X264_CRF, build_video_encoding_args
from .video_sequence import SequenceClip, VideoSequence, VideoSequenceError


class SequenceRenderError(ValueError):
    """Raised when a sequence cannot be rendered without guessing."""


@dataclass(frozen=True)
class SequenceRenderPlan:
    sequence: VideoSequence
    clips: tuple[SequenceClip, ...]
    output_duration: float
    include_audio: bool


# xfade requires identical frame rates and timebases on both inputs.  Sequence
# exports use a stable 30fps output clock so a concat result can feed a later
# transition without inheriting the source/container timebase.
SEQUENCE_RENDER_FPS = 30


def _number(value: object, field_name: str) -> float:
    try:
        number = coerce_float(value)
    except (TypeError, ValueError) as error:
        raise SequenceRenderError(f"{field_name} must be a number") from error
    if not math.isfinite(number):
        raise SequenceRenderError(f"{field_name} must be finite")
    return number


def _has_text_segments(project: Mapping[object, object]) -> bool:
    segments = project.get("segments", [])
    if not is_object_list(segments):
        return False
    return any(
        is_object_mapping(segment) and str(segment.get("text", "")).strip()
        for segment in segments
    )


def _video_parameters(asset_id: str, stream: Mapping[str, object]) -> tuple[int, int, str]:
    if not is_object_mapping(stream):
        raise SequenceRenderError(
            f"sequence asset video metadata is incomplete: {asset_id}"
        )
    try:
        width = coerce_int(stream["width"])
        height = coerce_int(stream["height"])
    except (KeyError, TypeError, ValueError) as error:
        raise SequenceRenderError(
            f"sequence asset video metadata is incomplete: {asset_id}"
        ) from error
    sample_aspect_ratio = stream.get("sample_aspect_ratio")
    sample_aspect_ratio_text = str(sample_aspect_ratio).strip() if sample_aspect_ratio is not None else ""
    if sample_aspect_ratio_text.lower() in {"", "n/a", "na", "unknown", "0:1", "0/1"}:
        raise SequenceRenderError(
            f"sequence asset video metadata is incomplete: {asset_id}"
        )
    if width <= 0 or height <= 0:
        raise SequenceRenderError(
            f"sequence asset video metadata is invalid: {asset_id}"
        )
    return width, height, sample_aspect_ratio_text


def _format_video_parameters(parameters: tuple[int, int, str]) -> str:
    width, height, sample_aspect_ratio = parameters
    return f"{width}x{height} SAR={sample_aspect_ratio}"


def prepare_sequence_render(
    project: Mapping[object, object],
    *,
    probe_duration: Callable[[str], float],
    probe_audio_streams: Callable[[str], Sequence[object]],
    probe_video_stream: Callable[[str], Mapping[str, object]],
    output_audio_track: str = "0:a:0",
    cut_no_speech: bool = False,
) -> SequenceRenderPlan:
    """Validate a multi-clip project and resolve stale media before encoding."""

    try:
        sequence = VideoSequence.from_json(
            project.get("sequence"),
            legacy_video=project.get("video"),
        )
    except VideoSequenceError as error:
        raise SequenceRenderError(f"sequence is invalid: {error}") from error
    if len(sequence.clips) < 2:
        raise SequenceRenderError("sequence render requires at least two clips")
    if sequence.is_legacy_single_video():
        raise SequenceRenderError("legacy single-video sequence must use the existing render path")
    if _has_text_segments(project):
        raise SequenceRenderError(
            "multi-clip subtitle mapping is not defined; refusing to render ambiguous timestamps"
        )
    timeline_payload = project.get("timeline")
    if is_object_mapping(timeline_payload) and timeline_payload.get("cuts"):
        raise SequenceRenderError(
            "single-source timeline cuts cannot be combined with a multi-clip sequence"
        )
    audio_mix = project.get("audio_mix")
    if is_object_mapping(audio_mix):
        if bool(audio_mix.get("customized")):
            raise SequenceRenderError(
                "custom audio mix is not sequence-scoped; refusing to render"
            )
        channels = audio_mix.get("channels", [])
        if not is_object_list(channels):
            raise SequenceRenderError("audio mix channels are invalid for sequence render")
        external_enabled = any(
            is_object_mapping(channel)
            and channel.get("kind") == "external"
            and bool(channel.get("enabled"))
            for channel in channels
        )
        if external_enabled:
            raise SequenceRenderError(
                "external audio mix is not sequence-scoped; refusing to render"
            )
    if cut_no_speech:
        raise SequenceRenderError(
            "silence cut is single-source only; refusing to combine it with a sequence"
        )
    if str(output_audio_track) != "0:a:0":
        raise SequenceRenderError(
            "multi-clip render uses each clip's first audio stream; select 0:a:0"
        )

    assets = {asset.id: asset for asset in sequence.assets}
    if len(assets) != len(sequence.assets):
        raise SequenceRenderError("sequence asset ids must be unique")
    audio_by_asset: dict[str, bool] = {}
    video_parameters_by_asset: dict[str, tuple[int, int, str]] = {}
    reference_video_asset: str | None = None
    reference_video_parameters: tuple[int, int, str] | None = None
    for clip in sequence.clips:
        asset = assets.get(clip.asset_id)
        if asset is None:
            raise SequenceRenderError(f"clip {clip.id!r} references an unknown asset")
        path = Path(asset.path)
        if not path.is_file():
            raise SequenceRenderError(f"sequence asset was not found: {asset.path}")
        try:
            actual_duration = _number(probe_duration(asset.path), f"{asset.id}.duration")
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            raise SequenceRenderError(f"could not probe sequence asset: {asset.path}") from error
        if actual_duration <= 0.0:
            raise SequenceRenderError(f"sequence asset has no usable duration: {asset.path}")
        if asset.duration_seconds <= 0.0 or asset.duration_seconds > actual_duration + 0.05:
            raise SequenceRenderError(
                f"sequence asset duration is stale: {asset.id}"
            )
        if clip.source_end > actual_duration + 0.05:
            raise SequenceRenderError(
                f"clip {clip.id!r} exceeds the probed asset duration"
            )
        if asset.id not in video_parameters_by_asset:
            try:
                video_stream = probe_video_stream(asset.path)
            except (OSError, ValueError, subprocess.CalledProcessError) as error:
                raise SequenceRenderError(
                    f"could not probe sequence asset video stream: {asset.path}"
                ) from error
            video_parameters = _video_parameters(asset.id, video_stream)
            video_parameters_by_asset[asset.id] = video_parameters
            if reference_video_parameters is None:
                reference_video_asset = asset.id
                reference_video_parameters = video_parameters
            elif video_parameters != reference_video_parameters:
                raise SequenceRenderError(
                    "sequence clips have mixed video resolution/SAR; "
                    f"{reference_video_asset}={_format_video_parameters(reference_video_parameters)}, "
                    f"{asset.id}={_format_video_parameters(video_parameters)}; refusing to render"
                )
        if asset.id not in audio_by_asset:
            try:
                audio_by_asset[asset.id] = bool(probe_audio_streams(asset.path))
            except (OSError, ValueError, subprocess.CalledProcessError) as error:
                raise SequenceRenderError(f"could not probe sequence asset streams: {asset.path}") from error

    audio_values = {audio_by_asset[clip.asset_id] for clip in sequence.clips}
    if len(audio_values) > 1:
        raise SequenceRenderError(
            "sequence clips mix assets with and without audio; refusing to guess the audio contract"
        )
    return SequenceRenderPlan(
        sequence=sequence,
        clips=sequence.clips,
        output_duration=sequence.output_duration,
        include_audio=bool(audio_values and next(iter(audio_values))),
    )


def _time(value: float) -> str:
    return f"{value:.3f}"


def _audio_filter(clip: SequenceClip, duration: float) -> str:
    offset = clip.audio_offset_seconds
    filters = [
        f"atrim=start={_time(clip.source_start)}:end={_time(clip.source_end)}",
        "asetpts=PTS-STARTPTS",
        f"volume={_time(0.0 if clip.muted else clip.volume)}",
    ]
    if offset > 0.0005:
        filters.append(f"adelay={max(1, round(offset * 1000))}:all=1")
    elif offset < -0.0005:
        filters.append(f"atrim=start={_time(-offset)}")
        filters.append("asetpts=PTS-STARTPTS")
    filters.extend(
        [
            "aformat=sample_rates=48000:sample_fmts=fltp",
            f"apad=whole_dur={_time(duration)}",
            f"atrim=duration={_time(duration)}",
        ]
    )
    return ",".join(filters)


def _normalize_video(label: str, output_label: str) -> str:
    return (
        f"[{label}]fps={SEQUENCE_RENDER_FPS},settb=AVTB,format=pix_fmts=yuv420p[{output_label}]"
    )


def _normalize_audio(label: str, output_label: str) -> str:
    return f"[{label}]aformat=sample_rates=48000:sample_fmts=fltp,asetpts=PTS-STARTPTS[{output_label}]"


def build_sequence_filter_graph(plan: SequenceRenderPlan, *, audio_filter: str | None = None) -> str:
    """Build a deterministic graph for the plan; inputs are one per ordered clip."""

    filters: list[str] = []
    video_labels: list[str] = []
    audio_labels: list[str] = []
    for index, clip in enumerate(plan.clips):
        duration = clip.duration
        raw_video = f"v{index}raw"
        video_label = f"v{index}"
        filters.append(
            f"[{index}:v:0]trim=start={_time(clip.source_start)}:end={_time(clip.source_end)},"
            f"setpts=PTS-STARTPTS[{raw_video}]"
        )
        filters.append(_normalize_video(raw_video, video_label))
        video_labels.append(video_label)
        if plan.include_audio:
            audio_label = f"a{index}"
            filters.append(
                f"[{index}:a:0]{_audio_filter(clip, duration)}[{audio_label}]"
            )
            audio_labels.append(audio_label)

    current_video = video_labels[0]
    current_audio = audio_labels[0] if plan.include_audio else ""
    current_duration = plan.clips[0].duration
    for index, clip in enumerate(plan.clips[1:], start=1):
        transition = clip.transition
        if transition.type == "cut":
            raw_video = f"vjoin{index}raw"
            next_video = f"vjoin{index}"
            filters.append(
                f"[{current_video}][{video_labels[index]}]concat=n=2:v=1:a=0[{raw_video}]"
            )
            filters.append(_normalize_video(raw_video, next_video))
            if plan.include_audio:
                raw_audio = f"ajoin{index}raw"
                next_audio = f"ajoin{index}"
                filters.append(
                    f"[{current_audio}][{audio_labels[index]}]concat=n=2:v=0:a=1[{raw_audio}]"
                )
                filters.append(_normalize_audio(raw_audio, next_audio))
                current_audio = next_audio
            current_duration += clip.duration
        else:
            overlap = transition.duration
            raw_video = f"vjoin{index}raw"
            next_video = f"vjoin{index}"
            filters.append(
                f"[{current_video}][{video_labels[index]}]xfade=transition=fade:"
                f"duration={_time(overlap)}:offset={_time(current_duration - overlap)}[{raw_video}]"
            )
            filters.append(_normalize_video(raw_video, next_video))
            if plan.include_audio:
                raw_audio = f"ajoin{index}raw"
                next_audio = f"ajoin{index}"
                filters.append(
                    f"[{current_audio}][{audio_labels[index]}]acrossfade=d={_time(overlap)}:"
                    f"c1=tri:c2=tri[{raw_audio}]"
                )
                filters.append(_normalize_audio(raw_audio, next_audio))
                current_audio = next_audio
            current_duration += clip.duration - overlap
        current_video = next_video

    filters.append(f"[{current_video}]format=yuv420p[vout]")
    if plan.include_audio:
        if audio_filter:
            filters.append(f"[{current_audio}]{audio_filter},atrim=duration={_time(plan.output_duration)}[aout]")
        else:
            filters.append(f"[{current_audio}]atrim=duration={_time(plan.output_duration)}[aout]")
    return ";".join(filters)


def build_sequence_ffmpeg_command(
    plan: SequenceRenderPlan,
    output: str | Path,
    *,
    video_codec: str,
    audio_codec: str = "aac",
    nvenc_preset: str = "p5",
    nvenc_cq: int = DEFAULT_NVENC_CQ,
    x264_crf: int = DEFAULT_X264_CRF,
    audio_filter: str | None = None,
) -> list[str]:
    graph = build_sequence_filter_graph(plan, audio_filter=audio_filter)
    command = ["ffmpeg", "-y"]
    for clip in plan.clips:
        asset = next(asset for asset in plan.sequence.assets if asset.id == clip.asset_id)
        command.extend(["-i", asset.path])
    command.extend(["-filter_complex", graph, "-map", "[vout]"])
    if plan.include_audio:
        command.extend(["-map", "[aout]"])
    command.extend(["-c:v", video_codec])
    command.extend(build_video_encoding_args(video_codec, nvenc_preset, nvenc_cq, x264_crf))
    command.extend(["-pix_fmt", "yuv420p"])
    if plan.include_audio:
        command.extend(["-c:a", "aac" if audio_codec == "copy" else audio_codec, "-ar", "48000"])
    if Path(output).suffix.lower() in {".mp4", ".m4v", ".mov"}:
        command.extend(["-movflags", "+faststart"])
    command.extend(["-t", _time(plan.output_duration), str(output)])
    return command


def render_sequence_video(
    plan: SequenceRenderPlan,
    output: str | Path,
    *,
    video_codec: str = "libx264",
    audio_codec: str = "aac",
    nvenc_preset: str = "p5",
    nvenc_cq: int = DEFAULT_NVENC_CQ,
    x264_crf: int = DEFAULT_X264_CRF,
    audio_filter: str | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> Path:
    output_path = Path(output)

    def command_builder(selected_codec: str, command_output: str) -> list[str]:
        return build_sequence_ffmpeg_command(
            plan,
            command_output,
            video_codec=selected_codec,
            audio_codec=audio_codec,
            nvenc_preset=nvenc_preset,
            nvenc_cq=nvenc_cq,
            x264_crf=x264_crf,
            audio_filter=audio_filter,
        )

    return run_atomic_ffmpeg_export(
        command_builder,
        output_path,
        video_codec=video_codec,
        progress_callback=progress_callback,
    )
