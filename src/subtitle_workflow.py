from __future__ import annotations

import argparse
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .audio_mixer import active_audio_mix_channels, reconcile_audio_mix, video_track_entries
from .ass_template import (
    DEFAULT_SUBTITLE_OUTLINE_COLOR,
    DEFAULT_SUBTITLE_OUTLINE_THICKNESS,
)
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
    build_speaker_style_map,
    decode_audio_samples,
    normalize_db_threshold,
    parse_craig_speaker_name,
    resolve_alignment,
    resolve_craig_audio_files,
    resolve_reference_audio_path,
    transcribe_craig_audio_files,
    write_json,
)
from .merge_transcripts import refine_segments
from .pipeline import build_ass_from_data
from .render_ass import parse_track_color_args
from .runtime_config import load_command_runtime_config, resolve_list_option
from .runtime_dependencies import check_runtime_dependencies, format_dependency_error
from .runtime_settings import (
    RuntimeSettings,
    configured_render_settings,
    render_runtime_options,
    settings_from_config,
    transcribe_runtime_options,
)
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
from .subtitle_workflow_transcription import transcribe_to_project_with_context
from .transcribe import probe_audio_streams
from .transcription_context_config import transcription_context_from_runtime_config
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
    subtitle_outline_color: str = DEFAULT_SUBTITLE_OUTLINE_COLOR,
    subtitle_outline_thickness: int = DEFAULT_SUBTITLE_OUTLINE_THICKNESS,
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
    speakers = _project_speakers(resolved_audio, style_map, colors)
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
    with ThreadPoolExecutor(max_workers=1) as waveform_executor:
        waveform_future = waveform_executor.submit(
            _build_waveforms,
            resolved_audio,
            speakers,
            offset_seconds,
        )
        transcription = transcribe_craig_audio_files(
            resolved_audio,
            transcript_dir,
            style_map,
            offset_seconds,
            model=model,
            device=device,
            compute_type=compute_type,
            language=language,
            vad_onset=vad_onset,
            vad_offset=vad_offset,
            skip_existing_transcripts=skip_existing_transcripts,
            postprocess_workers=postprocess_workers,
            subtitle_font_size=subtitle_font_size,
            subtitle_volume_scale_percent=subtitle_volume_scale_percent,
        )
        log_progress("Refining merged subtitle segments")
        refined, filtered = refine_segments(
            transcription.segments,
            subtitle_max_gap_seconds=subtitle_max_gap_seconds,
            subtitle_end_padding_seconds=subtitle_end_padding_seconds,
            subtitle_min_duration_seconds=subtitle_min_duration_seconds,
        )
        waveforms = waveform_future.result()
    transcript_map = transcription.transcript_map
    merged_path = write_json(str(output / f"{Path(video_path).stem}.craig.merged.json"), {"segments": refined})
    filtered_path = write_json(str(output / f"{Path(video_path).stem}.craig.filtered.json"), {"segments": filtered})
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
            "outline_color": subtitle_outline_color,
            "outline_thickness": subtitle_outline_thickness,
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
    try:
        video_tracks = video_track_entries(probe_audio_streams(video_path))
    except (OSError, subprocess.SubprocessError, ValueError):
        video_tracks = None
    reconcile_audio_mix(project, video_tracks)
    save_project(project_path, project)
    log_progress(f"Project ready: {project_path}")
    return project_path


def _ass_build_options(
    project: dict[str, Any],
    subtitle_font_size: int | None = None,
) -> dict[str, Any]:
    settings = project.get("subtitle_settings", {})
    colors = {
        str(item.get("track_key", "")): str(item.get("color", ""))
        for item in project.get("speakers", [])
        if item.get("track_key") and item.get("color")
    }
    return {
        "track_color_map": colors,
        "subtitle_font_size": int(subtitle_font_size or settings.get("font_size", 50)),
        "subtitle_outline_color": str(settings.get("outline_color", DEFAULT_SUBTITLE_OUTLINE_COLOR)),
        "subtitle_outline_thickness": int(settings.get("outline_thickness", DEFAULT_SUBTITLE_OUTLINE_THICKNESS)),
        "subtitle_max_gap_seconds": float(settings.get("max_gap_seconds", DEFAULT_SUBTITLE_MAX_GAP_SECONDS)),
        "subtitle_end_padding_seconds": float(settings.get("end_padding_seconds", DEFAULT_SUBTITLE_END_PADDING_SECONDS)),
        "subtitle_min_duration_seconds": float(settings.get("min_duration_seconds", DEFAULT_SUBTITLE_MIN_DURATION_SECONDS)),
    }


