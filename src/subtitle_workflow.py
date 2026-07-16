from __future__ import annotations

import argparse
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .assemble_video import build_loudnorm_filter
from .burn_subs import build_ass_filter, run_ffmpeg_burn
from .craig_pipeline import (
    DEFAULT_ALIGNMENT_SAMPLE_RATE,
    DEFAULT_AUDIO_LOUDNESS_RANGE,
    DEFAULT_AUDIO_NORMALIZE,
    DEFAULT_AUDIO_TARGET_LUFS,
    DEFAULT_AUDIO_TRUE_PEAK_DB,
    DEFAULT_COMPUTE_TYPE,
    DEFAULT_DEVICE,
    DEFAULT_FILTERED_AUDIO_CODEC,
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
    DEFAULT_NO_SPEECH_MIN_SECONDS,
    DEFAULT_OUTPUT_AUDIO_TRACK,
    DEFAULT_POSTPROCESS_WORKERS,
    DEFAULT_SPEECH_DETECT_SILENCE_SECONDS,
    DEFAULT_SPEECH_MIN_CLIP_SECONDS,
    DEFAULT_SPEECH_PADDING_SECONDS,
    DEFAULT_SPEECH_THRESHOLD_DB,
    DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
    DEFAULT_SUBTITLE_VOLUME_SCALE_PERCENT,
    DEFAULT_VAD_OFFSET,
    DEFAULT_VAD_ONSET,
    build_craig_segments_for_transcript,
    build_speaker_style_map,
    decode_audio_samples,
    expected_audio_transcript_path,
    normalize_db_threshold,
    parse_craig_speaker_name,
    resolve_alignment,
    resolve_craig_audio_files,
    resolve_reference_audio_path,
    transcribe_audio_file,
    write_json,
)
from .merge_transcripts import refine_segments
from .pipeline import build_ass_from_transcript
from .render_ass import parse_track_color_args
from .runtime_config import load_command_runtime_config, resolve_bool_option, resolve_list_option, resolve_option
from .runtime_dependencies import check_runtime_dependencies, format_dependency_error
from .silence_cut import (
    build_no_speech_plan,
    cut_media_ranges,
    detect_speech_ranges,
    probe_media_duration,
    retime_segments_for_keep_ranges,
)
from .subtitle_project import (
    DEFAULT_WAVEFORM_SAMPLE_RATE,
    build_waveform,
    create_project,
    derive_ass_path,
    derive_project_path,
    derive_render_path,
    load_project,
    project_to_transcript,
    save_project,
)
from .video_encoding import DEFAULT_NVENC_CQ, DEFAULT_X264_CRF


DEFAULT_SPEAKER_COLORS = ["#FFD966", "#F6B26B", "#93C47D", "#6FA8DC", "#E78284", "#81C8BE"]


def log_progress(message: str) -> None:
    print(f"[subtitle_workflow] {message}", flush=True)


def _project_speakers(
    audio_files: list[Path],
    style_map: dict[str, str],
    track_color_map: dict[str, str],
) -> list[dict[str, Any]]:
    speakers: list[dict[str, Any]] = []
    for index, audio_file in enumerate(audio_files):
        name = parse_craig_speaker_name(str(audio_file))
        track_key = f"craig:{name}"
        speakers.append(
            {
                "name": name,
                "style": style_map[name],
                "track_key": track_key,
                "file_name": audio_file.name,
                "path": str(audio_file.resolve()),
                "color": track_color_map.get(track_key, DEFAULT_SPEAKER_COLORS[index % len(DEFAULT_SPEAKER_COLORS)]).upper(),
            }
        )
    return speakers


