from __future__ import annotations

DEFAULT_SUBTITLE_FONT_SIZE = 50

STYLE_DEFINITIONS = {
    "Oz": "Style: Oz,Arial,50,&H00FFFFFF,&H0000FFFF,&H003030FF,&H66000000,-1,0,0,0,100,100,0,0,1,3,1,2,36,36,34,1",
    "Guest": "Style: Guest,Arial,50,&H00FFFFFF,&H0000FFFF,&H003030FF,&H66000000,-1,0,0,0,100,100,0,0,1,3,1,2,36,36,34,1",
    "A": "Style: A,Arial,50,&H00FFFFFF,&H0000FFFF,&H003030FF,&H66000000,-1,0,0,0,100,100,0,0,1,3,1,2,36,36,34,1",
    "B": "Style: B,Arial,50,&H00FFFFFF,&H0000FFFF,&H003030FF,&H66000000,-1,0,0,0,100,100,0,0,1,3,1,2,36,36,34,1",
    "C": "Style: C,Arial,50,&H00FFFFFF,&H0000FFFF,&H003030FF,&H66000000,-1,0,0,0,100,100,0,0,1,3,1,2,36,36,34,1",
    "UNKNOWN": "Style: UNKNOWN,Arial,50,&H00FFFFFF,&H0000FFFF,&H003030FF,&H66000000,-1,0,0,0,100,100,0,0,1,3,1,2,36,36,34,1",
    "ShoutOz": "Style: ShoutOz,Arial,50,&H00FFFFFF,&H0000FFFF,&H000000FF,&H66000000,-1,0,0,0,100,100,0,0,1,4,2,2,36,36,34,1",
    "ShoutGuest": "Style: ShoutGuest,Arial,50,&H00FFFFFF,&H0000FFFF,&H000000FF,&H66000000,-1,0,0,0,100,100,0,0,1,4,2,2,36,36,34,1",
    "ShoutA": "Style: ShoutA,Arial,50,&H00FFFFFF,&H0000FFFF,&H000000FF,&H66000000,-1,0,0,0,100,100,0,0,1,4,2,2,36,36,34,1",
    "ShoutB": "Style: ShoutB,Arial,50,&H00FFFFFF,&H0000FFFF,&H000000FF,&H66000000,-1,0,0,0,100,100,0,0,1,4,2,2,36,36,34,1",
    "ShoutC": "Style: ShoutC,Arial,50,&H00FFFFFF,&H0000FFFF,&H000000FF,&H66000000,-1,0,0,0,100,100,0,0,1,4,2,2,36,36,34,1",
    "ShoutUNKNOWN": "Style: ShoutUNKNOWN,Arial,50,&H00FFFFFF,&H0000FFFF,&H000000FF,&H66000000,-1,0,0,0,100,100,0,0,1,4,2,2,36,36,34,1",
}


def normalize_ass_color(color: str) -> str:
    normalized = color.strip()
    if normalized.startswith("&H"):
        return normalized.upper()
    if normalized.startswith("#"):
        normalized = normalized[1:]
    if len(normalized) != 6 or any(char not in "0123456789abcdefABCDEF" for char in normalized):
        raise ValueError(f"Unsupported color format: {color}")
    red = normalized[0:2]
    green = normalized[2:4]
    blue = normalized[4:6]
    return f"&H00{blue}{green}{red}".upper()


def clone_style_definition(base_style: str, new_name: str, outline_color: str) -> str:
    fields = STYLE_DEFINITIONS[base_style].split(",")
    fields[0] = f"Style: {new_name}"
    fields[5] = normalize_ass_color(outline_color)
    return ",".join(fields)


def build_extra_style_definitions(style_overrides: dict[str, tuple[str, str]] | None = None) -> list[str]:
    if not style_overrides:
        return []
    return [clone_style_definition(base_style, style_name, color) for style_name, (base_style, color) in style_overrides.items()]


def apply_subtitle_font_size(style_definition: str, subtitle_font_size: int) -> str:
    if subtitle_font_size < 3:
        raise ValueError("subtitle_font_size must be at least 3")
    fields = style_definition.split(",")
    fields[2] = str(int(fields[2]) + subtitle_font_size - DEFAULT_SUBTITLE_FONT_SIZE)
    return ",".join(fields)


def build_ass_header(
    width: int = 1920,
    height: int = 1080,
    style_overrides: dict[str, tuple[str, str]] | None = None,
    subtitle_font_size: int = DEFAULT_SUBTITLE_FONT_SIZE,
) -> str:
    style_definitions = list(STYLE_DEFINITIONS.values()) + build_extra_style_definitions(style_overrides)
    styles = "\n".join(apply_subtitle_font_size(style, subtitle_font_size) for style in style_definitions)
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,"
        "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,"
        "BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"{styles}\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
