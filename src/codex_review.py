from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence, cast

from .application_logging import redact_text
from .audio_mixer import active_audio_mix_channels, path_free_audio_mix_channels
from .codex_isolation import (
    CodexIsolationError,
    ISOLATED_DISABLED_FEATURES,
    build_isolated_thread_config,
    build_isolated_thread_params,
    build_isolated_turn_kwargs,
    collect_mcp_server_names,
    validate_isolated_cwd,
)


ROUTES = {
    "subtitle": "subtitle_proposal",
    "audio": "audio_mix_proposal",
    "timeline": "timeline_proposal",
    "short": "timeline_proposal",
    "processing": "processing_action",
    "render": "processing_action",
}
REVIEW_CATEGORIES = frozenset({"project", *ROUTES})
SEVERITY_ORDER = {"blocking": 0, "warning": 1, "suggestion": 2}
TARGET_ID_FIELDS = {
    "segment_ids": "segment_ids",
    "channel_ids": "channel_ids",
    "clip_ids": "clip_ids",
    "cut_ids": "cut_ids",
    "candidate_ids": "candidate_ids",
}
MAX_SEGMENT_TEXT_CHARS = 1_000
MAX_SUBTITLE_CHUNK_BYTES = 96 * 1024
MAX_SHORT_CLIPS = 500
MAX_TIMELINE_ITEMS = 500
MAX_HIGHLIGHT_ITEMS = 50
MAX_FINDINGS_PER_TURN = 100
MAX_FINDINGS_PER_RESULT = 1_000
MAX_REASON_CHARS = 800
MAX_ID_CHARS = 160
# Backward-compatible aliases for callers/tests that import the old review names.
MCP_STATUS_PAGE_SIZE = 100
MAX_MCP_STATUS_PAGES = 100
_REVIEW_DISABLED_FEATURES = ISOLATED_DISABLED_FEATURES


class ReviewError(ValueError):
    """Base error for safe review context and result contract failures."""


class ReviewContractError(ReviewError):
    """Raised when a Codex review result violates the strict contract."""


class StaleReviewError(ReviewError):
    """Raised when a project changes while a review is running."""


class ReviewClient(Protocol):
    def mcp_server_status_list(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def thread_start(self, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]: ...

    def run_structured_turn(self, **kwargs: Any) -> Mapping[str, Any]: ...


REVIEW_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["project_revision", "issues", "recommended_order"],
    "properties": {
        "project_revision": {"type": "integer", "minimum": 0},
        "issues": {
            "type": "array",
            "maxItems": MAX_FINDINGS_PER_TURN,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id",
                    "category",
                    "severity",
                    "target",
                    "reason",
                    "recommendation",
                ],
                "properties": {
                    "id": {"type": "string", "minLength": 1, "maxLength": MAX_ID_CHARS},
                    "category": {"type": "string", "enum": sorted(REVIEW_CATEGORIES)},
                    "severity": {"type": "string", "enum": list(SEVERITY_ORDER)},
                    "target": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "segment_ids",
                            "channel_ids",
                            "clip_ids",
                            "cut_ids",
                            "candidate_ids",
                            "start_seconds",
                            "end_seconds",
                        ],
                        "properties": {
                            "segment_ids": {
                                "type": "array",
                                "maxItems": 20,
                                "items": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": MAX_ID_CHARS,
                                },
                            },
                            "channel_ids": {
                                "type": "array",
                                "maxItems": 20,
                                "items": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": MAX_ID_CHARS,
                                },
                            },
                            "clip_ids": {
                                "type": "array",
                                "maxItems": 20,
                                "items": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": MAX_ID_CHARS,
                                },
                            },
                            "cut_ids": {
                                "type": "array",
                                "maxItems": 20,
                                "items": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": MAX_ID_CHARS,
                                },
                            },
                            "candidate_ids": {
                                "type": "array",
                                "maxItems": 20,
                                "items": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": MAX_ID_CHARS,
                                },
                            },
                            "start_seconds": {"type": ["number", "null"], "minimum": 0},
                            "end_seconds": {"type": ["number", "null"], "minimum": 0},
                        },
                    },
                    "reason": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_REASON_CHARS,
                    },
                    "recommendation": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["available", "route"],
                        "properties": {
                            "available": {"type": "boolean"},
                            "route": {
                                "type": "string",
                                "enum": ["", *sorted(set(ROUTES.values()))],
                            },
                        },
                    },
                },
            },
        },
        "recommended_order": {
            "type": "array",
            "maxItems": MAX_FINDINGS_PER_TURN,
            "items": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_ID_CHARS,
            },
        },
    },
}


REVIEW_PROMPT = """あなたは動画編集プロジェクトの横断レビュー担当です。
渡された JSON だけを根拠に、プロジェクトを変更せず問題点を列挙してください。
特に次を確認してください: 不自然な日本語、誤字、重複・欠落した字幕、字幕の長さと間、
音声/BGM の相対バランスと mute/solo、長い無音とテンポ、通常動画のカット、
見どころ候補・却下候補とショートクリップの不整合、目標尺との差、クリップの順序・範囲、
処理失敗、依存関係、書き出し可否。
事前検査 findings は補助情報であり、意味的な問題も独立に判断してください。
target の ID は入力に存在するものだけを使い、該当しない配列は空、時刻は null にしてください。
recommendation は route_availability にある経路だけ available=true にできます。
同じ問題を重複させず、recommended_order には全 issue ID を重要順に一度ずつ入れてください。
出力は指定された JSON Schema に厳密に従ってください。"""