def _build_waveforms(
    audio_files: list[Path],
    speakers: list[dict[str, Any]],
    offset_seconds: float,
) -> list[dict[str, Any]]:
    waveforms: list[dict[str, Any]] = []
    speaker_by_path = {str(Path(item["path"]).resolve()): item for item in speakers}
    for audio_file in audio_files:
        speaker = speaker_by_path[str(audio_file.resolve())]
        log_progress(f"Building waveform for {audio_file.name}")
        try:
            samples = decode_audio_samples(str(audio_file), sample_rate=DEFAULT_WAVEFORM_SAMPLE_RATE)
        except (OSError, subprocess.CalledProcessError):
            continue
        waveforms.append(
            build_waveform(
                audio_file,
                speaker=speaker["name"],
                style=speaker["style"],
                color=speaker["color"],
                offset_seconds=offset_seconds,
                samples=samples,
            )
        )
    return waveforms


def transcribe_to_project(
    *,
    video_path: str,
    audio_files: list[str],
    output_dir: str,
    reference_audio: str | None = None,
    reference_track: str | None = None,
    alignment_offset_adjustment: float = 0.0,
    alignment_sample_rate: int = DEFAULT_ALIGNMENT_SAMPLE_RATE,
    model: str = DEFAULT_MODEL,
    device: str = DEFAULT_DEVICE,
    compute_type: str = DEFAULT_COMPUTE_TYPE,
    language: str = DEFAULT_LANGUAGE,
    vad_onset: float = DEFAULT_VAD_ONSET,
    vad_offset: float = DEFAULT_VAD_OFFSET,
    skip_existing_transcripts: bool = True,
    postprocess_workers: int = DEFAULT_POSTPROCESS_WORKERS,
    track_color_map: dict[str, str] | None = None,
    subtitle_font_size: int = 50,
    subtitle_volume_scale_percent: float = DEFAULT_SUBTITLE_VOLUME_SCALE_PERCENT,
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
    render_settings: dict[str, Any] | None = None,
    overwrite_project: bool = False,
) -> Path:
    resolved_audio = resolve_craig_audio_files(None, audio_files)
    if not resolved_audio:
        raise SystemExit("No Craig speaker audio files were selected.")
    reference_path = resolve_reference_audio_path(resolved_audio, reference_audio)
    if reference_path is None:
        reference_path = resolved_audio[0]

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    project_path = derive_project_path(video_path, output)
    if project_path.exists() and not overwrite_project:
        raise SystemExit(
            f"Editable project already exists: {project_path}. "
            "Move it or pass --overwrite-project to replace it explicitly."
        )
    colors = dict(track_color_map or {})
    style_map = build_speaker_style_map(resolved_audio)
    log_progress(f"Resolving alignment from {reference_path.name}")
    matched_track, offset_seconds, score = resolve_alignment(
        video_path,
        str(reference_path),
        reference_track,
        alignment_sample_rate,
    )
    offset_seconds += alignment_offset_adjustment
    log_progress(f"Alignment ready at {offset_seconds:+.3f}s on {matched_track}")

    transcript_dir = output / "transcripts"
    transcript_map: dict[str, str] = {}
    segment_futures: dict[str, Any] = {}
    merged_segments: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, postprocess_workers)) as executor:
        for audio_file in resolved_audio:
            expected = expected_audio_transcript_path(str(audio_file), str(transcript_dir))
            if skip_existing_transcripts and expected.exists():
                log_progress(f"Cache hit for {audio_file.name}")
            else:
                log_progress(f"Starting WhisperX for {audio_file.name} on {device}/{compute_type}")
            transcript = transcribe_audio_file(
                str(audio_file),
                str(transcript_dir),
                model=model,
                device=device,
                compute_type=compute_type,
                language=language,
                vad_onset=vad_onset,
                vad_offset=vad_offset,
                skip_existing=skip_existing_transcripts,
            )
            transcript_map[str(audio_file.resolve())] = str(transcript.resolve())
            segment_futures[str(audio_file)] = executor.submit(
                build_craig_segments_for_transcript,
                str(audio_file),
                str(transcript),
                style_map,
                offset_seconds,
                subtitle_font_size,
                subtitle_volume_scale_percent,
            )
        for audio_file in resolved_audio:
            segments = segment_futures[str(audio_file)].result()
            merged_segments.extend(segments)
            log_progress(f"Prepared {len(segments)} captions for {audio_file.name}")

    log_progress("Refining merged subtitle segments")
    refined, filtered = refine_segments(
        merged_segments,
        subtitle_max_gap_seconds=subtitle_max_gap_seconds,
        subtitle_end_padding_seconds=subtitle_end_padding_seconds,
        subtitle_min_duration_seconds=subtitle_min_duration_seconds,
    )
    merged_path = write_json(str(output / f"{Path(video_path).stem}.craig.merged.json"), {"segments": refined})
    filtered_path = write_json(str(output / f"{Path(video_path).stem}.craig.filtered.json"), {"segments": filtered})
    speakers = _project_speakers(resolved_audio, style_map, colors)
    waveforms = _build_waveforms(resolved_audio, speakers, offset_seconds)
    try:
        duration_seconds = probe_media_duration(video_path)
    except (OSError, subprocess.CalledProcessError, ValueError):
        duration_seconds = max((float(segment["end"]) for segment in refined), default=0.0)

    project = create_project(
        video_path=video_path,
        output_dir=output,
        duration_seconds=duration_seconds,
        segments=refined,
        audio_sources=speakers,
        speakers=speakers,
        waveforms=waveforms,
        subtitle_settings={
            "font_size": subtitle_font_size,
            "volume_scale_percent": subtitle_volume_scale_percent,
            "max_gap_seconds": subtitle_max_gap_seconds,
            "end_padding_seconds": subtitle_end_padding_seconds,
            "min_duration_seconds": subtitle_min_duration_seconds,
        },
        render_settings=render_settings,
        transcription={
            "model": model,
            "device": device,
            "compute_type": compute_type,
            "language": language,
            "reference_audio": str(reference_path.resolve()),
            "matched_track": matched_track,
            "offset_seconds": offset_seconds,
            "alignment_score": score,
            "transcripts": transcript_map,
            "merged_json": str(merged_path.resolve()),
            "filtered_json": str(filtered_path.resolve()),
        },
    )
    save_project(project_path, project)
    log_progress(f"Project ready: {project_path}")
    return project_path


