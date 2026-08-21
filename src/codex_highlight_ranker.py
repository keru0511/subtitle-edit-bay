from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


SEMANTIC_CATEGORIES = {"conversation", "emphasis", "reaction", "gameplay", "other"}


class HighlightRankerClient(Protocol):
    def thread_start(self, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]: ...
    def turn_start(self, **kwargs: Any) -> Mapping[str, Any]: ...


class HighlightRankingError(ValueError):
    pass


@dataclass(frozen=True)
class HighlightRankerSettings:
    max_candidates: int = 12
    max_excerpt_chars: int = 120
    local_weight: float = 0.65
    semantic_weight: float = 0.35
    model_version: str = "codex-default"
    prompt_version: str = "highlight-ranker-v1"
    schema_version: str = "highlight-ranker-schema-v1"


@dataclass(frozen=True)
class HighlightRankingResult:
    candidates: tuple[dict[str, Any], ...]
    fallback: bool
    error: str = ""
    cache_key: str = ""


def build_ranker_context(
    candidates: Sequence[Mapping[str, Any]],
    settings: HighlightRankerSettings | None = None,
) -> dict[str, Any]:
    settings = settings or HighlightRankerSettings()
    payload: list[dict[str, Any]] = []
    for candidate in list(candidates)[: max(0, settings.max_candidates)]:
        excerpt_limit = max(0, int(settings.max_excerpt_chars))
        payload.append(
            {
                "id": str(candidate.get("id", "")),
                "start": float(candidate.get("start", 0.0)),
                "end": float(candidate.get("end", 0.0)),
                "local_score": float(candidate.get("score", 0.0)),
                "category": str(candidate.get("category", "other")),
                "reason": str(candidate.get("reason", ""))[:excerpt_limit],
                "subtitle_excerpt": str(candidate.get("subtitle_excerpt", ""))[:excerpt_limit],
            }
        )
    return {"candidates": payload, "candidate_count": len(payload)}


def rank_highlight_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    client: HighlightRankerClient | None = None,
    settings: HighlightRankerSettings | None = None,
    revision: int | None = None,
) -> HighlightRankingResult:
    settings = settings or HighlightRankerSettings()
    local = [dict(item) for item in candidates]
    context = build_ranker_context(local, settings)
    cache_key = build_ranker_cache_key(context, settings, revision)
    if client is None:
        return HighlightRankingResult(tuple(local), True, "Codex client is unavailable", cache_key)
    try:
        thread = client.thread_start()
        thread_id = str(thread.get("threadId", thread.get("id", "")))
        if not thread_id:
            raise HighlightRankingError("Codex thread id is missing")
        response = client.turn_start(
            thread_id=thread_id,
            prompt="候補の意味的な見どころ度を評価し、候補IDごとに返してください",
            output_schema=RANKING_OUTPUT_SCHEMA,
            context=context,
        )
        raw = response.get("output", response.get("ranking", response))
        if isinstance(raw, str):
            raw = json.loads(raw)
        semantic = _validate_ranking(raw, local)
        ranked = _combine_and_sort(local, semantic, settings)
        return HighlightRankingResult(tuple(ranked), False, "", cache_key)
    except Exception as error:
        return HighlightRankingResult(tuple(local), True, str(error), cache_key)


def build_ranker_cache_key(
    context: Mapping[str, Any],
    settings: HighlightRankerSettings,
    revision: int | None,
) -> str:
    payload = {
        "context": context,
        "model_version": settings.model_version,
        "prompt_version": settings.prompt_version,
        "schema_version": settings.schema_version,
        "local_weight": settings.local_weight,
        "semantic_weight": settings.semantic_weight,
        "revision": revision,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


RANKING_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rankings"],
    "properties": {
        "rankings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "semantic_score", "category", "reason", "hook"],
                "properties": {
                    "id": {"type": "string"},
                    "semantic_score": {"type": "number", "minimum": 0, "maximum": 1},
                    "category": {"type": "string"},
                    "reason": {"type": "string"},
                    "hook": {"type": "string"},
                },
            },
        }
    },
}


