from __future__ import annotations

from types import SimpleNamespace
import unittest

from src.codex_review import ReviewResult, build_review_context, review_context


def gui_stub(*, revision: int = 7, segments: list[dict] | None = None) -> SimpleNamespace:
    project_segments = segments if segments is not None else [
        {"id": "s1", "start": 0.0, "end": 2.0, "text": "読みやすい字幕", "speaker": "A"}
    ]
    dependencies = SimpleNamespace(ffmpeg=True, ffprobe=True, whisperx=True, cuda=False, nvenc=False)
    return SimpleNamespace(
        _project_revision=revision,
        _project={"video": {"path": "C:/secret/source.mp4"}, "segments": project_segments},
        _project_dirty=False,
        _cut_editor_available=True,
        _active_job="",
        _running=False,
        _processing_progress=SimpleNamespace(status="idle"),
        _dependencies=dependencies,
        subtitleSegments=project_segments,
        audioMixerChannels=[
            {"id": "voice", "kind": "voice", "label": "声", "enabled": True, "volume_percent": 100, "path": "C:/secret/voice.wav"}
        ],
        audioPreviewLevels={"voice": 0.4},
        audioMasterLevel=0.4,
        audioLimiterReductionDb=0.0,
        cutTimeline={"cuts": [], "source_duration": 30.0},
        shortVideoSettings={"enabled": False},
        shortVideoClips=[],
        highlightAnalysisState="idle",
        highlightCandidates=[],
        actionCapabilities={"canRenderNormal": True, "canRenderShort": False},
        projectDuration=30.0,
    )


class CodexReviewTests(unittest.TestCase):
    def test_context_is_path_free_and_chunks_long_subtitles(self) -> None:
        segments = [
            {"id": f"s-{index}", "start": index, "end": index + 1, "text": "字幕", "path": f"C:/secret/{index}.txt"}
            for index in range(501)
        ]
        context = build_review_context(gui_stub(segments=segments), subtitle_chunk_size=200)

        self.assertEqual([len(chunk) for chunk in context["subtitle_chunks"]], [200, 200, 101])
        self.assertNotIn("path", str(context).lower())
        self.assertNotIn("C:/secret", str(context))

        gui = gui_stub()
        gui.shortVideoSettings = {"enabled": True, "bgm": {"path": "C:/secret/bgm.wav", "volume": 0.5}}
        self.assertNotIn("C:/secret", str(build_review_context(gui)))

    def test_cross_domain_review_orders_blockers_and_routes_available_domains(self) -> None:
        gui = gui_stub(
            segments=[
                {"id": "empty", "start": 0.0, "end": 1.0, "text": ""},
                {"id": "dense", "start": 0.5, "end": 1.0, "text": "これは表示時間に対してかなり長すぎる字幕です"},
            ]
        )
        gui._project_dirty = True
        gui.audioMixerChannels[0]["volume_percent"] = 10
        gui.audioLimiterReductionDb = 8.0
        gui.shortVideoSettings = {"enabled": True}

        payload = review_context(build_review_context(gui)).to_json()
        categories = {item["category"] for item in payload["issues"]}

        self.assertTrue({"subtitle", "audio", "short", "render"}.issubset(categories))
        self.assertEqual(payload["issues"][0]["severity"], "blocking")
        self.assertEqual(payload["project_revision"], 7)
        for item in payload["issues"]:
            if item["category"] in {"subtitle", "audio", "short", "render"}:
                self.assertTrue(item["recommendation"].get("route"))

    def test_unavailable_timeline_is_reported_without_executable_route(self) -> None:
        gui = gui_stub()
        gui._cut_editor_available = False
        payload = review_context(build_review_context(gui)).to_json()
        timeline = next(item for item in payload["issues"] if item["category"] == "timeline")

        self.assertEqual(timeline["recommendation"], {"available": False})

    def test_review_only_does_not_mutate_and_stale_result_is_rejected(self) -> None:
        gui = gui_stub()
        before = repr(gui._project)
        result = review_context(build_review_context(gui))

        self.assertEqual(repr(gui._project), before)
        result.require_current_revision(7)
        with self.assertRaisesRegex(ValueError, "stale"):
            result.require_current_revision(8)

    def test_duplicate_findings_across_chunks_are_deduplicated(self) -> None:
        repeated = {"id": "same", "start": 0.0, "end": 1.0, "text": ""}
        result = review_context(build_review_context(gui_stub(segments=[repeated, repeated]), subtitle_chunk_size=25))
        empty_findings = [item for item in result.issues if item.reason == "空の字幕があります"]

        self.assertEqual(len(empty_findings), 1)

    def test_no_problem_project_has_empty_recommendation_order(self) -> None:
        payload = review_context(build_review_context(gui_stub())).to_json()

        self.assertEqual(payload["issues"], [])
        self.assertEqual(payload["recommended_order"], [])


if __name__ == "__main__":
    unittest.main()
