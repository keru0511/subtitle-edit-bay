from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from .burn_subs import DEFAULT_FILTERED_AUDIO_RATE, build_ass_filter
from .color_config import normalize_rgb_color
from .ffmpeg_execution import run_atomic_ffmpeg_export
from .media_probe import probe_media_duration, probe_media_stream_types
from .short_video_schema import ShortVideo, ShortVideoBgm, ShortVideoClip, ShortVideoError
from .short_video_timeline import build_short_video_timeline
from .subtitle_project import derive_short_render_path, load_project
from .video_encoding import DEFAULT_NVENC_CQ, DEFAULT_X264_CRF, build_video_encoding_args

DEFAULT_SHORT_VIDEO_CODEC = "libx264"
DEFAULT_SHORT_AUDIO_CODEC = "aac"
DEFAULT_SHORT_AUDIO_BITRATE = "192k"
DEFAULT_XFADE_TRANSITION = "fade"
DEFAULT_ACROSSFADE_CURVE = "tri"
DEFAULT_BOXBLUR_RADIUS = 40
DEFAULT_FILTER_SCRIPT_THRESHOLD = 8192


def _log_progress(message: str) -> None:
    print(f"[short_video] {message}", flush=True)


def _format_filter_time(seconds: float) -> str:
    return f"{seconds:.3f}"


def _normalize_hex_color(value: str) -> str:
    try:
        normalized = normalize_rgb_color(value).lstrip("#").upper()
    except (TypeError, ValueError, OverflowError):
        normalized = "000000"
    if len(normalized) != 6 or any(char not in "0123456789ABCDEF" for char in normalized):
        normalized = "000000"
    return normalized


def _clip_background_color(clip: ShortVideoClip, global_background_color: str) -> str:
    return _normalize_hex_color(clip.background_color or global_background_color)


def _clip_fit(clip: ShortVideoClip, global_fit: str) -> str:
    return clip.fit or global_fit or "cover"


def _build_fit_filter(width: int, height: int, fit: str, background_color: str) -> str:
    if fit == "contain":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:{background_color},"
            "format=yuv420p"
        )
    if fit == "blur":
        return (
            "split[orig][fill];"
            f"[fill]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            f"boxblur={DEFAULT_BOXBLUR_RADIUS}:{DEFAULT_BOXBLUR_RADIUS},"
            "format=yuv420p[bg];"
            f"[orig]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            "format=yuv420p[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2:format=auto"
        )
    # cover
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:(iw-ow)/2:(ih-oh)/2,"
        "format=yuv420p"
    )


def _build_clip_video_filter(
    index: int,
    clip: ShortVideoClip,
    output_width: int,
    output_height: int,
    output_fps: int,
    fit: str,
    background_color: str,
) -> str:
    fit_filter = _build_fit_filter(output_width, output_height, fit, background_color)
    return (
        f"[0:v:0]trim=start={_format_filter_time(clip.start)}:"
        f"end={_format_filter_time(clip.end)},"
        "setpts=PTS-STARTPTS,"
        f"{fit_filter},"
        f"fps={output_fps},"
        "setsar=1"
        f"[sv{index}]"
    )


def _build_clip_audio_filter(index: int, clip: ShortVideoClip) -> str:
    return (
        f"[0:a:0]atrim=start={_format_filter_time(clip.start)}:"
        f"end={_format_filter_time(clip.end)},"
        "asetpts=PTS-STARTPTS,"
        "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        f"[sa{index}]"
    )


def _build_bgm_filter(bgm: ShortVideoBgm, total_duration: float) -> str | None:
    """Build the FFmpeg audio filter chain for the BGM track.

    The BGM is trimmed to the requested in/out points, looped to cover the
    active portion of the short video (total duration minus start offset),
    delayed by the start offset, attenuated by volume, and converted to a
    consistent sample format for mixing.
    """
    if not bgm.path or total_duration <= 0.0:
        return None
    active_duration = total_duration - bgm.start
    if active_duration <= 0.0:
        return None

    start_ms = max(0, int(round(bgm.start * 1000)))
    volume = max(0.0, min(1.0, bgm.volume))
    parts: list[str] = ["[1:a:0]"]
    if bgm.out_point > bgm.in_point > 0.0 or (bgm.in_point == 0.0 and bgm.out_point > 0.0):
        parts.append(
            f"atrim=start={_format_filter_time(bgm.in_point)}:"
            f"end={_format_filter_time(bgm.out_point)},asetpts=PTS-STARTPTS,"
        )
    else:
        parts.append("asetpts=PTS-STARTPTS,")
    parts.append(
        f"aloop=loop=-1:size=0,"
        f"atrim=0:{_format_filter_time(active_duration)},"
        f"adelay={start_ms}:all=1,"
        f"volume={_format_filter_time(volume)},"
        "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[bgm]"
    )
    return "".join(parts)


