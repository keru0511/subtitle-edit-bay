from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SubtitleEvent:
    start: float
    end: float
    speaker: str
    text: str
    emphasis: str = "normal"
    layer: int = 0
    position: str = "bottom"
    metadata: dict[str, str | float | int | bool] = field(default_factory=dict)
