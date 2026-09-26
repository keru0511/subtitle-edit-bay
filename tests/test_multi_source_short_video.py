from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from src.data_boundary import is_object_list, is_object_mapping
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
    remove_source,
    speaker_style_key,
)
from tests.typed_case import TypedTestCase


def _single_project() -> dict[str, object]:
    return {"video_path": "C:/素材/一つ目.mkv", "clips": [{"source_start": 0, "source_end": 2}]}


def _object_list(project: Mapping[str, object], key: str) -> list[object]:
    value = project[key]
    if not is_object_list(value):
        raise AssertionError(f"{key} はリストである必要があります")
    return value


def _object_mapping(value: object) -> Mapping[str, object]:
    if not is_object_mapping(value) or any(not isinstance(key, str) for key in value):
        raise AssertionError("値は文字列キーのオブジェクトである必要があります")
    return cast(Mapping[str, object], value)


def _source(project: Mapping[str, object], index: int) -> Mapping[str, object]:
    return _object_mapping(_object_list(project, "sources")[index])


def _source_id(project: Mapping[str, object], index: int) -> str:
    value = _source(project, index)["source_id"]
    if not isinstance(value, str):
        raise AssertionError("source_id は文字列である必要があります")
    return value


class MultiSourceShortVideoTests(TypedTestCase):
    def test_single_source_migrates_without_losing_legacy_fields(self) -> None:
        project = ensure_multi_source_project(_single_project())
        self.assertEqual(project["video_path"], "C:/素材/一つ目.mkv")
        self.assertEqual(len(_object_list(project, "sources")), 1)
        self.assertEqual(_object_mapping(_object_list(project, "clips")[0])["source_id"], _source_id(project, 0))

    def test_mixed_sources_have_stable_ids_missing_relink_and_normalization(self) -> None:
        project = ensure_multi_source_project(_single_project())
        first_id = _source_id(project, 0)
        project = add_source(
            project,
            "C:/素材/二つ目.mkv",
            {
                "media_fingerprint": "second",
                "fps": 60,
                "width": 2560,
                "height": 1440,
                "audio_sample_rate": 44100,
            },
        )
        second_id = _source_id(project, 1)
        project = mark_source_missing(project, second_id)
        self.assertTrue(_source(project, 1)["missing"])
        project = relink_source(
            project,
            second_id,
            "D:/再リンク/二つ目.mkv",
            {"media_fingerprint": "second", "fps": 60, "width": 2560, "height": 1440},
        )
        self.assertEqual(_source_id(project, 1), second_id)
        plan = normalization_plan(project, target_fps=30, target_width=1280, target_height=720)
        self.assertTrue(all(item["target_size"] == [1280, 720] for item in plan["sources"]))
        self.assertTrue(all("aresample" not in ",".join(item["video_filters"]) for item in plan["sources"]))
        self.assertTrue(all("aresample=48000" in item["audio_filters"] for item in plan["sources"]))
        project = add_clip(project, first_id, 0, 4, timeline_start=0, timeline_end=4)
        script = build_concat_filter_script(project, output_path="C:/出力/short.mp4")
        self.assertIn("INPUT 0", script)
        self.assertIn("INPUT 1", script)
        self.assertIn("format=yuv420p", script)

    def test_normalization_rejects_non_finite_or_invalid_dimensions(self) -> None:
        project = ensure_multi_source_project(_single_project())
        with self.assertRaises(MultiSourceError):
            normalization_plan(project, target_fps=float("nan"))
        with self.assertRaises(MultiSourceError):
            normalization_plan(project, target_width=0)

    def test_normalization_pads_mixed_aspect_ratios_to_common_size(self) -> None:
        project = ensure_multi_source_project(
            {
                "sources": [
                    {"path": "wide.mkv", "width": 1920, "height": 1080},
                    {"path": "four-by-three.mkv", "width": 1440, "height": 1080},
                ]
            }
        )

        plan = normalization_plan(project, target_width=1920, target_height=1080)
        for source_plan in plan["sources"]:
            video_filters = ",".join(source_plan["video_filters"])
            self.assertIn("scale=1920:1080:force_original_aspect_ratio=decrease", video_filters)
            self.assertIn("pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black", video_filters)
            self.assertNotIn("aresample", video_filters)

        script = build_concat_filter_script(project)
        self.assertEqual(script.count("pad=1920:1080"), 2)

    def test_candidates_preserve_source_diversity_and_speaker_styles_are_scoped(self) -> None:
        candidates = merge_source_candidates(
            [
                {"candidate_id": "a", "source_id": "one", "score": 1.0},
                {"candidate_id": "b", "source_id": "one", "score": 0.9},
                {"candidate_id": "c", "source_id": "two", "score": 0.2},
            ],
            limit=2,
        )
        self.assertEqual({item["source_id"] for item in candidates}, {"one", "two"})
        self.assertNotEqual(speaker_style_key("one", "speaker"), speaker_style_key("two", "speaker"))

    def test_missing_source_cannot_be_used_for_clip(self) -> None:
        project = ensure_multi_source_project(_single_project())
        source_id = _source_id(project, 0)
        project = mark_source_missing(project, source_id)
        with self.assertRaises(MultiSourceError):
            add_clip(project, source_id, 0, 1)

    def test_source_removal_respects_clip_references(self) -> None:
        project = ensure_multi_source_project({"sources": [{"path": "first.mkv"}, {"path": "second.mkv"}]})
        second_id = _source_id(project, 1)
        project = add_clip(project, second_id, 0, 1)

        with self.assertRaises(MultiSourceError):
            remove_source(project, second_id)

        removed = remove_source(project, second_id, remove_clips=True)
        self.assertEqual(len(_object_list(removed, "sources")), 1)
        self.assertEqual(_object_list(removed, "clips"), [])
