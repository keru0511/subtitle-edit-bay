"""FFmpeg filter-complex script compatibility helpers."""

from __future__ import annotations

import re
import subprocess
from functools import lru_cache


LEGACY_FILTER_SCRIPT_OPTION = "-filter_complex_script"
MODERN_FILTER_SCRIPT_OPTION = "-/filter_complex"
MINIMUM_SUPPORTED_FFMPEG_MAJOR = 6


class FFmpegFilterScriptCompatibilityError(RuntimeError):
    """Raised when a safe filter script option cannot be selected."""


def _ffmpeg_major_version(version_output: str) -> int | None:
    match = re.search(
        r"^ffmpeg version\s+[nN]?(\d+)(?:\.|\s|$)",
        version_output,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return int(match.group(1)) if match else None


def _compatibility_error(detail: str) -> FFmpegFilterScriptCompatibilityError:
    return FFmpegFilterScriptCompatibilityError(
        f"{detail} FFmpeg 6 以上へ更新し、セットアップ検証を再実行してください。"
    )


def filter_complex_script_option(version_output: str) -> str:
    """Select the filter script option supported by an FFmpeg version."""

    major_version = _ffmpeg_major_version(version_output)
    if major_version is None:
        raise _compatibility_error("FFmpeg のバージョンを判定できませんでした。")
    if major_version < MINIMUM_SUPPORTED_FFMPEG_MAJOR:
        raise _compatibility_error(
            f"FFmpeg {major_version} はフィルタースクリプトのサポート対象外です。"
        )
    if major_version >= 7:
        return MODERN_FILTER_SCRIPT_OPTION
    return LEGACY_FILTER_SCRIPT_OPTION


@lru_cache(maxsize=1)
def detect_filter_complex_script_option() -> str:
    """Detect the local FFmpeg option and provide actionable failures."""

    try:
        process = subprocess.Popen(
            ["ffmpeg", "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        version_output, _ = process.communicate()
    except OSError as error:
        raise _compatibility_error(f"FFmpeg を起動できませんでした: {error}") from error

    if process.returncode != 0:
        first_line = next(
            (line.strip() for line in version_output.splitlines() if line.strip()),
            "出力なし",
        )
        raise _compatibility_error(
            f"FFmpeg のバージョン確認に失敗しました ({first_line})。"
        )
    return filter_complex_script_option(version_output)
