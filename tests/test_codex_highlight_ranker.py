from __future__ import annotations

import unittest
from collections.abc import Mapping

from src.codex_highlight_ranker import (
    HighlightRankerSettings,
    build_ranker_context,
    rank_highlight_candidates,
)
from src.data_boundary import is_object_mapping
from tests.typed_case import TypedTestCase


class FakeRankerClient:
    def __init__(self, output: object) -> None:
        self.output = output
        self.context: Mapping[str, object] = {}
        self.thread_params: dict[str, object] = {}
        self.turn_params: dict[str, object] = {}

    def thread_start(self, params: Mapping[str, object] | None = None) -> Mapping[str, object]:
        self.thread_params = dict(params or {})
        return {"threadId": "thread-highlight"}

    def turn_start(self, **kwargs: object) -> Mapping[str, object]:
        self.turn_params = dict(kwargs)
        context = kwargs["context"]
        if not is_object_mapping(context):
            raise AssertionError("ranker context must be an object")
        self.context = {str(key): value for key, value in context.items()}
        return {"output": self.output}


class CodexHighlightRankerTests(TypedTestCase):
    def setUp(self) -> None:
        self.candidates: list[dict[str, object]] = [
            {
                "id": "h1",
                "start": 1.0,
                "end": 5.0,
                "score": 0.8,
                "category": "conversation",
                "subtitle_excerpt": "A" * 300,
            },
            {"id": "h2", "start": 20.0, "end": 24.0, "score": 0.4, "category": "conversation", "subtitle_excerpt": "B"},
        ]

    def test_context_is_bounded_and_contains_no_media_paths(self) -> None:
        context = build_ranker_context(self.candidates, HighlightRankerSettings(max_candidates=1, max_excerpt_chars=20))
        self.assertEqual(context["candidate_count"], 1)
        self.assertEqual(len(context["candidates"][0]["subtitle_excerpt"]), 20)
        self.assertNotIn("path", str(context))

    def test_valid_semantic_scores_are_combined(self) -> None:
        client = FakeRankerClient(
            {
                "rankings": [
                    {"id": "h1", "semantic_score": 0.1, "category": "reaction", "reason": "反応が強い", "hook": "驚き"},
                    {"id": "h2", "semantic_score": 0.9, "category": "gameplay", "reason": "展開がある", "hook": "展開"},
                ]
            }
        )
        result = rank_highlight_candidates(self.candidates, client=client)
        self.assertFalse(result.fallback)
        self.assertEqual(result.candidates[0]["id"], "h2")
        self.assertEqual(result.candidates[0]["semantic_category"], "gameplay")
        self.assertEqual(client.context["candidate_count"], 2)
        self.assertEqual(
            client.thread_params,
            {
                "approvalPolicy": "never",
                "sandbox": "read-only",
            },
        )
        self.assertEqual(client.turn_params["approval_policy"], "never")
        self.assertEqual(
            client.turn_params["sandbox_policy"],
            {
                "type": "readOnly",
                "networkAccess": False,
            },
        )

    def test_unknown_id_or_invalid_output_falls_back_to_local_order(self) -> None:
        client = FakeRankerClient(
            {"rankings": [{"id": "missing", "semantic_score": 0.8, "category": "other", "reason": "", "hook": ""}]}
        )
        result = rank_highlight_candidates(self.candidates, client=client)
        self.assertTrue(result.fallback)
        self.assertEqual([item["id"] for item in result.candidates], ["h1", "h2"])

    def test_missing_or_duplicate_ranking_ids_fall_back_to_local_order(self) -> None:
        for rankings in (
            [{"id": "h1", "semantic_score": 0.8, "category": "other", "reason": "", "hook": ""}],
            [
                {"id": "h1", "semantic_score": 0.8, "category": "other", "reason": "", "hook": ""},
                {"id": "h1", "semantic_score": 0.7, "category": "other", "reason": "", "hook": ""},
            ],
        ):
            with self.subTest(rankings=rankings):
                result = rank_highlight_candidates(
                    self.candidates,
                    client=FakeRankerClient({"rankings": rankings}),
                )
                self.assertTrue(result.fallback)
                self.assertEqual([item["id"] for item in result.candidates], ["h1", "h2"])

    def test_negative_excerpt_limit_is_clamped_to_empty(self) -> None:
        context = build_ranker_context(
            self.candidates,
            HighlightRankerSettings(max_excerpt_chars=-1),
        )
        self.assertEqual(context["candidates"][0]["reason"], "")
        self.assertEqual(context["candidates"][0]["subtitle_excerpt"], "")

    def test_unavailable_client_is_a_safe_fallback(self) -> None:
        result = rank_highlight_candidates(self.candidates)
        self.assertTrue(result.fallback)
        self.assertIn("unavailable", result.error)


if __name__ == "__main__":
    unittest.main()