def build_project_ass(
    project_path: str | Path,
    output_path: str | Path | None = None,
    *,
    subtitle_font_size: int | None = None,
) -> Path:
    project = load_project(project_path)
    settings = project.get("subtitle_settings", {})
    font_size = int(subtitle_font_size or settings.get("font_size", 50))
    transcript_path = Path(project_path).with_name(f".{Path(project_path).stem}.render.json")
    write_json(str(transcript_path), project_to_transcript(project))
    colors = {str(item.get("track_key", "")): str(item.get("color", "")) for item in project.get("speakers", []) if item.get("track_key") and item.get("color")}
    output = Path(output_path) if output_path else derive_ass_path(project_path)
    try:
        build_ass_from_transcript(
            str(transcript_path),
            str(output),
            track_color_map=colors,
            subtitle_font_size=font_size,
            subtitle_max_gap_seconds=float(settings.get("max_gap_seconds", DEFAULT_SUBTITLE_MAX_GAP_SECONDS)),
            subtitle_end_padding_seconds=float(settings.get("end_padding_seconds", DEFAULT_SUBTITLE_END_PADDING_SECONDS)),
            subtitle_min_duration_seconds=float(settings.get("min_duration_seconds", DEFAULT_SUBTITLE_MIN_DURATION_SECONDS)),
        )
    finally:
        transcript_path.unlink(missing_ok=True)
    log_progress(f"ASS preview ready: {output}")
    return output


