from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .highlight_feedback import HighlightFeedbackEvent


@dataclass(frozen=True)
class PreferenceSettings:
    enabled: bool = True
    learning_rate: float = 0.08
    max_weight_delta: float = 0.25
    minimum_events: int = 3


@dataclass(frozen=True)
class PreferenceExplanation:
    adjustment: float
    reason: str
    event_count: int


class HighlightPreferenceModel:
    def __init__(
        self,
        events: Iterable[HighlightFeedbackEvent] | None = None,
        *,
        settings: PreferenceSettings | None = None,
    ) -> None:
        self.settings = settings or PreferenceSettings()
        self.events = list(events or [])
        self._weights = self._learn_weights()

    @property
    def weights(self) -> dict[str, float]:
        return dict(self._weights)

    def set_enabled(self, enabled: bool) -> None:
        self.settings = PreferenceSettings(
            enabled=bool(enabled),
            learning_rate=self.settings.learning_rate,
            max_weight_delta=self.settings.max_weight_delta,
            minimum_events=self.settings.minimum_events,
        )

    def rank(self, candidates: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for candidate in candidates:
            item = dict(candidate)
            base = float(item.get("score", 0.0))
            explanation = self.explain(candidate)
            item["baseline_score"] = round(base, 4)
            item["preference_adjustment"] = round(explanation.adjustment, 4)
            item["score"] = round(max(0.0, min(1.0, base + explanation.adjustment)), 4)
            item["preference_explanation"] = explanation.reason
            result.append(item)
        return sorted(result, key=lambda item: (-float(item["score"]), float(item.get("start", 0.0)), str(item.get("id", ""))))

    def explain(self, candidate: Mapping[str, Any]) -> PreferenceExplanation:
        if not self.settings.enabled or len(self.events) < self.settings.minimum_events:
            return PreferenceExplanation(0.0, "履歴が少ないためbaseline順位を使用", len(self.events))
        adjustment = 0.0
        reasons: list[str] = []
        category = str(candidate.get("category", ""))
        for feature, weight in self._weights.items():
            if feature.startswith("category:"):
                value = 1.0 if category == feature.split(":", 1)[1] else 0.0
                display_feature = feature.split(":", 1)[1]
            else:
                value = candidate.get(feature, candidate.get("score_breakdown", {}).get(feature, 0.0))
                display_feature = feature
            if isinstance(value, str):
                value = 1.0 if value == category else 0.0
            try:
                adjustment += float(value) * weight
            except (TypeError, ValueError):
                continue
            if abs(weight) >= 0.03:
                reasons.append(f"{display_feature}傾向 {weight:+.2f}")
        reason = "、".join(reasons) if reasons else "baselineと同じ"
        return PreferenceExplanation(max(-self.settings.max_weight_delta, min(self.settings.max_weight_delta, adjustment)), reason, len(self.events))

    def _learn_weights(self) -> dict[str, float]:
        if len(self.events) < self.settings.minimum_events:
            return {}
        totals: dict[str, float] = {}
        counts: dict[str, int] = {}
        for event in self.events:
            direction = 1.0 if event.event == "accepted" else -1.0 if event.event == "rejected" else 0.0
            if direction == 0.0:
                continue
            for feature, value in event.features.items():
                weight_key = f"category:{value}" if feature == "category" else feature
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    if feature != "category":
                        continue
                    numeric = 1.0
                totals[weight_key] = totals.get(weight_key, 0.0) + direction * numeric * self.settings.learning_rate
                counts[weight_key] = counts.get(weight_key, 0) + 1
        return {
            feature: max(-self.settings.max_weight_delta, min(self.settings.max_weight_delta, totals[feature] / max(1, counts[feature])))
            for feature in totals
        }
