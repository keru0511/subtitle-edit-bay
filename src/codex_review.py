from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping, Sequence


ROUTES = {
    "subtitle": "subtitle_proposal",
    "audio": "audio_mix_proposal",
    "timeline": "timeline_proposal",
    "short": "timeline_proposal",
    "processing": "processing_action",
    "render": "processing_action",
}
SEVERITY_ORDER = {"blocking": 0, "warning": 1, "suggestion": 2}
SAFE_SEGMENT_FIELDS = ("id", "start", "end", "text", "speaker")


@dataclass(frozen=True)
class ReviewFinding:
    id: str
    category: str
    severity: str
    target: Mapping[str, Any]
    reason: str
    route: str = ""

    def to_json(self) -> dict[str, Any]:
        recommendation = {"route": self.route} if self.route else {"available": False}
        return {
            "id": self.id,
            "category": self.category,
            "severity": self.severity,
            "target": dict(self.target),
            "reason": self.reason,
            "recommendation": recommendation,
        }


@dataclass(frozen=True)
class ReviewResult:
    project_revision: int
    issues: tuple[ReviewFinding, ...]

    def to_json(self) -> dict[str, Any]:
        ordered = sorted(
            self.issues,
            key=lambda item: (SEVERITY_ORDER[item.severity], item.category, item.id),
        )
        return {
            "project_revision": self.project_revision,
            "issues": [item.to_json() for item in ordered],
            "recommended_order": [item.id for item in ordered],
        }

    def require_current_revision(self, current_revision: int) -> None:
        if self.project_revision != current_revision:
            raise ValueError("review result is stale")


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


def _chunks(values: Sequence[Mapping[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [
        [{key: item[key] for key in SAFE_SEGMENT_FIELDS if key in item} for item in values[index : index + size]]
        for index in range(0, len(values), size)
    ]


def build_review_context(gui: Any, *, subtitle_chunk_size: int = 200) -> dict[str, Any]:
    """Build a bounded, path-free snapshot for review and later Codex reasoning."""

    size = max(25, min(500, int(subtitle_chunk_size)))
    revision = int(gui._project_revision)
    project = gui._project
    segments = list(gui.subtitleSegments) if project is not None else []
    safe_audio_fields = ("id", "kind", "label", "enabled", "muted", "solo", "volume_percent")
    audio = [
        {key: channel[key] for key in safe_audio_fields if key in channel}
        for channel in gui.audioMixerChannels
    ]
    capabilities = dict(gui.actionCapabilities)
    dependencies = gui._dependencies
    short_settings = dict(gui.shortVideoSettings)
    bgm = dict(short_settings.get("bgm", {}))
    safe_short_settings = {
        key: short_settings[key]
        for key in ("enabled", "output", "global_fit", "subtitle_scale_percent", "transition")
        if key in short_settings
    }
    if bgm:
        safe_short_settings["bgm"] = {
            key: bgm[key]
            for key in ("volume", "in", "out", "start")
            if key in bgm
        }
    context = {
        "project_revision": revision,
        "project": {
            "loaded": project is not None,
            "dirty": bool(gui._project_dirty),
            "duration_seconds": float(gui.projectDuration),
            "segment_count": len(segments),
        },
        "subtitle_chunks": _chunks(segments, size),
        "audio": {
            "channels": audio,
            "preview_levels": dict(gui.audioPreviewLevels),
            "master_level": float(gui.audioMasterLevel),
            "limiter_reduction_db": float(gui.audioLimiterReductionDb),
        },
        "timeline": dict(gui.cutTimeline),
        "timeline_available": bool(gui._cut_editor_available),
        "short": {
            "settings": safe_short_settings,
            "clips": [
                {key: clip[key] for key in ("id", "segment_id", "start", "end", "fit") if key in clip}
                for clip in gui.shortVideoClips
            ],
        },
        "highlight": {
            "status": str(gui.highlightAnalysisState),
            "candidate_count": len(gui.highlightCandidates),
        },
        "processing": {
            "active_job": str(gui._active_job),
            "running": bool(gui._running),
            "status": str(gui._processing_progress.status),
        },
        "render": capabilities,
        "dependencies": {
            "ffmpeg": bool(dependencies.ffmpeg),
            "ffprobe": bool(dependencies.ffprobe),
            "whisperx": bool(dependencies.whisperx),
            "cuda": bool(dependencies.cuda),
            "nvenc": bool(dependencies.nvenc),
        },
    }
    if int(gui._project_revision) != revision:
        raise ValueError("project changed while review context was being built")
    return context


def review_context(context: Mapping[str, Any]) -> ReviewResult:
    revision = int(context["project_revision"])
    findings: list[ReviewFinding] = []
    project = context["project"]
    if not project["loaded"]:
        return ReviewResult(
            revision,
            (_finding("project", "blocking", {}, "編集プロジェクトが読み込まれていません", route_available=False),),
        )
    if project["dirty"]:
        findings.append(_finding("render", "blocking", {}, "未保存の変更があります"))

    previous_end = 0.0
    for segment in _flatten(context["subtitle_chunks"]):
        segment_id = str(segment.get("id", ""))
        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", 0.0))
        text = str(segment.get("text", "")).strip()
        target = {"segment_ids": [segment_id]} if segment_id else {}
        if not text:
            findings.append(_finding("subtitle", "blocking", target, "空の字幕があります"))
        elif end <= start:
            findings.append(_finding("subtitle", "blocking", target, "字幕の表示時間が不正です"))
        elif len(text) / max(0.1, end - start) > 14:
            findings.append(_finding("subtitle", "warning", target, "表示時間に対して字幕が長すぎます"))
        if start < previous_end - 0.02:
            findings.append(_finding("subtitle", "warning", target, "前の字幕と表示時間が重なっています"))
        previous_end = max(previous_end, end)

    audio = context["audio"]
    enabled = [item for item in audio["channels"] if item.get("enabled", True)]
    if audio["channels"] and not enabled:
        findings.append(_finding("audio", "blocking", {}, "有効な音声トラックがありません"))
    for channel in enabled:
        volume = float(channel.get("volume_percent", 100.0))
        if volume < 25.0:
            findings.append(
                _finding("audio", "warning", {"channel_ids": [str(channel.get("id", ""))]}, "音量が極端に小さいトラックがあります")
            )
    if float(audio["limiter_reduction_db"]) >= 6.0:
        findings.append(_finding("audio", "warning", {}, "リミッターの減衰が大きすぎます"))

    if not context["timeline_available"]:
        findings.append(_finding("timeline", "suggestion", {}, "通常動画カット機能を利用できません", route_available=False))
    short = context["short"]
    if short["settings"].get("enabled") and not short["clips"]:
        findings.append(_finding("short", "blocking", {}, "ショート動画にクリップがありません"))
    if context["processing"]["status"] == "error":
        findings.append(_finding("processing", "blocking", {}, "直前の処理が失敗しています"))

    dependencies = context["dependencies"]
    if not dependencies["ffmpeg"] or not dependencies["ffprobe"]:
        findings.append(_finding("render", "blocking", {}, "動画処理の必須依存が不足しています", route_available=False))
    return ReviewResult(revision, _dedupe(findings))


def _flatten(chunks: Iterable[Iterable[Mapping[str, Any]]]) -> Iterable[Mapping[str, Any]]:
    for chunk in chunks:
        yield from chunk


def _dedupe(findings: Iterable[ReviewFinding]) -> tuple[ReviewFinding, ...]:
    unique: dict[str, ReviewFinding] = {}
    for finding in findings:
        unique.setdefault(finding.id, finding)
    return tuple(unique.values())