@dataclass(frozen=True)
class ReviewFinding:
    id: str
    category: str
    severity: str
    target: Mapping[str, Any]
    reason: str
    route: str = ""

    def to_json(self) -> dict[str, Any]:
        target: dict[str, Any] = {
            "segment_ids": [],
            "channel_ids": [],
            "clip_ids": [],
            "cut_ids": [],
            "candidate_ids": [],
            "start_seconds": None,
            "end_seconds": None,
        }
        target.update(dict(self.target))
        return {
            "id": self.id,
            "category": self.category,
            "severity": self.severity,
            "target": target,
            "reason": self.reason,
            "recommendation": {"available": bool(self.route), "route": self.route},
        }


@dataclass(frozen=True)
class ReviewResult:
    project_revision: int
    issues: tuple[ReviewFinding, ...]
    recommended_order: tuple[str, ...] = ()

    def ordered_issues(self, *, max_issues: int | None = None) -> tuple[ReviewFinding, ...]:
        rank = {finding_id: index for index, finding_id in enumerate(self.recommended_order)}
        ordered = tuple(
            sorted(
                self.issues,
                key=lambda item: (
                    SEVERITY_ORDER[item.severity],
                    rank.get(item.id, len(rank)),
                    item.category,
                    item.id,
                ),
            )
        )
        if max_issues is None:
            return ordered
        return ordered[: max(0, int(max_issues))]

    def to_json(self, *, max_issues: int = MAX_FINDINGS_PER_RESULT) -> dict[str, Any]:
        all_ordered = self.ordered_issues()
        ordered = all_ordered[: max(0, int(max_issues))]
        remaining_count = len(all_ordered) - len(ordered)
        return {
            "project_revision": self.project_revision,
            "issues": [item.to_json() for item in ordered],
            "recommended_order": [item.id for item in ordered],
            "truncated": remaining_count > 0,
            "remaining_count": remaining_count,
        }

    def require_current_revision(self, current_revision: int) -> None:
        if self.project_revision != current_revision:
            raise StaleReviewError("review result is stale")


def _safe_text(value: object, limit: int) -> str:
    return redact_text(value, paths=True)[: max(0, int(limit))]


def _safe_id(value: object, *, field: str) -> str:
    identity = str(value).strip()
    if len(identity) > MAX_ID_CHARS:
        raise ReviewError(f"{field} is too long")
    if identity and _safe_text(identity, MAX_ID_CHARS) != identity:
        raise ReviewError(f"{field} must not contain local paths or credentials")
    return identity


def _finite_number(value: object, *, default: float = 0.0) -> float:
    try:
        number = float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _safe_segment(item: Mapping[str, Any], index: int) -> dict[str, Any]:
    return {
        "id": _safe_id(item.get("id", ""), field=f"subtitle[{index}].id"),
        "start": max(0.0, _finite_number(item.get("start", 0.0))),
        "end": max(0.0, _finite_number(item.get("end", 0.0))),
        "text": _safe_text(item.get("text", ""), MAX_SEGMENT_TEXT_CHARS),
        "speaker": _safe_text(item.get("speaker", ""), 160),
    }


