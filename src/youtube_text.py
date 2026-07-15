from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

STOP_PHRASES = {
    "ああああああ",
    "うおおおおおお",
    "おー!",
    "またね。",
    "ありがとうございます",
}
STOP_KEYWORDS = {
    "これ",
    "それ",
    "あれ",
    "ここ",
    "そこ",
    "やばい",
    "すごい",
    "まじ",
    "www",
}


def load_merged_transcript(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def clean_text(text: str) -> str:
    return " ".join(str(text).replace("\n", " ").split()).strip()


def interesting_segments(segments: list[dict], limit: int = 8) -> list[dict]:
    scored: list[tuple[tuple[int, float, float], dict]] = []
    for segment in segments:
        text = clean_text(segment.get("text", ""))
        if not text or text in STOP_PHRASES:
            continue
        duration = max(0.0, float(segment.get("end", 0.0)) - float(segment.get("start", 0.0)))
        score = (
            1 if any(mark in text for mark in "!?！？") else 0,
            min(len(text), 40),
            duration,
        )
        scored.append((score, {**segment, "text": text}))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [segment for _, segment in scored[:limit]]


def extract_keywords(segments: list[dict], limit: int = 4) -> list[str]:
    counter: Counter[str] = Counter()
    for segment in segments:
        text = clean_text(segment.get("text", ""))
        for raw in text.replace("!", " ").replace("?", " ").replace("！", " ").replace("？", " ").split():
            token = raw.strip("。、,.「」『』()[]")
            if len(token) < 2 or token in STOP_KEYWORDS:
                continue
            counter[token] += 1
    keywords = [word for word, _ in counter.most_common(limit)]
    if keywords:
        return keywords

    fallback: list[str] = []
    for segment in segments:
        text = clean_text(segment.get("text", ""))
        if len(text) >= 6:
            fallback.append(text[:12])
        if len(fallback) >= limit:
            break
    return fallback


def build_title_candidates(segments: list[dict], video_stem: str, limit: int = 3) -> list[str]:
    picks = interesting_segments(segments, limit=limit * 2)
    keywords = extract_keywords(picks or segments)
    base_keyword = " / ".join(keywords[:2]) if keywords else video_stem

    candidates: list[str] = []
    for segment in picks:
        text = clean_text(segment["text"])
        if len(text) > 28:
            text = f"{text[:28]}…"
        candidate = f"【実況】{text}"
        if candidate not in candidates:
            candidates.append(candidate)
        if len(candidates) >= limit:
            return candidates

    templates = [
        f"【実況】{base_keyword}で大騒ぎした回",
        f"【ゲーム実況】{base_keyword}が強すぎた",
        f"【実況切り抜き】{video_stem}の見どころまとめ",
    ]
    for candidate in templates:
        if candidate not in candidates:
            candidates.append(candidate)
        if len(candidates) >= limit:
            break
    return candidates


def build_description_text(
    segments: list[dict],
    title_candidates: list[str],
    video_stem: str,
    timestamp_offset_seconds: float = 0.0,
) -> str:
    picks = interesting_segments(segments, limit=5)
    speakers = sorted({segment.get("speaker", "UNKNOWN") for segment in segments})
    keywords = extract_keywords(picks or segments)

    lines = [
        f"おすすめタイトル案1: {title_candidates[0] if title_candidates else f'【実況】{video_stem}'}",
        "おすすめタイトル案:",
    ]
    for index, title in enumerate(title_candidates, start=1):
        lines.append(f"{index}. {title}")

    lines.extend(
        [
            "",
            "概要欄たたき台:",
            f"{video_stem} の実況字幕版です。",
            f"登場話者: {', '.join(speakers)}",
        ]
    )

    if keywords:
        lines.append(f"見どころキーワード: {', '.join(keywords)}")

    if picks:
        lines.append("")
        lines.append("見どころメモ:")
        for segment in picks[:3]:
            start = max(0.0, float(segment.get("start", 0.0)) + timestamp_offset_seconds)
            mm = int(start // 60)
            ss = int(start % 60)
            lines.append(f"- {mm:02d}:{ss:02d} {segment.get('speaker', 'UNKNOWN')}: {clean_text(segment['text'])}")

    lines.extend(
        [
            "",
            "字幕は自動生成をベースに調整しています。",
            "感想や見どころがあればコメントでぜひ教えてください。",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def derive_youtube_text_paths(merged_json_path: str) -> tuple[Path, Path]:
    merged_path = Path(merged_json_path)
    stem = merged_path.stem.removesuffix(".merged")
    base_dir = merged_path.parent
    return base_dir / f"{stem}.youtube_title.txt", base_dir / f"{stem}.youtube_description.txt"


def write_youtube_texts(merged_json_path: str, timestamp_offset_seconds: float = 0.0) -> tuple[Path, Path]:
    data = load_merged_transcript(merged_json_path)
    segments = data.get("segments", [])
    merged_path = Path(merged_json_path)
    video_stem = merged_path.stem.removesuffix(".merged")
    title_path, description_path = derive_youtube_text_paths(merged_json_path)

    titles = build_title_candidates(segments, video_stem)
    description = build_description_text(
        segments,
        titles,
        video_stem,
        timestamp_offset_seconds=timestamp_offset_seconds,
    )

    title_path.write_text("\n".join(titles).strip() + "\n", encoding="utf-8")
    description_path.write_text(description, encoding="utf-8")
    return title_path, description_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate YouTube title and description drafts from merged transcript JSON.")
    parser.add_argument("--input", required=True, help="Path to *.merged.json")
    args = parser.parse_args()

    title_path, description_path = write_youtube_texts(args.input)
    print(title_path)
    print(description_path)


if __name__ == "__main__":
    main()
