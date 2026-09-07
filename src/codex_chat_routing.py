from __future__ import annotations

from dataclasses import dataclass
import re


_SUBTITLE_WORD = re.compile(r"字幕|テロップ|caption|subtitle", re.IGNORECASE)
_EDIT_WORD = re.compile(
    r"短く|長く|直して|修正|変更|書き換|整え|自然に|読みやす|校正|翻訳|追加|挿入|削除|消して|分割|結合|まとめ|置換",
    re.IGNORECASE,
)
_ALL_SUBTITLES = re.compile(
    r"(?:すべて|全て|全部|全)(?:の)?(?:字幕|テロップ)|(?:all|every)\s+(?:captions?|subtitles?)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SubtitleChatRoute:
    scope: str
    range_start: float = 0.0
    range_end: float = 0.0


def route_subtitle_chat_request(
    text: str,
    requested_scope: str,
    *,
    project_loaded: bool,
    has_selection: bool,
    current_time: float,
    range_start: float = 0.0,
    range_end: float = 0.0,
) -> SubtitleChatRoute | None:
    """Route an explicit or unambiguous subtitle request to proposal generation.

    ``all`` is never inferred from editor state: it requires either the explicit
    scope control or wording that explicitly names every subtitle.
    """

    prompt = str(text).strip()
    scope = str(requested_scope or "auto")
    explicit_scope = scope in {"selected", "current", "time_range", "all"}
    if not explicit_scope and not (_SUBTITLE_WORD.search(prompt) and _EDIT_WORD.search(prompt)):
        return None
    if not project_loaded:
        return SubtitleChatRoute(scope="unavailable")
    if scope == "time_range":
        return SubtitleChatRoute(scope=scope, range_start=range_start, range_end=range_end)
    if explicit_scope:
        return SubtitleChatRoute(scope=scope)
    if _ALL_SUBTITLES.search(prompt):
        return SubtitleChatRoute(scope="all")
    if has_selection:
        return SubtitleChatRoute(scope="selected")
    return SubtitleChatRoute(scope="current", range_start=current_time, range_end=current_time)
