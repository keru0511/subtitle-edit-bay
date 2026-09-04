from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.short_video_schema import SHORT_VIDEO_SCHEMA_VERSION
from src.subtitle_project import create_project, validate_project


FIXTURE_TIMESTAMP = "2026-01-01T00:00:00+00:00"
DEFAULT_SEGMENT_COUNTS = (3_000, 10_000)
DEFAULT_MEDIA_DURATION_SECONDS = 36.0

SPEAKERS = (
    ("Alice", "Speaker_Alice", "#7FD957"),
    ("Bob", "Speaker_Bob", "#FFD966"),
    ("Carol", "Speaker_Carol", "#6EC8FF"),
    ("Dave", "Speaker_Dave", "#FF8AB3"),
)


def generate_segments(segment_count: int) -> list[dict[str, Any]]:
    """Create a repeatable, varied subtitle data set without random input."""

    if segment_count <= 0:
        raise ValueError("segment_count must be positive")

    segments: list[dict[str, Any]] = []
    for index in range(segment_count):
        start = round(index * 0.72, 3)
        duration = 1.08 + (index % 5) * 0.09
        end = round(start + duration, 3)
        speaker_name, speaker_style, _color = SPEAKERS[index % len(SPEAKERS)]
        first_word_end = round(start + min(duration * 0.46, 0.55), 3)
        text = f"字幕 {index + 1:05d} {speaker_name} のゲーム実況"
        if index % 19 == 0:
            text += "\n二行目のレイアウト確認"
        segments.append(
            {
                "id": f"performance-segment-{index:05d}",
                "start": start,
                "end": end,
                "text": text,
                "speaker": speaker_style,
                "source_speaker": speaker_name,
                "source_track": f"track-{index % len(SPEAKERS)}",
                "source_file": f"speaker-{index % len(SPEAKERS)}.wav",
                "words": [
                    {
                        "word": f"字幕{index + 1:05d}",
                        "start": start,
                        "end": first_word_end,
                    },
                    {
                        "word": "ゲーム実況",
                        "start": first_word_end,
                        "end": end,
                    },
                ],
                "max_width": 18 + index % 13,
                "subtitle_line_count": ("auto", "1", "2")[index % 3],
                "subtitle_font_scale": round(0.85 + (index % 7) * 0.08, 2),
                "subtitle_font_family": ("", "Arial", "Yu Gothic UI")[index % 3],
                "manual_text": index % 11 == 0,
                "manual_timing": index % 13 == 0,
                "manual_speaker": index % 17 == 0,
                "manual_line_count": index % 3 != 0,
                "manual_font_scale": index % 7 != 0,
                "manual_font_family": index % 3 != 0,
            }
        )
    return segments


def build_fixture_project(
    media_path: Path,
    output_dir: Path,
    segment_count: int,
) -> dict[str, Any]:
    media_path = media_path.resolve()
    output_dir = output_dir.resolve()
    segments = generate_segments(segment_count)
    project_duration = float(segments[-1]["end"])
    speakers = [
        {
            "name": name,
            "style": style,
            "track_key": f"fixture:{name}",
            "file_name": "",
            "path": "",
            "color": color,
        }
        for name, style, color in SPEAKERS
    ]
    waveforms = [
        {
            "speaker": name,
            "style": style,
            "color": color,
            "source_path": "",
            "offset_seconds": 0.0,
            "duration_seconds": project_duration,
            "sample_rate": 400,
            "peaks": [round(((sample * (speaker_index + 3)) % 17) / 16, 4) for sample in range(96)],
        }
        for speaker_index, (name, style, color) in enumerate(SPEAKERS)
    ]
    project = create_project(
        video_path=media_path,
        output_dir=output_dir,
        segments=segments,
        speakers=speakers,
        waveforms=waveforms,
        subtitle_settings={
            "font_size": 50,
            "outline_color": "000000",
            "outline_thickness": 3,
        },
        transcription={"fixture": "large-gui-performance", "segment_count": segment_count},
        duration_seconds=DEFAULT_MEDIA_DURATION_SECONDS,
    )
    project["short_video"] = {
        "schema_version": SHORT_VIDEO_SCHEMA_VERSION,
        "enabled": True,
        "output": {"width": 1080, "height": 1920, "fps": 30},
        "global_fit": "cover",
        "global_background_color": "000000",
        "subtitle_scale_percent": 150,
        "transition": {"type": "crossfade", "duration": 0.5},
        "bgm": {"path": "", "in": 0.0, "out": 0.0, "start": 0.0, "volume": 0.3},
        "clips": [
            {
                "segment_id": str(segment["id"]),
                "start": float(segment["start"]),
                # The first clip spans the synthetic media so visual-only
                # changes cannot coincide with a natural playback stop.
                "end": (DEFAULT_MEDIA_DURATION_SECONDS - 1.0 if index == 0 else float(segment["end"])),
            }
            for index, segment in enumerate(segments)
        ],
    }
    project = validate_project(project)
    project["created_at"] = FIXTURE_TIMESTAMP
    project["updated_at"] = FIXTURE_TIMESTAMP
    return project


def write_fixture_project(
    path: Path,
    *,
    media_path: Path,
    segment_count: int,
) -> Path:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    project = build_fixture_project(media_path, path.parent, segment_count)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(
        json.dumps(project, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)
    return path


def generate_synthetic_media(
    path: Path,
    *,
    duration_seconds: float = DEFAULT_MEDIA_DURATION_SECONDS,
    force: bool = False,
) -> Path:
    if duration_seconds < 31.0:
        raise ValueError("duration_seconds must be at least 31 seconds")
    path = path.resolve()
    if path.is_file() and not force:
        return path
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is required to generate the synthetic media fixture")
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size=320x180:rate=15:duration={duration_seconds}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate=48000:duration={duration_seconds}",
        "-shortest",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(path),
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Synthetic media generation failed.\n"
            f"command: {subprocess.list2cmdline(command)}\n"
            f"stderr:\n{completed.stderr or '(empty)'}"
        )
    return path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate licensed-safe media and deterministic large GUI project fixtures.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--segment-count",
        type=int,
        action="append",
        dest="segment_counts",
        help="Subtitle count to generate; may be supplied more than once (default: 3000 and 10000).",
    )
    parser.add_argument(
        "--media-duration-seconds",
        type=float,
        default=DEFAULT_MEDIA_DURATION_SECONDS,
    )
    parser.add_argument(
        "--project-only",
        action="store_true",
        help="Write project JSON without running ffmpeg (the referenced media may not exist).",
    )
    parser.add_argument("--force-media", action="store_true")
    args = parser.parse_args(argv)
    args.segment_counts = args.segment_counts or list(DEFAULT_SEGMENT_COUNTS)
    if any(count <= 0 for count in args.segment_counts):
        parser.error("--segment-count must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    media_path = output_dir / "synthetic-gui-performance.mp4"
    if not args.project_only:
        generate_synthetic_media(
            media_path,
            duration_seconds=args.media_duration_seconds,
            force=args.force_media,
        )

    projects = [
        write_fixture_project(
            output_dir / f"large-{segment_count}.subtitle-project.json",
            media_path=media_path,
            segment_count=segment_count,
        )
        for segment_count in args.segment_counts
    ]
    print(
        json.dumps(
            {
                "media": str(media_path),
                "media_exists": media_path.is_file(),
                "projects": [str(project) for project in projects],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
