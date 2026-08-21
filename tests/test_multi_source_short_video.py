from __future__ import annotations

import unittest

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


class MultiSourceShortVideoTests(unittest.TestCase):
    def test_single_source_migrates_without_losing_legacy_fields(self):
        project = ensure_multi_source_project(_single_project())
        self.assertEqual(project["video_path"], "C:/素材/一つ目.mkv")
        self.assertEqual(len(project["sources"]), 1)
        self.assertEqual(project["clips"][0]["source_id"], project["sources"][0]["source_id"])

    def test_mixed_sources_have_stable_ids_missing_relink_and_normalization(self):
        project = ensure_multi_source_project(_single_project())
        first_id = project["sources"][0]["source_id"]
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
        second_id = project["sources"][1]["source_id"]
        project = mark_source_missing(project, second_id)
        self.assertTrue(project["sources"][1]["missing"])
        project = relink_source(
            project,
            second_id,
            "D:/再リンク/二つ目.mkv",
            {"media_fingerprint": "second", "fps": 60, "width": 2560, "height": 1440},
        )
        self.assertEqual(project["sources"][1]["source_id"], second_id)
        plan = normalization_plan(project, target_fps=30, target_width=1280, target_height=720)
        self.assertTrue(all(item["target_size"] == [1280, 720] for item in plan["sources"]))
        self.assertTrue(all("aresample" not in ",".join(item["video_filters"]) for item in plan["sources"]))
        self.assertTrue(all("aresample=48000" in item["audio_filters"] for item in plan["sources"]))
        project = add_clip(project, first_id, 0, 4, timeline_start=0, timeline_end=4)
        script = build_concat_filter_script(project, output_path="C:/出力/short.mp4")
        self.assertIn("INPUT 0", script)
        self.assertIn("INPUT 1", script)
        self.assertIn("format=yuv420p", script)

    def test_normalization_rejects_non_finite_or_invalid_dimensions(self):
        project = ensure_multi_source_project(_single_project())
        with self.assertRaises(MultiSourceError):
            normalization_plan(project, target_fps=float("nan"))
        with self.assertRaises(MultiSourceError):
            normalization_plan(project, target_width=0)

    def test_normalization_pads_mixed_aspect_ratios_to_common_size(self):
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

    def test_candidates_preserve_source_diversity_and_speaker_styles_are_scoped(self):
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

    def test_missing_source_cannot_be_used_for_clip(self):
        project = ensure_multi_source_project(_single_project())
        source_id = project["sources"][0]["source_id"]
        project = mark_source_missing(project, source_id)
        with self.assertRaises(MultiSourceError):
            add_clip(project, source_id, 0, 1)
