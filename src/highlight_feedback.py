from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


FEEDBACK_SCHEMA_VERSION = 1
ALLOWED_EVENTS = {"accepted", "rejected", "skipped", "boundary_adjusted"}
ALLOWED_FEATURES = {"text", "intensity", "speaker_diversity", "duration", "category"}
NUMERIC_FEATURES = {"text", "intensity", "speaker_diversity", "duration"}


@dataclass(frozen=True)
class HighlightFeedbackEvent:
    candidate_id: str
    event: str
    features: Mapping[str, float | str]
    timestamp: str

    @classmethod
    def create(
        cls,
        candidate_id: str,
        event: str,
        features: Mapping[str, float | str] | None = None,
    ) -> "HighlightFeedbackEvent":
        if event not in ALLOWED_EVENTS:
            raise ValueError(f"unsupported feedback event: {event}")
        safe_features: dict[str, float | str] = {}
        for key, value in (features or {}).items():
            feature = str(key)
            if feature not in ALLOWED_FEATURES:
                continue
            if feature in NUMERIC_FEATURES:
                if type(value) not in (int, float) or not math.isfinite(float(value)):
                    continue
                safe_features[feature] = float(value)
            elif feature == "category" and isinstance(value, str):
                safe_features[feature] = value
        return cls(
            candidate_id=str(candidate_id),
            event=event,
            features=safe_features,
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "event": self.event,
            "features": dict(self.features),
            "timestamp": self.timestamp,
        }


class HighlightFeedbackStore:
    """User-local feedback store that intentionally excludes subtitle text and paths."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.events: list[HighlightFeedbackEvent] = []
        self.load()

    def load(self) -> list[HighlightFeedbackEvent]:
        if not self.path.is_file():
            self.events = []
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or int(payload.get("schema_version", 0)) != FEEDBACK_SCHEMA_VERSION:
            raise ValueError("unsupported highlight feedback schema")
        raw_events = payload.get("events", [])
        if not isinstance(raw_events, list):
            raise ValueError("highlight feedback events must be an array")
        self.events = [self._from_json(item) for item in raw_events]
        return list(self.events)

    def record(
        self,
        candidate_id: str,
        event: str,
        features: Mapping[str, float | str] | None = None,
    ) -> HighlightFeedbackEvent:
        created = HighlightFeedbackEvent.create(candidate_id, event, features)
        next_events = [*self.events, created]
        self._save_events(next_events)
        self.events = next_events
        return created

    def save(self) -> None:
        self._save_events(self.events)

    def _save_events(self, events: Iterable[HighlightFeedbackEvent]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.path.parent, prefix="highlight-feedback-", suffix=".tmp", delete=False
        )
        temp_path = Path(handle.name)
        try:
            with handle:
                json.dump(
                    {"schema_version": FEEDBACK_SCHEMA_VERSION, "events": [item.to_json() for item in events]},
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
            os.replace(temp_path, self.path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def export(self) -> dict[str, Any]:
        return {"schema_version": FEEDBACK_SCHEMA_VERSION, "events": [item.to_json() for item in self.events]}

    def reset(self) -> None:
        self._save_events([])
        self.events = []

    def delete(self) -> None:
        self.events = []
        self.path.unlink(missing_ok=True)

    @staticmethod
    def _from_json(payload: Mapping[str, Any]) -> HighlightFeedbackEvent:
        event = HighlightFeedbackEvent.create(
            str(payload.get("candidate_id", "")),
            str(payload.get("event", "")),
            payload.get("features") if isinstance(payload.get("features"), Mapping) else {},
        )
        return HighlightFeedbackEvent(
            candidate_id=event.candidate_id,
            event=event.event,
            features=event.features,
            timestamp=str(payload.get("timestamp", event.timestamp)),
        )