def build_short_video_filter_complex(
    short_video: ShortVideo,
    *,
    has_audio: bool = True,
    include_bgm: bool = False,
    ass_path: str | None = None,
) -> str:
    """Build an FFmpeg filter_complex for the short mode video pipeline.

    Each selected clip is trimmed from the source video, converted to 9:16
    using the requested fit (cover / contain / blur), optionally crossfaded with
    neighbouring clips, mixed with an optional BGM track, and finally has ASS
    subtitles burned in if ``ass_path`` is provided.
    """
    clips = short_video.clips
    if not clips:
        raise ShortVideoError("short_video.clips must contain at least one clip")
    timeline = build_short_video_timeline(short_video)

    width = short_video.output.width
    height = short_video.output.height
    fps = short_video.output.fps
    global_fit = short_video.global_fit
    global_background_color = _normalize_hex_color(short_video.global_background_color)
    transition = short_video.transition
    use_bgm = include_bgm and bool(short_video.bgm.path)

    stream_filters: list[str] = []
    for index, clip in enumerate(clips):
        fit = _clip_fit(clip, global_fit)
        background_color = _clip_background_color(clip, global_background_color)
        stream_filters.append(
            _build_clip_video_filter(index, clip, width, height, fps, fit, background_color)
        )
        if has_audio:
            stream_filters.append(_build_clip_audio_filter(index, clip))

    v_out = "sv0"
    a_out = "sa0" if has_audio else ""
    total_duration = timeline.total_duration
    clip_count = len(clips)

    if clip_count > 1:
        if all(entry.overlap <= 0.0 for entry in timeline.clips[1:]):
            v_inputs = "".join(f"[sv{i}]" for i in range(clip_count))
            stream_filters.append(f"{v_inputs}concat=n={clip_count}:v=1:a=0[v_concat]")
            v_out = "v_concat"
            if has_audio:
                a_inputs = "".join(f"[sa{i}]" for i in range(clip_count))
                stream_filters.append(f"{a_inputs}concat=n={clip_count}:v=0:a=1[a_concat]")
                a_out = "a_concat"
        else:
            for index in range(1, clip_count):
                timeline_clip = timeline.clips[index]
                td = timeline_clip.overlap
                is_last = index == clip_count - 1
                if td <= 0.0:
                    new_v = f"vc{index}"
                    stream_filters.append(
                        f"[{v_out}][sv{index}]concat=n=2:v=1:a=0[{new_v}]"
                    )
                    v_out = new_v
                    if has_audio:
                        new_a = "aout" if is_last else f"ac{index}"
                        stream_filters.append(
                            f"[{a_out}][sa{index}]concat=n=2:v=0:a=1[{new_a}]"
                        )
                        a_out = new_a
                    continue
                offset = timeline_clip.output_start
                new_v = f"vx{index}"
                new_a = "aout" if is_last else f"ax{index}"
                stream_filters.append(
                    f"[{v_out}][sv{index}]"
                    f"xfade=transition={DEFAULT_XFADE_TRANSITION}:"
                    f"duration={_format_filter_time(td)}:"
                    f"offset={_format_filter_time(offset)}"
                    f"[{new_v}]"
                )
                if has_audio:
                    stream_filters.append(
                        f"[{a_out}][sa{index}]"
                        f"acrossfade=d={_format_filter_time(td)}:"
                        f"c1={DEFAULT_ACROSSFADE_CURVE}:"
                        f"c2={DEFAULT_ACROSSFADE_CURVE}"
                        f"[{new_a}]"
                    )
                v_out = new_v
                a_out = new_a

    if ass_path:
        ass_filter = build_ass_filter(str(ass_path))
        stream_filters.append(f"[{v_out}]{ass_filter},format=yuv420p[v_final]")
    else:
        stream_filters.append(f"[{v_out}]format=yuv420p[v_final]")

    if use_bgm:
        bgm_filter = _build_bgm_filter(short_video.bgm, total_duration)
        if bgm_filter:
            stream_filters.append(bgm_filter)
            main_label = a_out if has_audio else None
            if main_label is not None:
                if main_label == "aout":
                    stream_filters.append("[aout]anull[main_audio]")
                    main_label = "main_audio"
                stream_filters.append(
                    f"[{main_label}][bgm]amix=inputs=2:"
                    "duration=first:dropout_transition=0:normalize=0:"
                    "weights='1 1'[aout]"
                )
            else:
                stream_filters.append("[bgm]anull[aout]")
        elif has_audio and a_out != "aout":
            stream_filters.append(f"[{a_out}]anull[aout]")
    elif has_audio and a_out != "aout":
        stream_filters.append(f"[{a_out}]anull[aout]")

    return ";".join(stream_filters)


