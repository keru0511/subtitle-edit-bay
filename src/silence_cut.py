from __future__ import annotations

import argparse
import os
import re
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path

from .audio_mixer import build_audio_mix_filter
from .burn_subs import run_ffmpeg_with_nvenc_fallback
from .media_probe import probe_media_duration
from .video_encoding import DEFAULT_NVENC_CQ, DEFAULT_X264_CRF, build_video_encoding_args


SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9]+(?:\.[0-9]+)?)")
SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9]+(?:\.[0-9]+)?)")
DEFAULT_VIDEO_CODEC = "libx264"
DEFAULT_AUDIO_CODEC = "aac"
DEFAULT_AUDIO_TRACK = "0:a:0"
DEFAULT_NVENC_PRESET = "p5"
DEFAULT_FILTERED_AUDIO_RATE = "48000"
PIX_FMT = "yuv420p"


def _build_temporary_output(output_path: Path) -> Path:
    suffix = output_path.suffix or ".tmp"
    return output_path.with_name(f".{output_path.stem}.{uuid.uuid4().hex}.partial{suffix}")


def build_silencedetect_command(input_path: str, noise: str = "-35dB", duration: float = 0.4) -> list[str]:
    return [
        "ffmpeg",
        "-i",
        input_path,
        "-af",
        f"silencedetect=noise={noise}:d={duration}",
        "-f",
        "null",
        "-",
    ]


def parse_silencedetect_output(log_text: str, media_duration: float | None = None) -> list[tuple[float, float]]:
    silence_ranges: list[tuple[float, float]] = []
    current_start: float | None = None
    for line in log_text.splitlines():
        start_match = SILENCE_START_RE.search(line)
        if start_match:
            current_start = float(start_match.group(1))
            continue
        end_match = SILENCE_END_RE.search(line)
        if end_match and current_start is not None:
            silence_end = float(end_match.group(1))
            if silence_end > current_start:
                silence_ranges.append((current_start, silence_end))
            current_start = None
    if current_start is not None and media_duration is not None and media_duration > current_start:
        silence_ranges.append((current_start, media_duration))
    return silence_ranges