def _chunks(values: Sequence[Mapping[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_bytes = 2
    for index, item in enumerate(values):
        safe = _safe_segment(item, index)
        item_bytes = len(json.dumps(safe, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if current and (len(current) >= size or current_bytes + item_bytes > MAX_SUBTITLE_CHUNK_BYTES):
            chunks.append(current)
            current = []
            current_bytes = 2
        current.append(safe)
        current_bytes += item_bytes + 1
    if current:
        chunks.append(current)
    return chunks


def _safe_timeline_view(value: object) -> dict[str, Any]:
    timeline = value if isinstance(value, Mapping) else {}
    raw_cuts = timeline.get("cuts", [])
    cuts: list[dict[str, Any]] = []
    for index, raw in enumerate(list(raw_cuts if isinstance(raw_cuts, list) else [])[:MAX_TIMELINE_ITEMS]):
        if not isinstance(raw, Mapping):
            continue
        start = max(0.0, _finite_number(raw.get("source_start", raw.get("start", 0.0))))
        end = max(0.0, _finite_number(raw.get("source_end", raw.get("end", start))))
        cuts.append(
            {
                "id": _safe_id(raw.get("id", f"cut-{index}"), field=f"cut[{index}].id"),
                "source_start": start,
                "source_end": end,
                "duration": max(0.0, _finite_number(raw.get("duration", end - start))),
            }
        )
    keep_ranges: list[dict[str, float]] = []
    raw_keep_ranges = timeline.get("keepRanges", timeline.get("keep_ranges", []))
    for raw in list(raw_keep_ranges if isinstance(raw_keep_ranges, list) else [])[:MAX_TIMELINE_ITEMS]:
        if not isinstance(raw, Mapping):
            continue
        keep_ranges.append(
            {
                "source_start": max(0.0, _finite_number(raw.get("source_start", 0.0))),
                "source_end": max(0.0, _finite_number(raw.get("source_end", 0.0))),
                "output_start": max(0.0, _finite_number(raw.get("output_start", 0.0))),
                "output_end": max(0.0, _finite_number(raw.get("output_end", 0.0))),
            }
        )
    source_duration = max(
        0.0,
        _finite_number(timeline.get("sourceDuration", timeline.get("source_duration", 0.0))),
    )
    output_duration = max(
        0.0,
        _finite_number(timeline.get("outputDuration", timeline.get("output_duration", source_duration))),
    )
    removed_duration = max(
        0.0,
        _finite_number(timeline.get("removedDuration", timeline.get("removed_duration", 0.0))),
    )
    raw_cut_count = len(raw_cuts) if isinstance(raw_cuts, list) else 0
    return {
        "schema_version": int(_finite_number(timeline.get("schemaVersion", timeline.get("schema_version", 1)), default=1)),
        "source_duration": source_duration,
        "output_duration": output_duration,
        "removed_duration": removed_duration,
        "has_cuts": bool(timeline.get("hasCuts", timeline.get("has_cuts", cuts))),
        "cuts": cuts,
        "keep_ranges": keep_ranges,
        "cuts_truncated": raw_cut_count > len(cuts),
    }


def path_free_timeline_view(value: object) -> dict[str, Any]:
    """Return the common allowlisted timeline view used by Codex actions."""

    return _safe_timeline_view(value)


def _safe_short_settings(value: object) -> dict[str, Any]:
    settings: Mapping[str, Any] = value if isinstance(value, Mapping) else {}
    raw_output = settings.get("output")
    output: Mapping[str, Any] = raw_output if isinstance(raw_output, Mapping) else {}
    raw_transition = settings.get("transition")
    transition: Mapping[str, Any] = raw_transition if isinstance(raw_transition, Mapping) else {}
    raw_bgm = settings.get("bgm")
    bgm: Mapping[str, Any] = raw_bgm if isinstance(raw_bgm, Mapping) else {}
    return {
        "enabled": bool(settings.get("enabled", False)),
        "output": {
            "width": max(0, int(_finite_number(output.get("width", 0)))),
            "height": max(0, int(_finite_number(output.get("height", 0)))),
            "fps": max(0, int(_finite_number(output.get("fps", 0)))),
        },
        "global_fit": _safe_text(settings.get("global_fit", ""), 40),
        "subtitle_scale_percent": max(0.0, _finite_number(settings.get("subtitle_scale_percent", 0.0))),
        "transition": {
            "type": _safe_text(transition.get("type", ""), 40),
            "duration": max(0.0, _finite_number(transition.get("duration", 0.0))),
        },
        "bgm": {
            "configured": bool(str(bgm.get("path", "")).strip()),
            "volume": max(0.0, _finite_number(bgm.get("volume", 0.0))),
            "in": max(0.0, _finite_number(bgm.get("in", 0.0))),
            "out": max(0.0, _finite_number(bgm.get("out", 0.0))),
            "start": max(0.0, _finite_number(bgm.get("start", 0.0))),
        },
        "target_duration_seconds": max(0.0, _finite_number(settings.get("target_duration_seconds", 0.0))),
    }


def _safe_short_clips(values: object) -> tuple[list[dict[str, Any]], bool]:
    raw_values = values if isinstance(values, list) else []
    clips: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_values[:MAX_SHORT_CLIPS]):
        if not isinstance(raw, Mapping):
            continue
        clips.append(
            {
                "id": _safe_id(raw.get("id", f"clip-{index}"), field=f"clip[{index}].id"),
                "segment_id": _safe_id(raw.get("segment_id", ""), field=f"clip[{index}].segment_id"),
                "start": max(0.0, _finite_number(raw.get("start", 0.0))),
                "end": max(0.0, _finite_number(raw.get("end", 0.0))),
                "fit": _safe_text(raw.get("fit", ""), 40),
                "highlight_candidate_id": _safe_id(
                    raw.get("highlight_candidate_id", ""),
                    field=f"clip[{index}].highlight_candidate_id",
                ),
            }
        )
    return clips, len(raw_values) > len(clips)


def _short_output_duration(
    clips: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any],
) -> float:
    transition = settings.get("transition")
    transition = transition if isinstance(transition, Mapping) else {}
    transition_type = str(transition.get("type", "cut"))
    transition_duration = max(0.0, _finite_number(transition.get("duration", 0.0)))
    total = 0.0
    for index, clip in enumerate(clips):
        duration = max(
            0.0,
            _finite_number(clip.get("end", 0.0)) - _finite_number(clip.get("start", 0.0)),
        )
        overlap = 0.0
        if index > 0 and transition_type != "cut" and transition_duration > 0.0:
            overlap = min(transition_duration, total, duration)
        total = max(0.0, total - overlap) + duration
    return round(total, 3)


def _safe_highlights(values: object, *, field: str) -> tuple[list[dict[str, Any]], bool]:
    raw_values = values if isinstance(values, list) else []
    candidates: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_values[:MAX_HIGHLIGHT_ITEMS]):
        if not isinstance(raw, Mapping):
            continue
        source_ids = raw.get("source_segment_ids", [])
        candidates.append(
            {
                "id": _safe_id(raw.get("id", ""), field=f"{field}[{index}].id"),
                "start": max(0.0, _finite_number(raw.get("start", 0.0))),
                "end": max(0.0, _finite_number(raw.get("end", 0.0))),
                "score": max(0.0, min(1.0, _finite_number(raw.get("score", 0.0)))),
                "category": _safe_text(raw.get("category", ""), 80),
                "reason": _safe_text(raw.get("reason", ""), 300),
                "subtitle_excerpt": _safe_text(raw.get("subtitle_excerpt", ""), 500),
                "source_segment_ids": [
                    _safe_id(item, field=f"{field}[{index}].source_segment_ids")
                    for item in list(source_ids if isinstance(source_ids, (list, tuple)) else [])[:20]
                ],
            }
        )
    return candidates, len(raw_values) > len(candidates)


def _safe_render_view(value: object) -> dict[str, Any]:
    capabilities = value if isinstance(value, Mapping) else {}
    return {
        "can_transcribe": bool(capabilities.get("canTranscribe", False)),
        "transcription_reason": _safe_text(capabilities.get("transcriptionReason", ""), 300),
        "can_render_normal": bool(capabilities.get("canRenderNormal", False)),
        "normal_render_reason": _safe_text(capabilities.get("normalRenderReason", ""), 300),
        "can_render_short": bool(capabilities.get("canRenderShort", False)),
        "short_render_reason": _safe_text(capabilities.get("shortRenderReason", ""), 300),
        "normal_render_needs_output": bool(capabilities.get("normalRenderNeedsOutput", False)),
        "short_render_needs_output": bool(capabilities.get("shortRenderNeedsOutput", False)),
        "can_use_nvenc": bool(capabilities.get("canUseNvenc", False)),
    }


def path_free_render_view(value: object) -> dict[str, Any]:
    """Return the common allowlisted render capability view used by Codex actions."""

    return _safe_render_view(value)


def _default_route_availability(gui: Any) -> dict[str, bool]:
    capabilities = getattr(gui, "actionCapabilities", {})
    return {
        "subtitle_proposal": callable(getattr(gui, "startCodexEdit", None)),
        "audio_mix_proposal": False,
        "timeline_proposal": False,
        "processing_action": any(
            bool(capabilities.get(key, False))
            for key in ("canTranscribe", "canRenderNormal", "canRenderShort")
        )
        or callable(getattr(gui, "startHighlightAnalysis", None)),
    }


def build_review_context(
    gui: Any,
    *,
    subtitle_chunk_size: int = 200,
    route_availability: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    """Build a bounded, recursively allowlisted, path-free review snapshot."""

    size = max(25, min(500, int(subtitle_chunk_size)))
    revision = int(gui._project_revision)
    project = gui._project
    segments = list(gui.subtitleSegments) if project is not None else []
    try:
        audio_channels = path_free_audio_mix_channels(list(gui.audioMixerChannels))
    except ValueError as error:
        raise ReviewError("audio mix contains an unsafe channel identity") from error
    channel_ids = {str(channel["id"]) for channel in audio_channels}
    preview_levels = {
        str(channel_id): max(0.0, min(1.0, _finite_number(level)))
        for channel_id, level in dict(gui.audioPreviewLevels).items()
        if str(channel_id) in channel_ids
    }
    timeline = _safe_timeline_view(gui.cutTimeline)
    project_short = project.get("short_video", {}) if isinstance(project, Mapping) else {}
    project_short = project_short if isinstance(project_short, Mapping) else {}
    raw_short_settings = dict(gui.shortVideoSettings)
    if "target_duration_seconds" in project_short:
        raw_short_settings["target_duration_seconds"] = project_short["target_duration_seconds"]
    short_settings = _safe_short_settings(raw_short_settings)
    raw_short_clips = list(gui.shortVideoClips)
    persisted_clips = project_short.get("clips", [])
    persisted_clips = persisted_clips if isinstance(persisted_clips, list) else []
    enriched_short_clips: list[dict[str, Any]] = []
    for index, raw_clip in enumerate(raw_short_clips):
        if not isinstance(raw_clip, Mapping):
            continue
        enriched = dict(raw_clip)
        persisted = persisted_clips[index] if index < len(persisted_clips) else {}
        if isinstance(persisted, Mapping):
            for field in ("id", "highlight_candidate_id"):
                if field in persisted:
                    enriched[field] = persisted[field]
        enriched_short_clips.append(enriched)
    short_clips, short_clips_truncated = _safe_short_clips(enriched_short_clips)
    raw_candidates = list(gui.highlightCandidates)
    highlight_candidates, candidates_truncated = _safe_highlights(
        raw_candidates,
        field="highlight.candidates",
    )
    rejected_source = getattr(gui, "_highlight_rejected", [])
    raw_rejected = list(rejected_source) if isinstance(rejected_source, list) else []
    rejected_candidates, rejected_truncated = _safe_highlights(
        raw_rejected,
        field="highlight.rejected_candidates",
    )
    capabilities = _safe_render_view(gui.actionCapabilities)
    dependencies = gui._dependencies
    render_settings = project.get("render_settings", {}) if isinstance(project, Mapping) else {}
    target_duration = max(
        0.0,
        _finite_number(
            render_settings.get(
                "target_duration_seconds",
                render_settings.get("output_duration_seconds", 0.0),
            )
            if isinstance(render_settings, Mapping)
            else 0.0
        ),
    )
    routes = dict(_default_route_availability(gui))
    if route_availability is not None:
        routes.update(
            {
                route: bool(route_availability.get(route, False))
                for route in set(ROUTES.values())
            }
        )
    context = {
        "project_revision": revision,
        "project": {
            "loaded": project is not None,
            "dirty": bool(gui._project_dirty),
            "duration_seconds": max(0.0, _finite_number(gui.projectDuration)),
            "target_duration_seconds": target_duration,
            "segment_count": len(segments),
        },
        "subtitle_chunks": _chunks(segments, size),
        "audio": {
            "channels": audio_channels,
            "preview_levels": preview_levels,
            "master_level": max(0.0, min(1.0, _finite_number(gui.audioMasterLevel))),
            "limiter_reduction_db": max(0.0, _finite_number(gui.audioLimiterReductionDb)),
        },
        "timeline": timeline,
        "timeline_available": bool(gui._cut_editor_available),
        "short": {
            "settings": short_settings,
            "clips": short_clips,
            "clip_count": len(raw_short_clips),
            "clips_truncated": short_clips_truncated,
            "output_duration_seconds": _short_output_duration(short_clips, short_settings),
        },
        "highlight": {
            "status": _safe_text(gui.highlightAnalysisState, 40),
            "candidates": highlight_candidates,
            "candidate_count": len(raw_candidates),
            "candidates_truncated": candidates_truncated,
            "rejected_candidates": rejected_candidates,
            "rejected_candidate_count": len(raw_rejected),
            "rejected_candidates_truncated": rejected_truncated,
        },
        "processing": {
            "active_job": _safe_text(gui._active_job, 80),
            "running": bool(gui._running),
            "status": _safe_text(gui._processing_progress.status, 80),
        },
        "render": capabilities,
        "dependencies": {
            "ffmpeg": bool(dependencies.ffmpeg),
            "ffprobe": bool(dependencies.ffprobe),
            "whisperx": bool(dependencies.whisperx),
            "cuda": bool(dependencies.cuda),
            "nvenc": bool(dependencies.nvenc),
        },
        "route_availability": {
            route: bool(routes.get(route, False)) for route in sorted(set(ROUTES.values()))
        },
    }
    if int(gui._project_revision) != revision:
        raise StaleReviewError("project changed while review context was being built")
    return context


def _route_available(context: Mapping[str, Any], category: str) -> bool:
    route = ROUTES.get(category, "")
    availability = context.get("route_availability")
    if not route:
        return False
    if not isinstance(availability, Mapping):
        return True
    return bool(availability.get(route, False))


def _finding(
    category: str,
    severity: str,
    target: Mapping[str, Any],
    reason: str,
    *,
    route_available: bool = True,
) -> ReviewFinding:
    identity = json.dumps([category, target, reason], ensure_ascii=False, sort_keys=True)
    finding_id = f"review-{sha256(identity.encode('utf-8')).hexdigest()[:12]}"
    return ReviewFinding(
        id=finding_id,
        category=category,
        severity=severity,
        target=dict(target),
        reason=reason,
        route=ROUTES.get(category, "") if route_available else "",
    )


def review_context(context: Mapping[str, Any]) -> ReviewResult:
    """Run deterministic preflight checks; semantic review is performed separately."""

    revision = int(context["project_revision"])
    findings: list[ReviewFinding] = []
    project = context["project"]
    if not project["loaded"]:
        return ReviewResult(
            revision,
            (_finding("project", "blocking", {}, "編集プロジェクトが読み込まれていません", route_available=False),),
        )
    if project["dirty"]:
        findings.append(
            _finding(
                "render",
                "blocking",
                {},
                "未保存の変更があります",
                route_available=_route_available(context, "render"),
            )
        )

    previous_end = 0.0
    normalized_text: dict[str, list[str]] = {}
    for segment in _flatten(context["subtitle_chunks"]):
        segment_id = str(segment.get("id", ""))
        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", 0.0))
        text = str(segment.get("text", "")).strip()
        target = {"segment_ids": [segment_id]} if segment_id else {}
        if not text:
            findings.append(
                _finding(
                    "subtitle",
                    "blocking",
                    target,
                    "空の字幕があります",
                    route_available=_route_available(context, "subtitle"),
                )
            )
        elif end <= start:
            findings.append(
                _finding(
                    "subtitle",
                    "blocking",
                    target,
                    "字幕の表示時間が不正です",
                    route_available=_route_available(context, "subtitle"),
                )
            )
        elif len(text) / max(0.1, end - start) > 14:
            findings.append(
                _finding(
                    "subtitle",
                    "warning",
                    target,
                    "表示時間に対して字幕が長すぎます",
                    route_available=_route_available(context, "subtitle"),
                )
            )
        if start < previous_end - 0.02:
            findings.append(
                _finding(
                    "subtitle",
                    "warning",
                    target,
                    "前の字幕と表示時間が重なっています",
                    route_available=_route_available(context, "subtitle"),
                )
            )
        elif previous_end > 0.0 and start - previous_end >= 8.0:
            findings.append(
                _finding(
                    "timeline",
                    "suggestion",
                    {"start_seconds": previous_end, "end_seconds": start},
                    "字幕のない長い区間があります",
                    route_available=_route_available(context, "timeline"),
                )
            )
        previous_end = max(previous_end, end)
        if text:
            normalized_text.setdefault("".join(text.split()), []).append(segment_id)
    for segment_ids in normalized_text.values():
        unique_ids = list(dict.fromkeys(item for item in segment_ids if item))
        if len(unique_ids) > 1:
            findings.append(
                _finding(
                    "subtitle",
                    "warning",
                    {"segment_ids": unique_ids[:20]},
                    "同一内容の字幕が重複しています",
                    route_available=_route_available(context, "subtitle"),
                )
            )

    audio = context["audio"]
    active = active_audio_mix_channels({"channels": list(audio["channels"])})
    if not active:
        findings.append(
            _finding(
                "audio",
                "blocking",
                {},
                "再生・書き出しで有効な音声トラックがなく無音になります",
                route_available=_route_available(context, "audio"),
            )
        )
    for channel in active:
        volume = float(channel.get("volume_percent", 100.0))
        if volume < 25.0:
            findings.append(
                _finding(
                    "audio",
                    "warning",
                    {"channel_ids": [str(channel.get("id", ""))]},
                    "音量が極端に小さいトラックがあります",
                    route_available=_route_available(context, "audio"),
                )
            )
    if float(audio["limiter_reduction_db"]) >= 6.0:
        findings.append(
            _finding(
                "audio",
                "warning",
                {},
                "リミッターの減衰が大きすぎます",
                route_available=_route_available(context, "audio"),
            )
        )

    if not context["timeline_available"]:
        findings.append(
            _finding("timeline", "suggestion", {}, "通常動画カット機能を利用できません", route_available=False)
        )
    short = context["short"]
    if short["settings"].get("enabled") and not short["clips"]:
        findings.append(
            _finding(
                "short",
                "blocking",
                {},
                "ショート動画にクリップがありません",
                route_available=_route_available(context, "short"),
            )
        )
    previous_clip_end = -1.0
    for clip in short["clips"]:
        start = float(clip.get("start", 0.0))
        end = float(clip.get("end", 0.0))
        target = {"clip_ids": [str(clip.get("id", ""))]}
        if end <= start:
            findings.append(
                _finding(
                    "short",
                    "blocking",
                    target,
                    "ショートクリップの範囲が不正です",
                    route_available=_route_available(context, "short"),
                )
            )
        if previous_clip_end >= 0.0 and start < previous_clip_end:
            findings.append(
                _finding(
                    "short",
                    "warning",
                    target,
                    "ショートクリップの順序または範囲が重なっています",
                    route_available=_route_available(context, "short"),
                )
            )
        previous_clip_end = max(previous_clip_end, end)
    if context["processing"]["status"] == "error":
        findings.append(
            _finding(
                "processing",
                "blocking",
                {},
                "直前の処理が失敗しています",
                route_available=_route_available(context, "processing"),
            )
        )

    dependencies = context["dependencies"]
    if not dependencies["ffmpeg"] or not dependencies["ffprobe"]:
        findings.append(_finding("render", "blocking", {}, "動画処理の必須依存が不足しています", route_available=False))
    return ReviewResult(revision, _dedupe(findings))


def _known_target_ids(context: Mapping[str, Any]) -> dict[str, set[str]]:
    segment_ids = {
        str(item.get("id", ""))
        for item in _flatten(context.get("subtitle_chunks", []))
        if str(item.get("id", ""))
    }
    channel_ids = {
        str(item.get("id", ""))
        for item in context.get("audio", {}).get("channels", [])
        if str(item.get("id", ""))
    }
    clip_ids = {
        str(item.get("id", ""))
        for item in context.get("short", {}).get("clips", [])
        if str(item.get("id", ""))
    }
    cut_ids = {
        str(item.get("id", ""))
        for item in context.get("timeline", {}).get("cuts", [])
        if str(item.get("id", ""))
    }
    highlights = context.get("highlight", {})
    candidate_ids = {
        str(item.get("id", ""))
        for collection in (
            highlights.get("candidates", []),
            highlights.get("rejected_candidates", []),
        )
        for item in collection
        if str(item.get("id", ""))
    }
    return {
        "segment_ids": segment_ids,
        "channel_ids": channel_ids,
        "clip_ids": clip_ids,
        "cut_ids": cut_ids,
        "candidate_ids": candidate_ids,
    }


def _strict_keys(value: object, expected: set[str], *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReviewContractError(f"{field} must be an object")
    unknown = set(value) - expected
    missing = expected - set(value)
    if unknown or missing:
        raise ReviewContractError(f"{field} fields do not match the review contract")
    return value


def _validate_model_target(
    raw: object,
    *,
    known_ids: Mapping[str, set[str]],
    duration: float,
) -> dict[str, Any]:
    expected = {*TARGET_ID_FIELDS, "start_seconds", "end_seconds"}
    target = _strict_keys(raw, expected, field="issue.target")
    compact: dict[str, Any] = {}
    for field in TARGET_ID_FIELDS:
        values = target[field]
        if not isinstance(values, list) or len(values) > 20:
            raise ReviewContractError(f"issue.target.{field} must be a bounded array")
        if any(not isinstance(item, str) or not item or len(item) > MAX_ID_CHARS for item in values):
            raise ReviewContractError(f"issue.target.{field} contains an invalid id")
        if len(set(values)) != len(values) or not set(values).issubset(known_ids[field]):
            raise ReviewContractError(f"issue.target.{field} contains an unknown or duplicate id")
        if values:
            compact[field] = list(values)
    times: dict[str, float | None] = {}
    for field in ("start_seconds", "end_seconds"):
        value = target[field]
        if value is None:
            times[field] = None
            continue
        if type(value) not in (int, float) or not math.isfinite(float(value)) or float(value) < 0.0:
            raise ReviewContractError(f"issue.target.{field} must be a non-negative finite number or null")
        number = float(value)
        if duration > 0.0 and number > duration + 0.001:
            raise ReviewContractError(f"issue.target.{field} exceeds project duration")
        times[field] = number
        compact[field] = number
    if times["start_seconds"] is not None and times["end_seconds"] is not None:
        if times["end_seconds"] <= times["start_seconds"]:
            raise ReviewContractError("issue target time range must increase")
    return compact


def parse_review_result(raw: object, context: Mapping[str, Any]) -> ReviewResult:
    """Validate untrusted structured output and normalize stable finding IDs."""

    payload = _strict_keys(
        raw,
        {"project_revision", "issues", "recommended_order"},
        field="review result",
    )
    revision = payload["project_revision"]
    expected_revision = int(context["project_revision"])
    if type(revision) is not int or revision != expected_revision:
        raise ReviewContractError("review result project revision does not match")
    raw_issues = payload["issues"]
    raw_order = payload["recommended_order"]
    if not isinstance(raw_issues, list) or len(raw_issues) > MAX_FINDINGS_PER_TURN:
        raise ReviewContractError("review result issues must be a bounded array")
    if not isinstance(raw_order, list) or any(not isinstance(item, str) for item in raw_order):
        raise ReviewContractError("review result recommended_order must be an id array")
    known_ids = _known_target_ids(context)
    duration = float(context.get("project", {}).get("duration_seconds", 0.0))
    findings_by_model_id: dict[str, ReviewFinding] = {}
    canonical_by_model_id: dict[str, str] = {}
    for raw_issue in raw_issues:
        issue = _strict_keys(
            raw_issue,
            {"id", "category", "severity", "target", "reason", "recommendation"},
            field="issue",
        )
        model_id = issue["id"]
        if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > MAX_ID_CHARS:
            raise ReviewContractError("issue.id must be a bounded non-empty string")
        if model_id in findings_by_model_id:
            raise ReviewContractError("issue.id must be unique")
        category = issue["category"]
        severity = issue["severity"]
        reason = issue["reason"]
        if category not in REVIEW_CATEGORIES:
            raise ReviewContractError("issue.category is invalid")
        if severity not in SEVERITY_ORDER:
            raise ReviewContractError("issue.severity is invalid")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > MAX_REASON_CHARS:
            raise ReviewContractError("issue.reason must be a bounded non-empty string")
        target = _validate_model_target(issue["target"], known_ids=known_ids, duration=duration)
        recommendation = _strict_keys(
            issue["recommendation"],
            {"available", "route"},
            field="issue.recommendation",
        )
        available = recommendation["available"]
        route = recommendation["route"]
        if type(available) is not bool or not isinstance(route, str):
            raise ReviewContractError("issue.recommendation has invalid values")
        expected_route = ROUTES.get(str(category), "")
        route_is_available = bool(context.get("route_availability", {}).get(expected_route, False))
        if available:
            if not expected_route or route != expected_route or not route_is_available:
                raise ReviewContractError("issue.recommendation route is unavailable or belongs to another domain")
        elif route:
            raise ReviewContractError("unavailable issue.recommendation must have an empty route")
        safe_reason = _safe_text(reason.strip(), MAX_REASON_CHARS)
        finding = _finding(
            str(category),
            str(severity),
            target,
            safe_reason,
            route_available=bool(available),
        )
        if finding.id in canonical_by_model_id.values():
            raise ReviewContractError("semantically duplicate issues are not allowed")
        findings_by_model_id[model_id] = finding
        canonical_by_model_id[model_id] = finding.id
    if len(raw_order) != len(set(raw_order)) or set(raw_order) != set(findings_by_model_id):
        raise ReviewContractError("recommended_order must contain every issue id exactly once")
    findings = tuple(findings_by_model_id.values())
    canonical_order = tuple(canonical_by_model_id[model_id] for model_id in raw_order)
    return ReviewResult(expected_revision, findings, canonical_order)


def _review_turn_context(
    context: Mapping[str, Any],
    *,
    chunk_index: int,
    preflight: ReviewResult,
) -> dict[str, Any]:
    chunks = list(context.get("subtitle_chunks", [])) or [[]]
    chunk = list(chunks[chunk_index])
    previous_segment = list(chunks[chunk_index - 1])[-1] if chunk_index > 0 and chunks[chunk_index - 1] else None
    next_segment = list(chunks[chunk_index + 1])[0] if chunk_index + 1 < len(chunks) and chunks[chunk_index + 1] else None
    global_context = {key: value for key, value in context.items() if key != "subtitle_chunks"}
    global_context["subtitle_chunk"] = {
        "index": chunk_index,
        "count": len(chunks),
        "segments": chunk,
        "previous_segment": previous_segment,
        "next_segment": next_segment,
    }
    global_context["preflight"] = preflight.to_json(max_issues=MAX_FINDINGS_PER_TURN)
    return global_context


def _isolated_review_cwd(value: str | Path | None) -> str:
    try:
        return validate_isolated_cwd(value)
    except CodexIsolationError as error:
        raise ReviewContractError(str(error).replace("Codex turn", "Codex review")) from error


def _mcp_server_names(
    client: ReviewClient,
    *,
    thread_id: str = "",
    config_only: bool = False,
) -> tuple[str, ...]:
    try:
        return collect_mcp_server_names(
            client,
            thread_id=thread_id,
            config_only=config_only,
        )
    except CodexIsolationError as error:
        raise ReviewContractError(str(error)) from error


def _review_thread_config(mcp_server_names: Sequence[str]) -> dict[str, Any]:
    return build_isolated_thread_config(mcp_server_names)


def _require_revision(context: Mapping[str, Any], current_revision: Callable[[], int] | None) -> None:
    if current_revision is not None and int(current_revision()) != int(context["project_revision"]):
        raise StaleReviewError("project changed while review was running")


def review_context_with_codex(
    context: Mapping[str, Any],
    *,
    client: ReviewClient,
    isolated_cwd: str | Path | None = None,
    model: str = "",
    current_revision: Callable[[], int] | None = None,
    turn_timeout: float = 120.0,
) -> ReviewResult:
    """Run bounded semantic review turns and merge them with deterministic preflight."""

    preflight = review_context(context)
    if not bool(context.get("project", {}).get("loaded", False)):
        return preflight
    _require_revision(context, current_revision)
    review_cwd = _isolated_review_cwd(isolated_cwd)
    configured_mcp_names = _mcp_server_names(client, config_only=True)
    chunks = list(context.get("subtitle_chunks", [])) or [[]]
    findings: list[ReviewFinding] = list(preflight.issues)
    recommended: list[str] = [item.id for item in preflight.ordered_issues()]
    for chunk_index in range(len(chunks)):
        _require_revision(context, current_revision)
        thread = client.thread_start(
            build_isolated_thread_params(
                review_cwd,
                mcp_server_names=configured_mcp_names,
            )
        )
        thread_payload = thread.get("thread", thread)
        thread_id = (
            str(thread_payload.get("id", ""))
            if isinstance(thread_payload, Mapping)
            else ""
        ) or str(thread.get("threadId", ""))
        if not thread_id:
            raise ReviewContractError("Codex review thread id is missing")
        if _mcp_server_names(client, thread_id=thread_id):
            raise ReviewContractError("Codex review thread still exposes an MCP server")
        raw = client.run_structured_turn(
            thread_id=thread_id,
            prompt=REVIEW_PROMPT,
            output_schema=REVIEW_OUTPUT_SCHEMA,
            context=_review_turn_context(context, chunk_index=chunk_index, preflight=preflight),
            model=model or None,
            **build_isolated_turn_kwargs(review_cwd),
            timeout=max(1.0, float(turn_timeout)),
        )
        _require_revision(context, current_revision)
        parsed = parse_review_result(raw, context)
        findings.extend(parsed.issues)
        recommended.extend(parsed.recommended_order)
    _require_revision(context, current_revision)
    unique = _dedupe(findings)
    unique_ids = {finding.id for finding in unique}
    order = tuple(dict.fromkeys(item for item in recommended if item in unique_ids))
    return ReviewResult(int(context["project_revision"]), unique, order)


def _flatten(chunks: Iterable[Iterable[Mapping[str, Any]]]) -> Iterable[Mapping[str, Any]]:
    for chunk in chunks:
        yield from chunk


def _dedupe(findings: Iterable[ReviewFinding]) -> tuple[ReviewFinding, ...]:
    unique: dict[str, ReviewFinding] = {}
    for finding in findings:
        unique.setdefault(finding.id, finding)
    return tuple(unique.values())
