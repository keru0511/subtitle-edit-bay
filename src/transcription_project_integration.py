"""文字起こし結果を、保存や画面状態に依存せず編集プロジェクトへ統合する。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import cast
from uuid import uuid4


LayoutRows = Callable[[list[dict[str, object]]], list[dict[str, object]]]


def ensure_transcription_context_base_dir(project: dict[str, object], project_path: str | Path) -> None:
    """文字起こし設定が null の旧プロジェクトも含め、辞書の基準位置を補う。"""

    transcription = project.get("transcription")
    if transcription is None:
        transcription = {}
        project["transcription"] = transcription
    if not isinstance(transcription, dict):
        raise TypeError("transcription must be an object or null")
    transcription.setdefault(
        "context_base_dir",
        str(Path(transcription.get("work_dir") or project.get("output_dir") or Path(project_path).parent).resolve()),
    )


def _copy_segments(project: Mapping[str, object]) -> list[dict[str, object]]:
    segments = project.get("segments", [])
    if not isinstance(segments, list) or any(not isinstance(segment, dict) for segment in segments):
        raise TypeError("segments must be a list of objects")
    return deepcopy(cast(list[dict[str, object]], segments))


def compose_transcription_project(
    preserved: Mapping[str, object],
    generated: Mapping[str, object],
    mode: str,
    *,
    assign_layout_rows: LayoutRows,
) -> dict[str, object]:
    """既存の編集設定を保ち、生成された字幕と文字起こし情報だけを反映する。"""

    if mode not in {"merge", "replace"}:
        raise ValueError(f"未対応の文字起こし統合方法です: {mode}")

    integrated = deepcopy(dict(preserved))
    generated_segments = _copy_segments(generated)
    if mode == "merge":
        segments = _copy_segments(integrated)
        used_ids = {str(segment.get("id", "")) for segment in segments}
        for segment in generated_segments:
            segment_id = str(segment.get("id", ""))
            if not segment_id or segment_id in used_ids:
                segment_id = f"transcribed-{uuid4().hex[:12]}"
                while segment_id in used_ids:
                    segment_id = f"transcribed-{uuid4().hex[:12]}"
                segment["id"] = segment_id
            used_ids.add(segment_id)
            segments.append(segment)
    else:
        segments = generated_segments

    integrated["segments"] = assign_layout_rows(
        sorted(
            segments,
            key=lambda segment: (
                cast(float, segment["start"]),
                cast(float, segment["end"]),
                cast(str, segment["id"]),
            ),
        )
    )
    for key in ("transcription", "transcription_context", "waveforms"):
        if key in generated:
            integrated[key] = deepcopy(generated[key])
    return integrated
