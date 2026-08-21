from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any, Mapping


PROGRESS_EVENT_PREFIX = "PROGRESS_EVENT "
STEP_DISPLAY_STATUS = {
    "pending": "未着手",
    "running": "実行中",
    "completed": "完了",
    "cancelled": "中断",
    "error": "エラー",
}
_FFMPEG_TIMESTAMP_PATTERN = re.compile(
    r"(?:Duration|time)\s*[:=]\s*(?P<hours>\d+):(?P<minutes>\d{2}):(?P<seconds>\d{2}(?:\.\d+)?)"
)


@dataclass(frozen=True)
class ProgressStep:
    id: str
    label: str
    weight: float
    state: str = "pending"
    progress: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "state": self.state,
            "displayStatus": STEP_DISPLAY_STATUS.get(self.state, self.state),
            "progress": self.progress,
        }


STEP_DEFINITIONS: dict[str, tuple[tuple[str, str, float], ...]] = {
    "transcribe": (
        ("prepare", "準備", 0.08),
        ("alignment", "音声同期", 0.20),
        ("transcription", "文字起こし", 0.32),
        ("refine", "字幕の統合・整形", 0.16),
        ("waveform", "波形生成", 0.12),
        ("project", "プロジェクト保存", 0.12),
    ),
    "render": (
        ("prepare", "準備", 0.08),
        ("subtitle", "字幕生成", 0.18),
        ("audio", "音声処理", 0.18),
        ("encode", "動画エンコード", 0.46),
        ("finalize", "出力確定", 0.10),
    ),
    "render_short": (
        ("prepare", "準備", 0.08),
        ("clips", "クリップ構築", 0.22),
        ("transition_audio", "トランジション・音声処理", 0.20),
        ("encode", "動画エンコード", 0.40),
        ("finalize", "出力確定", 0.10),
    ),
}


def progress_event_line(
    job: str,
    step: str,
    *,
    phase: str = "progress",
    progress: float = 0.0,
    duration: float | None = None,
) -> str:
    payload = {
        "job": str(job),
        "step": str(step),
        "phase": str(phase),
        "progress": max(0.0, min(1.0, float(progress))),
    }
    if duration is not None:
        payload["duration"] = max(0.0, float(duration))
    return PROGRESS_EVENT_PREFIX + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def parse_progress_events(output: str) -> tuple[dict[str, Any], ...]:
    events: list[dict[str, Any]] = []
    for line in str(output).splitlines():
        if not line.startswith(PROGRESS_EVENT_PREFIX):
            continue
        try:
            payload = json.loads(line[len(PROGRESS_EVENT_PREFIX) :])
        except (TypeError, ValueError):
            continue
        if isinstance(payload, Mapping) and payload.get("job") and payload.get("step"):
            events.append(dict(payload))
    return tuple(events)


def parse_ffmpeg_timestamp(line: str) -> float | None:
    """Return a non-negative FFmpeg timestamp from a log line, if present."""
    match = _FFMPEG_TIMESTAMP_PATTERN.search(str(line))
    if match is None:
        return None
    try:
        return (
            int(match.group("hours")) * 3600
            + int(match.group("minutes")) * 60
            + float(match.group("seconds"))
        )
    except (TypeError, ValueError):
        return None


class ProcessingProgress:
    """Track weighted, monotonic progress for one GUI process."""

    def __init__(self) -> None:
        self.job = ""
        self.status = "idle"
        self.value = 0.0
        self.current_step = ""
        self.steps: tuple[ProgressStep, ...] = ()
        self._weight_total = 0.0

    def start(self, job: str, *, skip_steps: set[str] | None = None) -> None:
        skipped = skip_steps or set()
        definitions = tuple(
            definition
            for definition in STEP_DEFINITIONS.get(str(job), ())
            if definition[0] not in skipped
        )
        self.job = str(job)
        self.status = "running"
        self.value = 0.0
        self.current_step = ""
        self.steps = tuple(ProgressStep(*definition) for definition in definitions)
        self._weight_total = sum(step.weight for step in self.steps)

    def update(self, event: Mapping[str, Any]) -> bool:
        if str(event.get("job", "")) != self.job:
            return False
        step_id = str(event.get("step", ""))
        index = next((i for i, step in enumerate(self.steps) if step.id == step_id), -1)
        if index < 0:
            return False
        try:
            step_progress = max(0.0, min(1.0, float(event.get("progress", 0.0))))
        except (TypeError, ValueError):
            step_progress = 0.0
        phase = str(event.get("phase", "progress"))
        updated: list[ProgressStep] = []
        for step_index, step in enumerate(self.steps):
            if step_index < index:
                updated.append(replace(step, state="completed", progress=1.0))
            elif step_index == index:
                if phase in {"complete", "completed"}:
                    updated.append(replace(step, state="completed", progress=1.0))
                else:
                    updated.append(replace(step, state="running", progress=step_progress))
            else:
                updated.append(step)
        self.steps = tuple(updated)
        self.current_step = "" if phase in {"complete", "completed"} else step_id
        calculated = sum(step.weight * step.progress for step in self.steps)
        if self._weight_total > 0.0:
            calculated /= self._weight_total
        self.value = max(self.value, min(1.0, calculated))
        return True

    def finish(self, outcome: str) -> None:
        self.status = str(outcome)
        if outcome == "completed":
            self.steps = tuple(
                replace(step, state="completed", progress=1.0) for step in self.steps
            )
            self.value = 1.0
            self.current_step = ""
            return
        if self.current_step:
            terminal_state = "cancelled" if outcome == "cancelled" else "error"
            self.steps = tuple(
                replace(step, state=terminal_state)
                if step.id == self.current_step
                else step
                for step in self.steps
            )

    def as_list(self) -> list[dict[str, Any]]:
        return [step.to_dict() for step in self.steps]
