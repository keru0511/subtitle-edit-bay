from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from .ass_template import (
    DEFAULT_SUBTITLE_FONT_SIZE,
    DEFAULT_SUBTITLE_OUTLINE_COLOR,
    DEFAULT_SUBTITLE_OUTLINE_THICKNESS,
    build_ass_header,
)
from .color_config import load_speaker_color_map, normalize_color_key
from .models import SubtitleEvent
from .subtitle_line_count import pack_segments_with_line_count
from .subtitle_layout.packer import (
    DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
    MAX_LINES,
    normalize_text,
)

DEFAULT_SPEAKER_STYLE = {
    "Oz": "Oz",
    "Guest": "Guest",
    "A": "A",
    "B": "B",
    "C": "C",
}
ROW_MARGIN_BASE = 34
ROW_MARGIN_STEP = 156

_STYLE_TOKEN_RE = re.compile(r"[^0-9A-Za-z\u0080-\uFFFF]+")
_STYLE_TOKEN_SAFE_RE = re.compile(r"^[0-9A-Za-z_]+$")


def resolve_row_margin_step(
    subtitle_font_size: int,
    events: list[SubtitleEvent] | None = None,
    height: int = 1080,
) -> int:
    max_font_scale = max(
        (max(0.1, float(event.metadata.get("subtitle_font_scale", 1.0))) for event in events or []),
        default=1.0,
    )
    scaled_step = round(
        ROW_MARGIN_STEP * max(1.0, subtitle_font_size / DEFAULT_SUBTITLE_FONT_SIZE * max_font_scale)
    )
    max_layer = max((max(0, int(event.layer)) for event in events or []), default=0)
    if max_layer == 0:
        return scaled_step
    scaled_font_size = max(3, round(subtitle_font_size * max_font_scale))
    available = max(1, int(height) - ROW_MARGIN_BASE - scaled_font_size)
    return max(1, min(scaled_step, available // max_layer))


def format_ass_time(seconds: float) -> str:
    total_cs = max(0, round(seconds * 100))
    hours, rem = divmod(total_cs, 360000)
    minutes, rem = divmod(rem, 6000)
    secs, centis = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def parse_track_color_args(values: list[str] | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for value in values or []:
        if "=" not in value:
            raise SystemExit(f"--track-color must be TRACK=COLOR, got: {value}")
        track, color = value.split("=", 1)
        mapping[track.strip()] = color.strip()
    return mapping


def style_name_for_track(track: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z]+", "_", track).strip("_") or "track"
    return f"Track_{normalized}"


def _style_token_for_speaker(value: str) -> str:
    raw = str(value).strip()
    token = _STYLE_TOKEN_RE.sub("_", raw).strip("_")
    if not token:
        token = "speaker"
    if _STYLE_TOKEN_SAFE_RE.fullmatch(token) and raw == token:
        return token
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"{token[:48]}_{digest}"


def style_name_for_speaker(speaker: str) -> str:
    normalized = _style_token_for_speaker(speaker)
    return f"Speaker_{normalized}"


def infer_base_style(event: SubtitleEvent) -> str:
    base_style = DEFAULT_SPEAKER_STYLE.get(event.speaker)
    if base_style is None:
        base_style = "UNKNOWN"
    return base_style


def resolve_event_style_override(
    event: SubtitleEvent,
    speaker_color_map: dict[str, str] | None = None,
    track_color_map: dict[str, str] | None = None,
) -> tuple[str, str] | None:
    source_file = str(event.metadata.get("source_file", "")).strip()
    source_speaker = str(event.metadata.get("source_speaker", "")).strip()
    source_track = str(event.metadata.get("source_track", "")).strip()
    if speaker_color_map and source_file:
        color = speaker_color_map.get(normalize_color_key(source_file))
        if color:
            return style_name_for_speaker(source_file), color
    if speaker_color_map and source_speaker:
        color = speaker_color_map.get(normalize_color_key(source_speaker))
        if color:
            return style_name_for_speaker(source_speaker), color
    if track_color_map and source_track and source_track in track_color_map:
        return style_name_for_track(source_track), track_color_map[source_track]
    return None


def infer_style(
    event: SubtitleEvent,
    speaker_color_map: dict[str, str] | None = None,
    track_color_map: dict[str, str] | None = None,
) -> str:
    base_style = infer_base_style(event)
    override = resolve_event_style_override(event, speaker_color_map=speaker_color_map, track_color_map=track_color_map)
    if override is not None:
        chosen, _color = override
        return f"Shout{chosen}" if event.emphasis == "shout" else chosen
    if event.emphasis == "shout":
        return f"Shout{base_style}"
    return base_style


def build_track_style_overrides(
    events: list[SubtitleEvent],
    speaker_color_map: dict[str, str] | None = None,
    track_color_map: dict[str, str] | None = None,
) -> dict[str, tuple[str, str]]:
    overrides: dict[str, tuple[str, str]] = {}
    for event in events:
        override = resolve_event_style_override(event, speaker_color_map=speaker_color_map, track_color_map=track_color_map)
        if override is None:
            continue
        base_style = infer_base_style(event)
        style_name, color = override
        overrides[style_name] = (base_style, color)
        overrides[f"Shout{style_name}"] = (f"Shout{base_style}", color)
    return overrides


def parse_segments(
    data: dict,
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
) -> list[SubtitleEvent]:
    return pack_segments_with_line_count(
        data,
        subtitle_max_gap_seconds=subtitle_max_gap_seconds,
        subtitle_end_padding_seconds=subtitle_end_padding_seconds,
        subtitle_min_duration_seconds=subtitle_min_duration_seconds,
    )


def sanitize_ass_font_family(value: object) -> str:
    return "".join(
        char
        for char in str(value).strip()
        if char not in "{}\\" and char >= " " and char != "\x7f"
    )[:256]


def escape_ass_text(value: object) -> str:
    text = str(value)
    result: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] in ("N", "n"):
            result.append(text[index : index + 2])
            index += 2
        elif char == "{":
            result.append("\\{")
            index += 1
        elif char == "}":
            result.append("\\}")
            index += 1
        elif char == "\\":
            result.append("\\\\")
            index += 1
        else:
            result.append(char)
            index += 1
    return "".join(result)

def sanitize_ass_text(value: object) -> str:
    sanitized = str(value).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    sanitized = sanitized.replace("{", "_").replace("}", "_").replace("\\", "_").replace(",", "_")
    return "".join(char for char in sanitized if char >= " " and char != "\x7f")


def render_dialogue(
    event: SubtitleEvent,
    speaker_color_map: dict[str, str] | None = None,
    track_color_map: dict[str, str] | None = None,
    subtitle_font_size: int = DEFAULT_SUBTITLE_FONT_SIZE,
    row_margin_step: int = ROW_MARGIN_STEP,
) -> str:
    style = infer_style(event, speaker_color_map=speaker_color_map, track_color_map=track_color_map)
    font_scale = max(0.1, float(event.metadata.get("subtitle_font_scale", 1.0)))
    scaled_font_size = max(3, round(subtitle_font_size * font_scale))
    font_family = sanitize_ass_font_family(event.metadata.get("subtitle_font_family", ""))
    overrides = ""
    if font_family:
        overrides += f"\\fn{font_family}"
    if scaled_font_size != subtitle_font_size:
        overrides += f"\\fs{scaled_font_size}"
    dialogue_text = escape_ass_text(event.text)
    if overrides:
        dialogue_text = f"{{{overrides}}}{dialogue_text}"
    actor = sanitize_ass_text(event.speaker)
    margin_v = ROW_MARGIN_BASE + max(0, event.layer) * row_margin_step
    return (
        "Dialogue: "
        f"{event.layer},{format_ass_time(event.start)},{format_ass_time(event.end)},"
        f"{style},{actor},0,0,{margin_v},,{dialogue_text}"
    )


def render_ass(
    data: dict,
    width: int = 1920,
    height: int = 1080,
    track_color_map: dict[str, str] | None = None,
    speaker_color_map: dict[str, str] | None = None,
    subtitle_max_gap_seconds: float = DEFAULT_SUBTITLE_MAX_GAP_SECONDS,
    subtitle_end_padding_seconds: float = DEFAULT_SUBTITLE_END_PADDING_SECONDS,
    subtitle_min_duration_seconds: float = DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
    subtitle_font_size: int = DEFAULT_SUBTITLE_FONT_SIZE,
    subtitle_outline_color: str = DEFAULT_SUBTITLE_OUTLINE_COLOR,
    subtitle_outline_thickness: int = DEFAULT_SUBTITLE_OUTLINE_THICKNESS,
) -> str:
    events = parse_segments(
        data,
        subtitle_max_gap_seconds=subtitle_max_gap_seconds,
        subtitle_end_padding_seconds=subtitle_end_padding_seconds,
        subtitle_min_duration_seconds=subtitle_min_duration_seconds,
    )
    row_margin_step = resolve_row_margin_step(
        subtitle_font_size=subtitle_font_size,
        events=events,
        height=height,
    )
    resolved_speaker_color_map = load_speaker_color_map() if speaker_color_map is None else speaker_color_map
    style_overrides = build_track_style_overrides(
        events,
        speaker_color_map=resolved_speaker_color_map,
        track_color_map=track_color_map,
    )
    lines = [build_ass_header(
        width=width,
        height=height,
        style_overrides=style_overrides,
        subtitle_font_size=subtitle_font_size,
        subtitle_outline_color=subtitle_outline_color,
        subtitle_outline_thickness=subtitle_outline_thickness,
    )]
    lines.extend(
        render_dialogue(
            event,
            speaker_color_map=resolved_speaker_color_map,
            track_color_map=track_color_map,
            subtitle_font_size=subtitle_font_size,
            row_margin_step=row_margin_step,
        )
        for event in events
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Render ASS subtitles from WhisperX-like JSON.")
    parser.add_argument("--input", required=True, help="Path to transcript JSON.")
    parser.add_argument("--output", required=True, help="Path to output ASS file.")
    parser.add_argument("--width", type=int, default=1920, help="Video width.")
    parser.add_argument("--height", type=int, default=1080, help="Video height.")
    parser.add_argument("--subtitle-font-size", type=int, default=DEFAULT_SUBTITLE_FONT_SIZE, help="Base ASS subtitle font size.")
    parser.add_argument("--subtitle-outline-color", default=DEFAULT_SUBTITLE_OUTLINE_COLOR, help="Global subtitle outline color such as #000000.")
    parser.add_argument("--subtitle-outline-thickness", type=int, default=DEFAULT_SUBTITLE_OUTLINE_THICKNESS, help="Global subtitle outline thickness from 0 to 20.")
    parser.add_argument("--track-color", action="append", default=[], help="Per-track subtitle color like 0:a:1=#FFFFFF.")
    args = parser.parse_args()

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    ass = render_ass(
        data,
        width=args.width,
        height=args.height,
        subtitle_font_size=args.subtitle_font_size,
        subtitle_outline_color=args.subtitle_outline_color,
        subtitle_outline_thickness=args.subtitle_outline_thickness,
        track_color_map=parse_track_color_args(args.track_color),
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(ass, encoding="utf-8")


if __name__ == "__main__":
    main()