def render_project_video(
    project_path: str | Path,
    output_path: str | Path | None = None,
    *,
    video_codec: str = "libx264",
    audio_codec: str = "copy",
    output_audio_track: str = DEFAULT_OUTPUT_AUDIO_TRACK,
    nvenc_preset: str = "p5",
    nvenc_cq: int = DEFAULT_NVENC_CQ,
    x264_crf: int = DEFAULT_X264_CRF,
    audio_normalize: bool = DEFAULT_AUDIO_NORMALIZE,
    audio_target_lufs: float = DEFAULT_AUDIO_TARGET_LUFS,
    audio_loudness_range: float = DEFAULT_AUDIO_LOUDNESS_RANGE,
    audio_true_peak_db: float = DEFAULT_AUDIO_TRUE_PEAK_DB,
    cut_no_speech: bool = False,
    no_speech_min_seconds: float = DEFAULT_NO_SPEECH_MIN_SECONDS,
    speech_padding_seconds: float = DEFAULT_SPEECH_PADDING_SECONDS,
    speech_threshold_db: str = DEFAULT_SPEECH_THRESHOLD_DB,
    speech_min_clip_seconds: float = DEFAULT_SPEECH_MIN_CLIP_SECONDS,
) -> Path:
    project = load_project(project_path)
    video_path = str(project["video"]["path"])
    if not Path(video_path).is_file():
        raise SystemExit(f"Project video was not found: {video_path}")
    ass_path = build_project_ass(project_path)
    output = Path(output_path) if output_path else derive_render_path(project_path)
    loudnorm_filter = build_loudnorm_filter(audio_target_lufs, audio_loudness_range, audio_true_peak_db) if audio_normalize else None

    if cut_no_speech:
        offset_seconds = float(project.get("transcription", {}).get("offset_seconds", 0.0))
        speech_ranges: list[tuple[float, float]] = []
        for source in project.get("audio_sources", []):
            source_path = str(source.get("path", ""))
            if Path(source_path).is_file():
                log_progress(f"Detecting speech in {Path(source_path).name}")
                speech_ranges.extend(
                    detect_speech_ranges(
                        source_path,
                        noise=normalize_db_threshold(speech_threshold_db),
                        duration=DEFAULT_SPEECH_DETECT_SILENCE_SECONDS,
                    )
                )
        duration = float(project.get("video", {}).get("duration_seconds", 0.0)) or probe_media_duration(video_path)
        no_speech_ranges, keep_ranges = build_no_speech_plan(
            duration,
            speech_ranges,
            offset_seconds,
            min_no_speech_seconds=no_speech_min_seconds,
            padding=speech_padding_seconds,
            min_clip_duration=speech_min_clip_seconds,
        )
        if not keep_ranges:
            raise SystemExit("No speech activity was detected; refusing to cut the entire video.")
        cut_json = Path(project_path).with_name(f".{Path(project_path).stem}.cut.json")
        cut_ass = Path(project_path).with_name(f".{Path(project_path).stem}.cut.ass")
        write_json(str(cut_json), {"segments": retime_segments_for_keep_ranges(project_to_transcript(project)["segments"], keep_ranges)})
        try:
            settings = project.get("subtitle_settings", {})
            colors = {
                str(item.get("track_key", "")): str(item.get("color", ""))
                for item in project.get("speakers", [])
                if item.get("track_key") and item.get("color")
            }
            build_ass_from_transcript(
                str(cut_json),
                str(cut_ass),
                track_color_map=colors,
                subtitle_font_size=int(settings.get("font_size", 50)),
                subtitle_max_gap_seconds=float(settings.get("max_gap_seconds", DEFAULT_SUBTITLE_MAX_GAP_SECONDS)),
                subtitle_end_padding_seconds=float(settings.get("end_padding_seconds", DEFAULT_SUBTITLE_END_PADDING_SECONDS)),
                subtitle_min_duration_seconds=float(settings.get("min_duration_seconds", DEFAULT_SUBTITLE_MIN_DURATION_SECONDS)),
            )
            log_progress(f"Rendering edited video and cutting {len(no_speech_ranges)} silent ranges")
            cut_media_ranges(
                video_path,
                str(output),
                keep_ranges,
                video_codec=video_codec,
                audio_codec=DEFAULT_FILTERED_AUDIO_CODEC,
                nvenc_preset=nvenc_preset,
                nvenc_cq=nvenc_cq,
                x264_crf=x264_crf,
                audio_filter=loudnorm_filter,
                video_filter=build_ass_filter(str(cut_ass)),
                audio_track=output_audio_track,
            )
        finally:
            cut_json.unlink(missing_ok=True)
            cut_ass.unlink(missing_ok=True)
    else:
        log_progress(f"Rendering edited subtitles to {output.name}")
        burn_audio_codec = DEFAULT_FILTERED_AUDIO_CODEC if loudnorm_filter else audio_codec
        run_ffmpeg_burn(
            video_path,
            str(ass_path),
            str(output),
            video_codec=video_codec,
            audio_codec=burn_audio_codec,
            nvenc_preset=nvenc_preset,
            nvenc_cq=nvenc_cq,
            x264_crf=x264_crf,
            audio_filter=loudnorm_filter,
            audio_track=output_audio_track,
        )
    project["render_settings"] = {
        **project.get("render_settings", {}),
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "output_audio_track": output_audio_track,
        "audio_normalize": audio_normalize,
        "audio_target_lufs": audio_target_lufs,
        "cut_no_speech": cut_no_speech,
        "no_speech_min_seconds": no_speech_min_seconds,
        "speech_padding_seconds": speech_padding_seconds,
        "last_output": str(output.resolve()),
    }
    save_project(project_path, project)
    log_progress(f"Render complete: {output}")
    return output