def build_short_video_command(
    video_path: str,
    filter_complex: str,
    output_path: str,
    *,
    video_codec: str,
    output_width: int,
    output_height: int,
    output_fps: int,
    has_audio: bool = True,
    bgm_path: str | None = None,
    audio_codec: str = DEFAULT_SHORT_AUDIO_CODEC,
    nvenc_preset: str = "p5",
    nvenc_cq: int = DEFAULT_NVENC_CQ,
    x264_crf: int = DEFAULT_X264_CRF,
    filter_script_path: str | None = None,
) -> list[str]:
    """Build the full FFmpeg command for the short video render."""
    if filter_script_path:
        filter_option = "-filter_complex_script"
        filter_value = filter_script_path
    else:
        filter_option = "-filter_complex"
        filter_value = filter_complex

    command = ["ffmpeg", "-y", "-i", video_path]
    if bgm_path:
        command.extend(["-i", str(bgm_path)])
    command.extend([filter_option, filter_value])
    command.extend(["-map", "[v_final]"])
    if has_audio:
        command.extend(["-map", "[aout]"])

    command.extend(["-c:v", video_codec])
    command.extend(build_video_encoding_args(video_codec, nvenc_preset, nvenc_cq, x264_crf))
    command.extend([
        "-r", str(output_fps),
        "-pix_fmt", "yuv420p",
        "-s", f"{output_width}x{output_height}",
    ])
    if has_audio:
        command.extend([
            "-c:a", audio_codec,
            "-b:a", DEFAULT_SHORT_AUDIO_BITRATE,
            "-ar", DEFAULT_FILTERED_AUDIO_RATE,
        ])

    if Path(output_path).suffix.lower() in {".mp4", ".m4v", ".mov"}:
        command.extend(["-movflags", "+faststart"])

    command.append(output_path)
    return command


def _write_filter_script(filter_complex: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        suffix=".ffmpeg-filter.txt",
        prefix="short-video-",
        delete=False,
        mode="w",
        encoding="utf-8",
    )
    handle.write(filter_complex)
    handle.close()
    return Path(handle.name)


def _clamp_clip_times(clips: list[ShortVideoClip], video_duration: float) -> list[ShortVideoClip]:
    clamped: list[ShortVideoClip] = []
    for clip in clips:
        start = max(0.0, clip.start)
        end = max(start, min(clip.end, video_duration))
        if end <= start:
            continue
        if start != clip.start or end != clip.end:
            clamped.append(replace(clip, start=start, end=end))
        else:
            clamped.append(clip)
    return clamped


def render_short_video(
    project_path: str | Path,
    output_path: str | Path | None = None,
    *,
    video_codec: str = DEFAULT_SHORT_VIDEO_CODEC,
    audio_codec: str = DEFAULT_SHORT_AUDIO_CODEC,
    nvenc_preset: str = "p5",
    nvenc_cq: int = DEFAULT_NVENC_CQ,
    x264_crf: int = DEFAULT_X264_CRF,
    ass_path: str | Path | None = None,
    progress_callback: Callable[[str], None] | None = None,
    _project: dict[str, Any] | None = None,
) -> Path:
    """Render the short mode vertical video for a subtitle project."""
    project = _project if _project is not None else load_project(project_path)
    video_path = str(project.get("video", {}).get("path", ""))
    if not video_path or not Path(video_path).is_file():
        raise FileNotFoundError(f"Project video was not found: {video_path}")

    short_video = ShortVideo.from_json(project.get("short_video"))
    if not short_video.clips:
        raise ShortVideoError("short_video.clips is empty; nothing to render")

    output = Path(output_path) if output_path else derive_short_render_path(project_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        video_duration = probe_media_duration(video_path)
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        raise ShortVideoError(f"Could not probe video duration: {error}") from error

    clamped_clips = _clamp_clip_times(short_video.clips, video_duration)
    if not clamped_clips:
        raise ShortVideoError("All clips are outside the video duration")

    short_video = replace(short_video, clips=clamped_clips)

    try:
        has_audio = "audio" in probe_media_stream_types(video_path)
    except (OSError, subprocess.CalledProcessError, ValueError):
        has_audio = False

    bgm_path = short_video.bgm.path
    include_bgm = bool(bgm_path) and Path(bgm_path).is_file()
    command_has_audio = has_audio or include_bgm

    filter_complex = build_short_video_filter_complex(
        short_video,
        has_audio=has_audio,
        include_bgm=include_bgm,
        ass_path=str(ass_path) if ass_path else None,
    )

    use_filter_script = os.name == "nt" or len(filter_complex) > DEFAULT_FILTER_SCRIPT_THRESHOLD
    filter_script: Path | None = None

    def progress(message: str) -> None:
        if progress_callback is not None:
            progress_callback(message)
        else:
            _log_progress(message)

    try:
        if use_filter_script:
            filter_script = _write_filter_script(filter_complex)

        progress(f"Rendering short video to {output.name}")

        def command_builder(selected_codec: str, command_output: str) -> list[str]:
            return build_short_video_command(
                video_path,
                filter_complex,
                command_output,
                video_codec=selected_codec,
                output_width=short_video.output.width,
                output_height=short_video.output.height,
                output_fps=short_video.output.fps,
                has_audio=command_has_audio,
                bgm_path=bgm_path if include_bgm else None,
                audio_codec=audio_codec,
                nvenc_preset=nvenc_preset,
                nvenc_cq=nvenc_cq,
                x264_crf=x264_crf,
                filter_script_path=str(filter_script) if filter_script else None,
            )

        result = run_atomic_ffmpeg_export(
            command_builder,
            output,
            video_codec=video_codec,
            progress_callback=progress,
        )
        progress(f"Render complete: {result}")
        return result
    finally:
        if filter_script is not None:
            try:
                filter_script.unlink(missing_ok=True)
            except OSError:
                pass
