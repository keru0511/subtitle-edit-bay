from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


RULE_VERSION = "subtitle-review-v1"


@dataclass(frozen=True)
class ReviewFinding:
    rule_id: str
    severity: str
    reason: str
    evidence: Mapping[str, Any]


def review_segment_rules(segment: Mapping[str, Any], previous: Mapping[str, Any] | None = None) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    start = float(segment.get("start", 0.0))
    end = float(segment.get("end", start))
    duration = max(0.0, end - start)
    text = str(segment.get("text", "")).replace("\n", "")
    confidence = segment.get("confidence", segment.get("avg_confidence"))
    if confidence is not None and float(confidence) < 0.55:
        findings.append(ReviewFinding("low_confidence", "high", "認識信頼度が低い字幕です", {"confidence": float(confidence)}))
    if duration > 0 and len(text) / duration > 15:
        findings.append(ReviewFinding("reading_speed", "medium", "表示時間に対して字幕の読速が速すぎます", {"chars_per_second": round(len(text) / duration, 2)}))
    if text and duration < 0.28:
        findings.append(ReviewFinding("short_display", "medium", "字幕の表示時間が短すぎます", {"duration": round(duration, 3)}))
    max_width = int(segment.get("max_width", 24))
    if max((len(line) for line in str(segment.get("text", "")).splitlines()), default=0) > max_width * 1.5:
        findings.append(ReviewFinding("line_width", "medium", "字幕の1行が長すぎます", {"max_width": max_width}))
    if previous is not None:
        previous_end = float(previous.get("end", 0.0))
        gap = start - previous_end
        if gap < -0.05:
            findings.append(ReviewFinding("overlap", "high", "字幕の表示区間が重なっています", {"overlap": round(-gap, 3)}))
        elif gap > 2.5:
            findings.append(ReviewFinding("long_gap", "low", "字幕間の無音・空白が長くなっています", {"gap": round(gap, 3)}))
        if str(previous.get("speaker", "")) != str(segment.get("speaker", "")) and gap < 0.05:
            findings.append(ReviewFinding("speaker_switch", "low", "話者切替が近接しています", {"gap": round(max(0.0, gap), 3)}))
    return findings