def _validate_ranking(raw: Any, local: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("rankings"), list):
        raise HighlightRankingError("Codex output must contain rankings")
    known = {str(item.get("id")): item for item in local}
    validated: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for item in raw["rankings"]:
        if not isinstance(item, Mapping):
            raise HighlightRankingError("ranking item must be an object")
        unknown = set(item) - {"id", "semantic_score", "category", "reason", "hook"}
        if unknown:
            raise HighlightRankingError(f"unknown ranking fields: {sorted(unknown)}")
        candidate_id = str(item.get("id", ""))
        if candidate_id not in known:
            raise HighlightRankingError(f"unknown candidate id: {candidate_id}")
        if candidate_id in seen:
            raise HighlightRankingError(f"duplicate candidate id: {candidate_id}")
        seen.add(candidate_id)
        score = float(item.get("semantic_score"))
        if not 0.0 <= score <= 1.0:
            raise HighlightRankingError(f"invalid semantic score for {candidate_id}")
        category = str(item.get("category", ""))
        if category not in SEMANTIC_CATEGORIES:
            raise HighlightRankingError(f"invalid category for {candidate_id}: {category}")
        validated[candidate_id] = {
            "semantic_score": score,
            "semantic_category": category,
            "semantic_reason": str(item.get("reason", ""))[:500],
            "hook": str(item.get("hook", ""))[:160],
        }
    expected = set(known)
    if seen != expected:
        missing = sorted(expected - seen)
        extra = sorted(seen - expected)
        raise HighlightRankingError(
            f"Codex ranking ids must match candidates exactly; missing={missing}, extra={extra}"
        )
    return validated


def _combine_and_sort(
    candidates: Sequence[Mapping[str, Any]],
    semantic: Mapping[str, Mapping[str, Any]],
    settings: HighlightRankerSettings,
) -> list[dict[str, Any]]:
    total_weight = settings.local_weight + settings.semantic_weight
    if total_weight <= 0:
        raise HighlightRankingError("ranking weights must have a positive sum")
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        item = dict(candidate)
        item_semantic = semantic.get(str(item.get("id")))
        if item_semantic is None:
            item_semantic = {"semantic_score": 0.0, "semantic_category": "other", "semantic_reason": "", "hook": ""}
        local_score = max(0.0, min(1.0, float(item.get("score", 0.0))))
        combined = (
            settings.local_weight * local_score
            + settings.semantic_weight * float(item_semantic["semantic_score"])
        ) / total_weight
        item.update(item_semantic)
        item["local_score"] = round(local_score, 4)
        item["semantic_score"] = round(float(item_semantic["semantic_score"]), 4)
        item["score"] = round(combined, 4)
        item["reason"] = f"{item.get('reason', '')} / Codex: {item_semantic['semantic_reason']}".strip(" /")
        result.append(item)
    ordered = sorted(
        result,
        key=lambda item: (-float(item["score"]), float(item.get("start", 0.0)), str(item.get("id", ""))),
    )
    return _preserve_candidate_constraints(ordered, settings.max_candidates)


def _preserve_candidate_constraints(
    candidates: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    if not candidates or limit <= 0:
        return []
    non_overlapping: list[dict[str, Any]] = []
    for item in candidates:
        if not _overlaps_selected(item, non_overlapping):
            non_overlapping.append(item)
    if len(non_overlapping) <= 1:
        return non_overlapping[:limit]

    bucket_count = min(5, limit, len(non_overlapping))
    max_end = max(float(item.get("end", 0.0)) for item in non_overlapping)
    buckets: dict[int, list[dict[str, Any]]] = {index: [] for index in range(bucket_count)}
    for item in non_overlapping:
        start = max(0.0, float(item.get("start", 0.0)))
        bucket = min(bucket_count - 1, int(start / max(1.0, max_end) * bucket_count))
        buckets[bucket].append(item)

    selected: list[dict[str, Any]] = []
    for bucket in range(bucket_count):
        choices = [item for item in buckets[bucket] if not _overlaps_selected(item, selected)]
        if choices and len(selected) < limit:
            selected.append(
                max(
                    choices,
                    key=lambda item: (
                        float(item.get("score", 0.0)),
                        -float(item.get("start", 0.0)),
                        str(item.get("id", "")),
                    ),
                )
            )
    for item in non_overlapping:
        if len(selected) >= limit:
            break
        if item not in selected and not _overlaps_selected(item, selected):
            selected.append(item)
    return sorted(
        selected,
        key=lambda item: (
            -float(item.get("score", 0.0)),
            float(item.get("start", 0.0)),
            str(item.get("id", "")),
        ),
    )


def _overlaps_selected(candidate: Mapping[str, Any], selected: list[Mapping[str, Any]]) -> bool:
    start = float(candidate.get("start", 0.0))
    end = float(candidate.get("end", start))
    duration = max(0.001, end - start)
    for item in selected:
        overlap = max(0.0, min(end, float(item.get("end", 0.0))) - max(start, float(item.get("start", 0.0))))
        if overlap / min(duration, max(0.001, float(item.get("end", 0.0)) - float(item.get("start", 0.0)))) >= 0.55:
            return True
    return False
