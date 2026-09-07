import unittest

from src.codex_chat_routing import route_subtitle_chat_request


class SubtitleChatRoutingTests(unittest.TestCase):
    def test_normal_chat_is_not_routed_to_subtitle_proposal(self) -> None:
        route = route_subtitle_chat_request(
            "この動画の内容を要約して",
            "auto",
            project_loaded=True,
            has_selection=True,
            current_time=3.0,
        )
        self.assertIsNone(route)

    def test_auto_scope_prefers_selection_without_expanding_to_all(self) -> None:
        route = route_subtitle_chat_request(
            "字幕を少し短くして",
            "auto",
            project_loaded=True,
            has_selection=True,
            current_time=3.0,
        )
        self.assertIsNotNone(route)
        assert route is not None
        self.assertEqual(route.scope, "selected")

    def test_auto_scope_uses_current_when_nothing_is_selected(self) -> None:
        route = route_subtitle_chat_request(
            "この字幕を分割して",
            "auto",
            project_loaded=True,
            has_selection=False,
            current_time=7.5,
        )
        self.assertIsNotNone(route)
        assert route is not None
        self.assertEqual(route.scope, "current")

    def test_all_requires_explicit_control_or_wording(self) -> None:
        inferred = route_subtitle_chat_request(
            "字幕を修正して",
            "auto",
            project_loaded=True,
            has_selection=False,
            current_time=1.0,
        )
        explicit = route_subtitle_chat_request(
            "すべての字幕を修正して",
            "auto",
            project_loaded=True,
            has_selection=False,
            current_time=1.0,
        )
        self.assertIsNotNone(inferred)
        self.assertIsNotNone(explicit)
        assert inferred is not None and explicit is not None
        self.assertEqual(inferred.scope, "current")
        self.assertEqual(explicit.scope, "all")

    def test_explicit_time_range_preserves_bounds(self) -> None:
        route = route_subtitle_chat_request(
            "読みやすくして",
            "time_range",
            project_loaded=True,
            has_selection=False,
            current_time=1.0,
            range_start=2.5,
            range_end=8.0,
        )
        self.assertIsNotNone(route)
        assert route is not None
        self.assertEqual((route.scope, route.range_start, route.range_end), ("time_range", 2.5, 8.0))
