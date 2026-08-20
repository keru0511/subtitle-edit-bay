from __future__ import annotations

import pytest

from src.multi_source_short_video import (
    MultiSourceError,
    add_clip,
    add_source,
    build_concat_filter_script,
    ensure_multi_source_project,
    mark_source_missing,
    merge_source_candidates,
    normalization_plan,
    relink_source,
    speaker_style_key,
)


def _single_project():
    return {"video_path": "C:/素材/一つ目.mkv", "clips": [{"source_start": 0, "source_end": 2}]}


def test_single_source_migrates_without_losing_legacy_fields():
    project = ensure_multi_source_project(_single_project())
    assert project["video_path"] == "C:/素材/一つ目.mkv"
    assert len(project["sources"]) == 1
    assert project["clips"][0]["source_id"] == project["sources"][0]["source_id"]


def test_mixed_sources_have_stable_ids_missing_relink_and_normalization():
    project = ensure_multi_source_project(_single_project())
    first_id = project["sources"][0]["source_id"]
    project = add_source(project, "C:/素材/二つ目.mkv", {"media_fingerprint": "second", "fps": 60, "width": 2560, "height": 1440, "audio_sample_rate": 44100})
    second_id = project["sources"][1]["source_id"]
    project = mark_source_missing(project, second_id)
    assert project["sources"][1]["missing"] is True
    project = relink_source(project, second_id, "D:/再リンク/二つ目.mkv", {"media_fingerprint": "second", "fps": 60, "width": 2560, "height": 1440})
    assert project["sources"][1]["source_id"] == second_id
    plan = normalization_plan(project, target_fps=30, target_width=1280, target_height=720)
    assert all(item["target_size"] == [1280, 720] for item in plan["sources"])
    project = add_clip(project, first_id, 0, 4, timeline_start=0, timeline_end=4)
    script = build_concat_filter_script(project, output_path="C:/出力/short.mp4")
    assert "INPUT 0" in script and "INPUT 1" in script
    assert "format=yuv420p" in script


def test_candidates_preserve_source_diversity_and_speaker_styles_are_scoped():
    candidates = merge_source_candidates(
        [
            {"candidate_id": "a", "source_id": "one", "score": 1.0},
            {"candidate_id": "b", "source_id": "one", "score": 0.9},
            {"candidate_id": "c", "source_id": "two", "score": 0.2},
        ],
        limit=2,
    )
    assert {item["source_id"] for item in candidates} == {"one", "two"}
    assert speaker_style_key("one", "speaker") != speaker_style_key("two", "speaker")


def test_missing_source_cannot_be_used_for_clip():
    project = ensure_multi_source_project(_single_project())
    source_id = project["sources"][0]["source_id"]
    project = mark_source_missing(project, source_id)
    with pytest.raises(MultiSourceError):
        add_clip(project, source_id, 0, 1)
