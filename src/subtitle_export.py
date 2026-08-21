"""Deterministic subtitle exports that never overwrite implicitly."""

from __future__ import annotations

import csv
import io
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


class SubtitleExportError(ValueError):
    """Raised when subtitle data cannot be exported safely."""


def _time_value(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SubtitleExportError(f"{field} must be a number") from exc
    if result < 0:
        raise SubtitleExportError(f"{field} must not be negative")
    return result


def format_timestamp(seconds: float, separator: str = ",") -> str:
    """Format seconds as an SRT/VTT timestamp."""

    value = _time_value(seconds, "timestamp")
    total_milliseconds = int(value * 1000 + 0.5)
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}{separator}{milliseconds:03d}"


def _segments(segments: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, Mapping):
            raise SubtitleExportError(f"segment {index} must be an object")
        start = _time_value(segment.get("start"), "start")
        end = _time_value(segment.get("end"), "end")
        if end <= start:
            raise SubtitleExportError(f"segment {index} must end after it starts")
        text = segment.get("text", "")
        if text is None:
            text = ""
        if not isinstance(text, str):
            raise SubtitleExportError(f"segment {index} text must be a string")
        item = dict(segment)
        item.update(start=start, end=end, text=text.replace("\r\n", "\n").replace("\r", "\n"))
        item.setdefault("id", str(index + 1))
        item.setdefault("speaker", "")
        normalized.append(item)
    return sorted(enumerate(normalized), key=lambda pair: (pair[1]["start"], pair[0])) and [
        item for _, item in sorted(enumerate(normalized), key=lambda pair: (pair[1]["start"], pair[0]))
    ]


def _atomic_write(destination: str | os.PathLike[str], content: str, overwrite: bool) -> Path:
    path = Path(destination)
    if path.exists() and not overwrite:
        raise SubtitleExportError(f"destination already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists() and not overwrite:
            raise SubtitleExportError(f"destination already exists: {path}")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return path


def export_srt(
    segments: Sequence[Mapping[str, Any]], destination: str | os.PathLike[str], *, overwrite: bool = False
) -> Path:
    rows = _segments(segments)
    blocks = [
        f"{index}\n{format_timestamp(row['start'])} --> {format_timestamp(row['end'])}\n{row['text']}"
        for index, row in enumerate(rows, start=1)
    ]
    return _atomic_write(destination, "\n\n".join(blocks) + ("\n" if blocks else ""), overwrite)


def export_vtt(
    segments: Sequence[Mapping[str, Any]], destination: str | os.PathLike[str], *, overwrite: bool = False
) -> Path:
    rows = _segments(segments)
    blocks = [
        f"{format_timestamp(row['start'], '.')} --> {format_timestamp(row['end'], '.')}\n{row['text']}"
        for row in rows
    ]
    return _atomic_write(destination, "WEBVTT\n\n" + "\n\n".join(blocks) + ("\n" if blocks else ""), overwrite)


def export_csv(
    segments: Sequence[Mapping[str, Any]], destination: str | os.PathLike[str], *, overwrite: bool = False
) -> Path:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("id", "start", "end", "speaker", "text"))
    for row in _segments(segments):
        writer.writerow((row["id"], row["start"], row["end"], row["speaker"], row["text"]))
    return _atomic_write(destination, output.getvalue(), overwrite)


def export_subtitles(
    segments: Sequence[Mapping[str, Any]],
    destination: str | os.PathLike[str],
    format_name: str,
    *,
    overwrite: bool = False,
) -> Path:
    exporters = {"srt": export_srt, "vtt": export_vtt, "csv": export_csv}
    try:
        exporter = exporters[format_name.lower().lstrip(".")]
    except KeyError as exc:
        raise SubtitleExportError(f"unsupported subtitle format: {format_name}") from exc
    return exporter(segments, destination, overwrite=overwrite)
