"""字幕行数の設定を、組版エンジンに依存せず検証する。"""

from __future__ import annotations

SUBTITLE_LINE_COUNT_AUTO = "auto"
SUPPORTED_SUBTITLE_LINE_COUNTS = {SUBTITLE_LINE_COUNT_AUTO, "1", "2"}


def normalize_subtitle_line_count(value: object = SUBTITLE_LINE_COUNT_AUTO) -> str:
    """Normalize the per-segment subtitle line count override.

    ``auto`` preserves the existing wrapping behavior. ``1`` and ``2`` force the
    maximum visible line count used by the existing text normalizer.
    """
    if value is None:
        return SUBTITLE_LINE_COUNT_AUTO
    if isinstance(value, bool):
        raise ValueError("subtitle_line_count must be 'auto', '1', or '2'")
    if isinstance(value, int) and value in (1, 2):
        return str(value)

    normalized = str(value).strip().lower()
    if normalized == "":
        return SUBTITLE_LINE_COUNT_AUTO
    if normalized in SUPPORTED_SUBTITLE_LINE_COUNTS:
        return normalized
    raise ValueError("subtitle_line_count must be 'auto', '1', or '2'")


def subtitle_line_count_max_lines(value: object = SUBTITLE_LINE_COUNT_AUTO) -> int | None:
    normalized = normalize_subtitle_line_count(value)
    if normalized == SUBTITLE_LINE_COUNT_AUTO:
        return None
    return int(normalized)
