from __future__ import annotations

import json

import pytest

from src.thumbnail_candidates import build_contact_sheet_command, build_thumbnail_command, rank_thumbnail_candidates
from src.youtube_package import (
    Chapter,
    YouTubePackageError,
    add_chapter,
    adjust_chapter,
    build_post_package,
    delete_chapter,
    is_stale,
    rename_chapter,
    write_post_package,
)


def _project():
    return {
        "revision": 12,
        "title": "default",
        "youtube_text": {
            "title": "完成版タイトル",
            "description": "説明\n本文",
            "pinned_comment": "固定コメント",
            "keywords": ["実況", "編集"],
        },
        "chapters": [{"id": "intro", "start": 0, "title": "イントロ"}],
        "duration": 60,
    }


def test_package_reuses_text_and_supports_chapter_edits(tmp_path):
    chapters = add_chapter(_project()["chapters"], Chapter("main", 10, "本編"))
    chapters = rename_chapter(chapters, "main", "本編 改訂")
    chapters = adjust_chapter(chapters, "main", 12)
    chapters = delete_chapter(chapters, "intro")
    package = build_post_package(_project(), chapters=chapters, settings={"style": "default"})
    assert package["title"] == "完成版タイトル"
    assert package["description"] == "説明\n本文"
    assert package["chapters_text"] == "00:00:12 本編 改訂"
    assert package["upload"] == {"enabled": False, "account": None}
    assert not is_stale(package, 12, {"style": "default"})
    assert is_stale(package, 13, {"style": "default"})

    output = tmp_path / "投稿用 日本語"
    write_post_package(_project(), output, settings={"style": "default"})
    assert (output / "manifest.json").exists()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["upload_performed"] is False
    with pytest.raises(YouTubePackageError):
        write_post_package(_project(), output, settings={"style": "default"})


def test_chapter_validation_rejects_duplicate_or_invalid_values():
    with pytest.raises(YouTubePackageError):
        build_post_package(_project(), chapters=[{"id": "a", "start": 2, "title": "A"}, {"id": "b", "start": 2, "title": "B"}])
    with pytest.raises(YouTubePackageError):
        build_post_package(_project(), chapters=[{"id": "a", "start": 60, "title": "A"}])


def test_thumbnail_ranking_and_commands_are_local_only():
    ranked = rank_thumbnail_candidates(
        [
            {"candidate_id": "dark", "highlight_score": 0.9, "brightness": 0.02},
            {"candidate_id": "good", "highlight_score": 0.8, "brightness": 0.55},
        ]
    )
    assert ranked[0]["candidate_id"] == "good"
    command = build_thumbnail_command("C:/素材/録画.mkv", 12.5, "C:/出力/frame.png")
    assert command[0] == "ffmpeg"
    assert "-frames:v" in command
    sheet = build_contact_sheet_command("C:/出力/*.png", "C:/出力/contact.png")
    assert "tile=3x3:padding=4:margin=4" in sheet