def build_project_ass(
    project_path: str | Path,
    output_path: str | Path | None = None,
    *,
    subtitle_font_size: int | None = None,
    _project: dict[str, Any] | None = None,
) -> Path:
    project = _project if _project is not None else load_project(project_path)
    output = Path(output_path) if output_path else derive_ass_path(project_path)
    build_ass_from_data(
        project_to_transcript(project, project_is_validated=True),
        str(output),
        **_ass_build_options(project, subtitle_font_size),
    )
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
    ass_path = build_project_ass(project_path, _project=project)
    output = Path(output_path) if output_path else derive_render_path(project_path)
    loudnorm_filter = build_loudnorm_filter(audio_target_lufs, audio_loudness_range, audio_true_peak_db) if audio_normalize else None
    audio_mix = project.get("audio_mix", {})
    use_audio_mix = bool(audio_mix.get("customized", False))
    offset_seconds = float(project.get("transcription", {}).get("offset_seconds", 0.0))
    if use_audio_mix:
        for channel in active_audio_mix_channels(audio_mix):
            if channel.get("kind") == "external" and not Path(str(channel.get("path", ""))).is_file():
                raise SystemExit(f"Mixer audio source was not found: {channel.get('path', '')}")
    if not use_audio_mix:
        try:
            actual_video_track_selectors = {
                entry["selector"] for entry in video_track_entries(probe_audio_streams(video_path))
            }
            has_known_video_tracks = True
        except (OSError, subprocess.CalledProcessError, ValueError):
            has_known_video_tracks = False
            actual_video_track_selectors = set()
        has_real_video_track = any(
            isinstance(channel, dict)
            and channel.get("kind") == "video"
            and str(channel.get("selector", "")).strip()
            and (
                has_known_video_tracks
                and str(channel.get("selector", "")) in actual_video_track_selectors
            )
            for channel in (audio_mix.get("channels") if isinstance(audio_mix.get("channels"), list) else [])
        )
        has_enabled_external = any(
            isinstance(channel, dict)
            and channel.get("kind") == "external"
            and bool(channel.get("enabled"))
            for channel in audio_mix.get("channels", [])
        )
        if not has_real_video_track and not has_enabled_external:
            for channel in audio_mix.get("channels", []):
                if (
                    isinstance(channel, dict)
                    and channel.get("kind") == "external"
                    and Path(str(channel.get("path", ""))).is_file()
                ):
                    channel["enabled"] = True
                    use_audio_mix = True
                    break

    if cut_no_speech:
        speech_ranges: list[tuple[float, float]] = []
        source_paths = [
            str(source.get("path", ""))
            for source in project.get("audio_sources", [])
            if Path(str(source.get("path", ""))).is_file()
        ]
        for source_path in source_paths:
            log_progress(f"Detecting speech in {Path(source_path).name}")

        def detect_source(source_path: str) -> list[tuple[float, float]]:
            return detect_speech_ranges(
                source_path,
                noise=normalize_db_threshold(speech_threshold_db),
                duration=DEFAULT_SPEECH_DETECT_SILENCE_SECONDS,
            )

        with ThreadPoolExecutor(max_workers=max(1, min(4, len(source_paths)))) as executor:
            for source_ranges in executor.map(detect_source, source_paths):
                speech_ranges.extend(source_ranges)
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
        cut_ass = Path(project_path).with_name(f".{Path(project_path).stem}.cut.ass")
        transcript = project_to_transcript(project, project_is_validated=True)
        cut_transcript = {"segments": retime_segments_for_keep_ranges(transcript["segments"], keep_ranges)}
        try:
            build_ass_from_data(
                cut_transcript,
                str(cut_ass),
                **_ass_build_options(project),
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
                audio_mix=audio_mix if use_audio_mix else None,
                audio_offset_seconds=offset_seconds,
            )
        finally:
            cut_ass.unlink(missing_ok=True)
    else:
        log_progress(f"Rendering edited subtitles to {output.name}")
        burn_audio_codec = DEFAULT_FILTERED_AUDIO_CODEC if loudnorm_filter or use_audio_mix else audio_codec
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
            audio_mix=audio_mix if use_audio_mix else None,
            audio_offset_seconds=offset_seconds,
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


