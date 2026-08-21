from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


VISUAL_SIGNAL_VERSION = "ffmpeg-scene-v1"


@dataclass(frozen=True)
class VisualSignalSettings:
    enabled: bool = False
    sample_fps: float = 1.0
    scale_width: int = 320
    scene_threshold: float = 0.12
    max_windows: int = 30
    timeout_seconds: float = 20.0
    max_runtime_seconds: float = 120.0
    weight: float = 0.15
    ffmpeg_version: str = "unknown"
    signal_version: str = VISUAL_SIGNAL_VERSION


@dataclass(frozen=True)
class VisualSignal:
    timestamp: float
    score: float
    reason: str = "画面変化"


@dataclass(frozen=True)
class VisualSignalResult:
    signals: tuple[VisualSignal, ...]
    fallback: bool = False
    error: str = ""
    elapsed_seconds: float = 0.0


def build_scene_change_command(
    video_path: str | Path,
    *,
    start: float,
    end: float,
    settings: VisualSignalSettings,
) -> list[str]:
    duration = max(0.0, float(end) - float(start))
    filter_graph = (
        f"fps={max(0.1, settings.sample_fps):g},"
        f"scale={max(16, int(settings.scale_width))}:-2:force_original_aspect_ratio=decrease,"
        f"select='gt(scene,{max(0.0, min(1.0, settings.scene_threshold)):g})',"
        "metadata=print"
    )
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "info",
        "-ss",
        f"{max(0.0, float(start)):.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(video_path),
        "-an",
        "-vf",
        filter_graph,
        "-f",
        "null",
        "-",
    ]


def visual_signal_cache_key(
    video_path: str | Path,
    windows: Sequence[tuple[float, float]],
    settings: VisualSignalSettings,
) -> str:
    path = Path(video_path)
    fingerprint = {
        "path": str(path),
        "size": path.stat().st_size if path.is_file() else None,
        "mtime_ns": path.stat().st_mtime_ns if path.is_file() else None,
    }
    payload = {"video": fingerprint, "windows": windows, "settings": asdict(settings)}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def extract_visual_signals(
    video_path: str | Path,
    windows: Iterable[tuple[float, float]],
    *,
    settings: VisualSignalSettings | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    cancel_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[float], None] | None = None,
) -> VisualSignalResult:
    settings = settings or VisualSignalSettings()
    if not settings.enabled:
        return VisualSignalResult((), fallback=True, error="visual signal is disabled")
    started = time.monotonic()
    signals: list[VisualSignal] = []
    selected_windows = list(windows)[: max(0, settings.max_windows)]
    try:
        for index, (start, end) in enumerate(selected_windows):
            if cancel_check and cancel_check():
                return VisualSignalResult((), fallback=True, error="cancelled", elapsed_seconds=time.monotonic() - started)
            elapsed = time.monotonic() - started
            remaining = settings.max_runtime_seconds - elapsed
            if remaining <= 0.0:
                return VisualSignalResult((), fallback=True, error="visual signal budget exceeded", elapsed_seconds=elapsed)
            window_timeout = max(0.01, min(float(settings.timeout_seconds), remaining))
            completed = runner(
                build_scene_change_command(video_path, start=start, end=end, settings=settings),
                capture_output=True,
                text=True,
                timeout=window_timeout,
                check=False,
                shell=False,
            )
            if completed.returncode != 0:
                raise RuntimeError((completed.stderr or "ffmpeg scene analysis failed").strip())
            signals.extend(_parse_scene_metadata(completed.stderr or "", float(start)))
            if progress_callback:
                progress_callback((index + 1) / max(1, len(selected_windows)))
        return VisualSignalResult(tuple(_deduplicate_signals(signals)), elapsed_seconds=time.monotonic() - started)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        return VisualSignalResult(tuple(), fallback=True, error=str(error), elapsed_seconds=time.monotonic() - started)


def blend_visual_scores(
    candidates: Iterable[Mapping[str, Any]],
    signals: Iterable[VisualSignal],
    *,
    settings: VisualSignalSettings | None = None,
) -> list[dict[str, Any]]:
    settings = settings or VisualSignalSettings()
    source = [dict(item) for item in candidates]
    if not settings.enabled or not settings.weight:
        return source
    visual = list(signals)
    result: list[dict[str, Any]] = []
    weight = max(0.0, min(1.0, settings.weight))
    for candidate in source:
        start = float(candidate.get("start", 0.0))
        end = float(candidate.get("end", start))
        matching = [signal.score for signal in visual if start <= signal.timestamp <= end]
        visual_score = max(matching, default=0.0)
        local_score = max(0.0, min(1.0, float(candidate.get("score", 0.0))))
        updated = dict(candidate)
        updated["local_score"] = local_score
        updated["visual_score"] = round(visual_score, 4)
        updated["score"] = round((1.0 - weight) * local_score + weight * visual_score, 4)
        updated["visual_reason"] = "画面変化あり" if matching else "画面signalなし"
        result.append(updated)
    return sorted(result, key=lambda item: (-float(item["score"]), float(item.get("start", 0.0)), str(item.get("id", ""))))


def _parse_scene_metadata(output: str, window_start: float) -> list[VisualSignal]:
    signals: list[VisualSignal] = []
    for line in output.splitlines():
        if "lavfi.scene_score" not in line:
            continue
        score_match = re.search(r"lavfi\.scene_score=([0-9.]+)", line)
        pts_match = re.search(r"pts_time:([0-9.]+)", line)
        if not score_match:
            continue
        score = max(0.0, min(1.0, float(score_match.group(1))))
        timestamp = window_start + (float(pts_match.group(1)) if pts_match else 0.0)
        signals.append(VisualSignal(round(timestamp, 3), round(score, 4)))
    return signals


def _deduplicate_signals(signals: Iterable[VisualSignal]) -> list[VisualSignal]:
    by_timestamp: dict[float, VisualSignal] = {}
    for signal in signals:
        previous = by_timestamp.get(signal.timestamp)
        if previous is None or signal.score > previous.score:
            by_timestamp[signal.timestamp] = signal
    return [by_timestamp[key] for key in sorted(by_timestamp)]