def merge_ranges(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not ranges:
        return []
    merged: list[tuple[float, float]] = []
    current_start, current_end = sorted(ranges)[0]
    for start, end in sorted(ranges)[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
            continue
        merged.append((current_start, current_end))
        current_start, current_end = start, end
    merged.append((current_start, current_end))
    return merged


def invert_ranges(duration: float, ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    clipped = merge_ranges(
        [
            (max(0.0, start), min(duration, end))
            for start, end in ranges
            if end > 0.0 and start < duration and end > start
        ]
    )
    inverted: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in clipped:
        if start > cursor:
            inverted.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        inverted.append((cursor, duration))
    return inverted


def shift_ranges(ranges: list[tuple[float, float]], offset_seconds: float, duration: float) -> list[tuple[float, float]]:
    return merge_ranges(
        [
            (max(0.0, start + offset_seconds), min(duration, end + offset_seconds))
            for start, end in ranges
            if end + offset_seconds > 0.0 and start + offset_seconds < duration
        ]
    )


def build_keep_ranges(
    duration: float,
    silence_ranges: list[tuple[float, float]],
    padding: float = 0.08,
    min_clip_duration: float = 0.25,
) -> list[tuple[float, float]]:
    cut_ranges: list[tuple[float, float]] = []
    for silence_start, silence_end in merge_ranges(silence_ranges):
        cut_start = 0.0 if silence_start <= 0.0 else min(silence_end, silence_start + padding)
        cut_end = duration if silence_end >= duration else max(silence_start, silence_end - padding)
        if cut_end > cut_start:
            cut_ranges.append((cut_start, cut_end))

    return [
        (start, end)
        for start, end in invert_ranges(duration, cut_ranges)
        if end - start >= min_clip_duration
    ]


def build_no_speech_plan(
    video_duration: float,
    speaker_speech_ranges: list[tuple[float, float]],
    offset_seconds: float,
    min_no_speech_seconds: float = 1.2,
    padding: float = 0.25,
    min_clip_duration: float = 0.25,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    aligned_speech = shift_ranges(speaker_speech_ranges, offset_seconds, video_duration)
    all_no_speech = invert_ranges(video_duration, aligned_speech)
    cut_ranges = [
        (start, end)
        for start, end in all_no_speech
        if end - start >= min_no_speech_seconds
    ]
    keep_ranges = build_keep_ranges(
        video_duration,
        cut_ranges,
        padding=padding,
        min_clip_duration=min_clip_duration,
    )
    return cut_ranges, keep_ranges


def retime_segments_for_keep_ranges(
    segments: list[dict],
    keep_ranges: list[tuple[float, float]],
) -> list[dict]:
    retimed_segments: list[dict] = []
    output_cursor = 0.0
    for keep_start, keep_end in keep_ranges:
        for segment in segments:
            segment_start = float(segment.get("start", 0.0))
            segment_end = float(segment.get("end", segment_start))
            overlap_start = max(segment_start, keep_start)
            overlap_end = min(segment_end, keep_end)
            if overlap_end <= overlap_start:
                continue
            retimed = dict(segment)
            shift = output_cursor - keep_start
            retimed["start"] = overlap_start + shift
            retimed["end"] = overlap_end + shift
            # Layout is already packed; stale source word times must not trigger another timing pass.
            retimed.pop("words", None)
            retimed_segments.append(retimed)
        output_cursor += keep_end - keep_start
    return sorted(retimed_segments, key=lambda item: (float(item["start"]), int(item.get("layout_row", 0))))


def build_concat_filter(
    keep_ranges: list[tuple[float, float]],
    audio_filter: str | None = None,
    video_filter: str | None = None,
    audio_track: str = DEFAULT_AUDIO_TRACK,
) -> str:
    video_parts: list[str] = []
    audio_parts: list[str] = []
    concat_inputs: list[str] = []
    split_filtered_audio = audio_track == "mixed_audio" and len(keep_ranges) > 1
    for index, (start, end) in enumerate(keep_ranges):
        video_parts.append(f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{index}]")
        audio_source = f"mixed_audio_{index}" if split_filtered_audio else audio_track
        audio_parts.append(f"[{audio_source}]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{index}]")
        concat_inputs.append(f"[v{index}][a{index}]")
    video_output = "[vcat]" if video_filter else "[v]"
    audio_output = "[acat]" if audio_filter else "[a]"
    audio_split = (
        [f"[mixed_audio]asplit={len(keep_ranges)}{''.join(f'[mixed_audio_{index}]' for index in range(len(keep_ranges)))}"]
        if split_filtered_audio
        else []
    )
    filters = audio_split + video_parts + audio_parts + [
        f"{''.join(concat_inputs)}concat=n={len(keep_ranges)}:v=1:a=1{video_output}{audio_output}"
    ]
    if video_filter:
        filters.append(f"[vcat]{video_filter}[v]")
    if audio_filter:
        filters.append(f"[acat]{audio_filter}[a]")
    return ";".join(filters)


def build_silence_cut_command(
    input_path: str,
    output_path: str,
    keep_ranges: list[tuple[float, float]],
    video_codec: str = DEFAULT_VIDEO_CODEC,
    audio_codec: str = DEFAULT_AUDIO_CODEC,
    nvenc_preset: str = DEFAULT_NVENC_PRESET,
    nvenc_cq: int = DEFAULT_NVENC_CQ,
    x264_crf: int = DEFAULT_X264_CRF,
    audio_filter: str | None = None,
    video_filter: str | None = None,
    filter_script_path: str | None = None,
    audio_track: str = DEFAULT_AUDIO_TRACK,
    audio_mix: dict | None = None,
    audio_offset_seconds: float = 0.0,
) -> list[str]:
    if not keep_ranges:
        raise ValueError("At least one keep range is required.")
    input_args: list[str] = []
    mix_filter = ""
    if audio_mix is not None:
        input_args, mix_filter = build_audio_mix_filter(audio_mix, offset_seconds=audio_offset_seconds)
        audio_track = "mixed_audio"
    concat_filter = build_concat_filter(
        keep_ranges,
        audio_filter=audio_filter,
        video_filter=video_filter,
        audio_track=audio_track,
    )
    if filter_script_path:
        # FFmpeg 9 removed the deprecated option on Windows.  The replacement
        # reads the option value from the temporary filter file without adding
        # its contents to the command line.
        filter_option = "-/filter_complex" if os.name == "nt" else "-filter_complex_script"
    else:
        filter_option = "-filter_complex"
    filter_value = filter_script_path or (f"{mix_filter};{concat_filter}" if mix_filter else concat_filter)
    command = ["ffmpeg", "-y", "-i", input_path]
    command.extend(input_args)
    command.extend([
        filter_option,
        filter_value,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        video_codec,
    ])
    command.extend(build_video_encoding_args(video_codec, nvenc_preset, nvenc_cq, x264_crf))
    command.extend(["-pix_fmt", PIX_FMT])
    command.extend(["-c:a", audio_codec])
    if audio_filter or audio_mix is not None:
        command.extend(["-ar", DEFAULT_FILTERED_AUDIO_RATE])
    command.extend(["-movflags", "+faststart"])
    command.append(output_path)
    return command


def cut_media_ranges(
    input_path: str,
    output_path: str,
    keep_ranges: list[tuple[float, float]],
    video_codec: str = DEFAULT_VIDEO_CODEC,
    audio_codec: str = DEFAULT_AUDIO_CODEC,
    nvenc_preset: str = DEFAULT_NVENC_PRESET,
    nvenc_cq: int = DEFAULT_NVENC_CQ,
    x264_crf: int = DEFAULT_X264_CRF,
    audio_filter: str | None = None,
    video_filter: str | None = None,
    audio_track: str = DEFAULT_AUDIO_TRACK,
    audio_mix: dict | None = None,
    audio_offset_seconds: float = 0.0,
    progress_callback: Callable[[str], None] | None = None,
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = _build_temporary_output(output)
    filter_script = output.parent / f"{output.stem}.ffmpeg-filter.txt"
    filter_script.write_text(
        (
            build_audio_mix_filter(audio_mix, offset_seconds=audio_offset_seconds)[1] + ";"
            if audio_mix is not None
            else ""
        )
        + build_concat_filter(
            keep_ranges,
            audio_filter=audio_filter,
            video_filter=video_filter,
            audio_track="mixed_audio" if audio_mix is not None else audio_track,
        ),
        encoding="utf-8",
    )
    try:
        def command_factory(codec: str) -> list[str]:
            return build_silence_cut_command(
                input_path,
                str(temporary_output),
                keep_ranges,
                video_codec=codec,
                audio_codec=audio_codec,
                nvenc_preset=nvenc_preset,
                nvenc_cq=nvenc_cq,
                x264_crf=x264_crf,
                audio_filter=audio_filter,
                video_filter=video_filter,
                filter_script_path=str(filter_script),
                audio_track=audio_track,
                audio_mix=audio_mix,
                audio_offset_seconds=audio_offset_seconds,
            )

        run_ffmpeg_with_nvenc_fallback(
            command_factory,
            video_codec,
            progress_callback=progress_callback,
        )
        if not temporary_output.exists() or temporary_output.stat().st_size == 0:
            raise RuntimeError(f"FFmpeg completed without a usable output file: {temporary_output}")
        os.replace(temporary_output, output)
    finally:
        filter_script.unlink(missing_ok=True)
        temporary_output.unlink(missing_ok=True)
    return output


def detect_silence(
    input_path: str,
    noise: str = "-35dB",
    duration: float = 0.4,
    media_duration: float | None = None,
) -> list[tuple[float, float]]:
    command = build_silencedetect_command(input_path, noise=noise, duration=duration)
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    return parse_silencedetect_output(
        (result.stderr or "") + "\n" + (result.stdout or ""),
        media_duration=media_duration,
    )


def detect_speech_ranges(input_path: str, noise: str = "-40dB", duration: float = 0.1) -> list[tuple[float, float]]:
    media_duration = probe_media_duration(input_path)
    silence_ranges = detect_silence(
        input_path,
        noise=noise,
        duration=duration,
        media_duration=media_duration,
    )
    return invert_ranges(media_duration, silence_ranges)


def cut_silence(
    input_path: str,
    output_path: str,
    noise: str = "-35dB",
    silence_duration: float = 0.4,
    padding: float = 0.08,
    min_clip_duration: float = 0.25,
    video_codec: str = DEFAULT_VIDEO_CODEC,
    audio_codec: str = DEFAULT_AUDIO_CODEC,
    nvenc_preset: str = DEFAULT_NVENC_PRESET,
    nvenc_cq: int = DEFAULT_NVENC_CQ,
    x264_crf: int = DEFAULT_X264_CRF,
    audio_filter: str | None = None,
    audio_track: str = DEFAULT_AUDIO_TRACK,
) -> Path:
    media_duration = probe_media_duration(input_path)
    silence_ranges = detect_silence(
        input_path,
        noise=noise,
        duration=silence_duration,
        media_duration=media_duration,
    )
    keep_ranges = build_keep_ranges(
        media_duration,
        silence_ranges,
        padding=padding,
        min_clip_duration=min_clip_duration,
    )
    return cut_media_ranges(
        input_path,
        output_path,
        keep_ranges,
        video_codec=video_codec,
        audio_codec=audio_codec,
        nvenc_preset=nvenc_preset,
        nvenc_cq=nvenc_cq,
        x264_crf=x264_crf,
        audio_filter=audio_filter,
        audio_track=audio_track,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect silence and cut silent ranges from a video.")
    parser.add_argument("--input", required=True, help="Input video path.")
    parser.add_argument("--output", required=True, help="Output video path.")
    parser.add_argument("--noise", default="-35dB", help="Silence threshold passed to ffmpeg silencedetect.")
    parser.add_argument("--silence-duration", type=float, default=0.4, help="Minimum silence duration in seconds.")
    parser.add_argument("--padding", type=float, default=0.08, help="Speech padding around cut boundaries.")
    parser.add_argument("--min-clip-duration", type=float, default=0.25, help="Minimum kept clip duration in seconds.")
    parser.add_argument("--audio-track", default=DEFAULT_AUDIO_TRACK, help="Audio track included in the output, such as 0:a:0.")
    parser.add_argument("--run", action="store_true", help="Execute instead of printing the command.")
    args = parser.parse_args()

    media_duration = probe_media_duration(args.input)
    silence_ranges = detect_silence(
        args.input,
        noise=args.noise,
        duration=args.silence_duration,
        media_duration=media_duration,
    )
    keep_ranges = build_keep_ranges(
        media_duration,
        silence_ranges,
        padding=args.padding,
        min_clip_duration=args.min_clip_duration,
    )
    command = build_silence_cut_command(args.input, args.output, keep_ranges, audio_track=args.audio_track)

    if not args.run:
        print(f"Duration: {media_duration:.3f}s")
        print(f"Silence ranges: {silence_ranges}")
        print(f"Keep ranges: {keep_ranges}")
        print("FFmpeg command:")
        print(" ".join(command))
        return

    result = cut_silence(
        args.input,
        args.output,
        noise=args.noise,
        silence_duration=args.silence_duration,
        padding=args.padding,
        min_clip_duration=args.min_clip_duration,
        audio_track=args.audio_track,
    )
    print(result)


if __name__ == "__main__":
    main()