def _transcribe_options_with_cli_overrides(
    settings: RuntimeSettings,
    *,
    alignment_offset_adjustment: float | None,
    skip_existing_transcripts: bool | None,
) -> dict[str, Any]:
    options = transcribe_runtime_options(settings)
    if alignment_offset_adjustment is not None:
        options["alignment_offset_adjustment"] = alignment_offset_adjustment
    if skip_existing_transcripts is not None:
        options["skip_existing_transcripts"] = skip_existing_transcripts
    return options


def main() -> None:
    parser = argparse.ArgumentParser(description="Editable subtitle project workflow.")
    subparsers = parser.add_subparsers(dest="phase", required=True)

    transcribe = subparsers.add_parser("transcribe", help="Transcribe sources and create an editable project.")
    _add_shared_options(transcribe)
    transcribe.add_argument("--video", required=True)
    transcribe.add_argument("--audio-file", action="append")
    transcribe.add_argument("--video-audio-track")
    transcribe.add_argument("--output-dir", required=True)
    transcribe.add_argument("--reference-audio")
    transcribe.add_argument("--reference-track")
    transcribe.add_argument("--alignment-offset-adjustment", type=float, default=None)
    transcribe.add_argument("--skip-existing-transcripts", action=argparse.BooleanOptionalAction, default=None)
    transcribe.add_argument("--transcription-context-file", help="Path to a transcription context JSON file.")
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
        settings = settings_from_config(config)
        transcribe_options = _transcribe_options_with_cli_overrides(
            settings,
            alignment_offset_adjustment=args.alignment_offset_adjustment,
            skip_existing_transcripts=args.skip_existing_transcripts,
        )
        device = str(transcribe_options["device"])
        dependency_error = format_dependency_error(
            check_runtime_dependencies(),
            require_whisperx=True,
            device=device,
        )
        if dependency_error:
            raise SystemExit(dependency_error)
        track_colors = parse_track_color_args(resolve_list_option(None, config, "track_color", []))
        context_base_dir = Path(args.config).resolve().parent if args.config else Path.cwd()
        transcription_context = transcription_context_from_runtime_config(
            config,
            cli_context_file=args.transcription_context_file,
            base_dir=context_base_dir,
        )
        transcribe_options.update(
            {
                "postprocess_workers": int(config.get("postprocess_workers", DEFAULT_POSTPROCESS_WORKERS)),
                "track_color_map": track_colors,
                "render_settings": configured_render_settings(settings, config),
                "overwrite_project": args.overwrite_project,
            }
        )
        result = transcribe_to_project_with_context(
            video_path=args.video,
            audio_files=args.audio_file or [],
            output_dir=args.output_dir,
            reference_audio=args.reference_audio,
            reference_track=args.reference_track,
            video_audio_track=args.video_audio_track,
            transcription_context=transcription_context,
            **transcribe_options,
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
    settings = settings_from_config(config)
    dependency_error = format_dependency_error(check_runtime_dependencies(), require_whisperx=False)
    if dependency_error:
        raise SystemExit(dependency_error)
    render_options = render_runtime_options(settings)
    render_options["speech_threshold_db"] = normalize_db_threshold(render_options["speech_threshold_db"])
    result = render_project_video(args.project, args.output, **render_options)
    print(f"final_video: {result}")


if __name__ == "__main__":
    main()