def _add_shared_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="Runtime JSON config path.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Editable subtitle project workflow.")
    subparsers = parser.add_subparsers(dest="phase", required=True)

    transcribe = subparsers.add_parser("transcribe", help="Transcribe sources and create an editable project.")
    _add_shared_options(transcribe)
    transcribe.add_argument("--video", required=True)
    transcribe.add_argument("--audio-file", action="append", required=True)
    transcribe.add_argument("--output-dir", required=True)
    transcribe.add_argument("--reference-audio")
    transcribe.add_argument("--reference-track")
    transcribe.add_argument("--alignment-offset-adjustment", type=float, default=None)
    transcribe.add_argument("--skip-existing-transcripts", action=argparse.BooleanOptionalAction, default=None)
    transcribe.add_argument("--overwrite-project", action="store_true", help="Explicitly replace an existing editable project.")
    transcribe.add_argument("--run", action="store_true")

    ass = subparsers.add_parser("ass", help="Generate ASS from an edited project.")
    _add_shared_options(ass)
    ass.add_argument("--project", required=True)
    ass.add_argument("--output")
    ass.add_argument("--subtitle-font-size", type=int)

    render = subparsers.add_parser("render", help="Burn an edited project into video.")
    _add_shared_options(render)
    render.add_argument("--project", required=True)
    render.add_argument("--output")
    render.add_argument("--run", action="store_true")

    args = parser.parse_args()
    config = load_command_runtime_config("craig_pipeline", args.config)

    if args.phase == "transcribe":
        if not args.run:
            print(derive_project_path(args.video, args.output_dir))
            return
        dependency_error = format_dependency_error(check_runtime_dependencies(), require_whisperx=True)
        if dependency_error:
            raise SystemExit(dependency_error)
        track_colors = parse_track_color_args(resolve_list_option(None, config, "track_color", []))
        render_settings = {
            key: config[key]
            for key in (
                "video_codec", "audio_codec", "output_audio_track", "nvenc_preset", "nvenc_cq", "x264_crf",
                "audio_normalize", "audio_target_lufs", "cut_no_speech", "no_speech_min_seconds", "speech_padding_seconds",
            )
            if key in config
        }
        result = transcribe_to_project(
            video_path=args.video,
            audio_files=args.audio_file,
            output_dir=args.output_dir,
            reference_audio=args.reference_audio,
            reference_track=args.reference_track,
            alignment_offset_adjustment=float(resolve_option(args.alignment_offset_adjustment, config, "alignment_offset_adjustment", 0.0)),
            model=resolve_option(None, config, "model", DEFAULT_MODEL),
            device=resolve_option(None, config, "device", DEFAULT_DEVICE),
            compute_type=resolve_option(None, config, "compute_type", DEFAULT_COMPUTE_TYPE),
            language=resolve_option(None, config, "language", DEFAULT_LANGUAGE),
            vad_onset=float(resolve_option(None, config, "vad_onset", DEFAULT_VAD_ONSET)),
            vad_offset=float(resolve_option(None, config, "vad_offset", DEFAULT_VAD_OFFSET)),
            skip_existing_transcripts=resolve_bool_option(args.skip_existing_transcripts, config, "skip_existing_transcripts", True),
            postprocess_workers=int(resolve_option(None, config, "postprocess_workers", DEFAULT_POSTPROCESS_WORKERS)),
            track_color_map=track_colors,
            subtitle_font_size=int(resolve_option(None, config, "subtitle_font_size", 50)),
            subtitle_volume_scale_percent=float(resolve_option(None, config, "subtitle_volume_scale_percent", DEFAULT_SUBTITLE_VOLUME_SCALE_PERCENT)),
            subtitle_max_gap_seconds=float(resolve_option(None, config, "subtitle_max_gap_seconds", DEFAULT_SUBTITLE_MAX_GAP_SECONDS)),
            subtitle_end_padding_seconds=float(resolve_option(None, config, "subtitle_end_padding_seconds", DEFAULT_SUBTITLE_END_PADDING_SECONDS)),
            subtitle_min_duration_seconds=float(resolve_option(None, config, "subtitle_min_duration_seconds", DEFAULT_SUBTITLE_MIN_DURATION_SECONDS)),
            render_settings=render_settings,
            overwrite_project=args.overwrite_project,
        )
        print(f"project_path: {result}")
        return

    if args.phase == "ass":
        result = build_project_ass(args.project, args.output, subtitle_font_size=args.subtitle_font_size)
        print(f"ass_path: {result}")
        return

    if not args.run:
        print(Path(args.output) if args.output else derive_render_path(args.project))
        return
    dependency_error = format_dependency_error(check_runtime_dependencies(), require_whisperx=False)
    if dependency_error:
        raise SystemExit(dependency_error)
    result = render_project_video(
        args.project,
        args.output,
        video_codec=resolve_option(None, config, "video_codec", "libx264"),
        audio_codec=resolve_option(None, config, "audio_codec", "copy"),
        output_audio_track=resolve_option(None, config, "output_audio_track", DEFAULT_OUTPUT_AUDIO_TRACK),
        nvenc_preset=resolve_option(None, config, "nvenc_preset", "p5"),
        nvenc_cq=int(resolve_option(None, config, "nvenc_cq", DEFAULT_NVENC_CQ)),
        x264_crf=int(resolve_option(None, config, "x264_crf", DEFAULT_X264_CRF)),
        audio_normalize=resolve_bool_option(None, config, "audio_normalize", DEFAULT_AUDIO_NORMALIZE),
        audio_target_lufs=float(resolve_option(None, config, "audio_target_lufs", DEFAULT_AUDIO_TARGET_LUFS)),
        audio_loudness_range=float(resolve_option(None, config, "audio_loudness_range", DEFAULT_AUDIO_LOUDNESS_RANGE)),
        audio_true_peak_db=float(resolve_option(None, config, "audio_true_peak_db", DEFAULT_AUDIO_TRUE_PEAK_DB)),
        cut_no_speech=resolve_bool_option(None, config, "cut_no_speech", False),
        no_speech_min_seconds=float(resolve_option(None, config, "no_speech_min_seconds", DEFAULT_NO_SPEECH_MIN_SECONDS)),
        speech_padding_seconds=float(resolve_option(None, config, "speech_padding_seconds", DEFAULT_SPEECH_PADDING_SECONDS)),
        speech_threshold_db=normalize_db_threshold(resolve_option(None, config, "speech_threshold_db", DEFAULT_SPEECH_THRESHOLD_DB)),
        speech_min_clip_seconds=float(resolve_option(None, config, "speech_min_clip_seconds", DEFAULT_SPEECH_MIN_CLIP_SECONDS)),
    )
    print(f"final_video: {result}")


if __name__ == "__main__":
    main()
