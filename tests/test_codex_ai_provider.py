from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from openai_codex.generated.v2_all import ModelListResponse
from openai_codex.models import JsonObject

from src.ai_provider import AIProviderEvent, AIProviderPrompt
from src.codex_ai_provider import CodexAIProvider
from tests.codex_fixtures import FakeSDKClient
from tests.codex_fixtures import notification as CodexNotification
from tests.typed_case import TypedTestCase


class FakeCodexClient(FakeSDKClient):
    def __init__(self, *, authenticated: bool = True) -> None:
        super().__init__(authenticated=authenticated, fast_model=False)


class CodexAIProviderTests(TypedTestCase):
    def test_account_model_session_stream_and_cancel_are_adapterized(self) -> None:
        client = FakeCodexClient()
        provider = CodexAIProvider(client_factory=lambda: client)
        self.addCleanup(provider.close)
        events: list[AIProviderEvent] = []
        provider.subscribe(events.append)

        state = provider.connect()
        self.assertEqual(state.availability, "available")
        self.assertEqual(state.auth_state, "authenticated")
        self.assertEqual([model.model_id for model in state.models], ["gpt-default"])
        self.assertEqual(state.selected_model, "gpt-default")

        session = provider.new_session(
            model_id="gpt-default",
            workspace_root=str(Path.cwd()),
        )
        result = provider.send_prompt(
            AIProviderPrompt(
                session=session,
                prompt="こんにちは",
                model_id="gpt-default",
                workspace_root=str(Path.cwd()),
            )
        )
        provider.cancel_active_turn(session_id=session.session_id, turn_id=result.turn_id)

        self.assertEqual(client.thread_params["approvalPolicy"], "never")
        self.assertEqual(client.thread_params["sandbox"], "read-only")
        self.assertEqual(client.turn_params["approval_policy"], "never")
        self.assertEqual(
            client.turn_params["sandbox_policy"],
            {"type": "readOnly", "networkAccess": False},
        )
        self.assertEqual(client.interrupted, ("thread-1", "turn-1"))
        deadline = time.monotonic() + 2
        while not any(event.kind == "turn_completed" for event in events) and time.monotonic() < deadline:
            time.sleep(0.01)
        stream_events = {
            "turn_started",
            "text_delta",
            "message_completed",
            "turn_completed",
        }
        self.assertEqual(
            [event.kind for event in events if event.kind in stream_events],
            ["turn_started", "text_delta", "text_delta", "message_completed", "turn_completed"],
        )

    def test_unauthenticated_account_has_no_models_and_login_state_is_generic(self) -> None:
        client = FakeCodexClient(authenticated=False)
        provider = CodexAIProvider(client_factory=lambda: client)
        self.addCleanup(provider.close)
        state = provider.connect()
        self.assertEqual(state.auth_state, "unauthenticated")
        self.assertEqual(state.models, ())

        state = provider.login()
        self.assertEqual(state.auth_state, "login_pending")
        self.assertEqual(state.login_id, "login-1")
        self.assertEqual(state.login_url, "https://example.invalid/login")

    def test_saved_model_is_selected_from_a_later_page(self) -> None:
        client = FakeCodexClient()
        first = client.model_list()
        first.next_cursor = "page-2"
        saved = first.data[0].model_copy()
        saved.id = saved.model = "gpt-saved"
        saved.display_name = "Saved model"
        saved.is_default = False
        second = ModelListResponse(data=[], next_cursor="page-3")
        third = ModelListResponse(data=[saved])
        pages = [second, third]
        provider = CodexAIProvider(client_factory=lambda: client, preferred_model="gpt-saved")
        self.addCleanup(provider.close)
        with (
            patch.object(client, "model_list", return_value=first),
            patch.object(client, "request", side_effect=pages) as request,
        ):
            state = provider.connect()
        for cursor in ("page-2", "page-3"):
            params: JsonObject = {"cursor": cursor, "includeHidden": False}
            request.assert_any_call("model/list", params, response_model=ModelListResponse)
        self.assertEqual([model.model_id for model in state.models], ["gpt-default", "gpt-saved"])
        self.assertEqual(state.selected_model, "gpt-saved")
        self.assertEqual(state.error, "")

    def test_invalid_model_cursor_fails_without_publishing_a_partial_list(self) -> None:
        for cursor in ("", "repeated-cursor"):
            with self.subTest(cursor=cursor):
                client = FakeCodexClient()
                page = client.model_list()
                page.next_cursor = cursor
                provider = CodexAIProvider(client_factory=lambda: client, preferred_model="gpt-saved")
                self.addCleanup(provider.close)
                with (
                    patch.object(client, "model_list", return_value=page),
                    patch.object(client, "request", return_value=page),
                ):
                    state = provider.connect()
                self.assertEqual(state.availability, "error")
                self.assertEqual(state.models, ())
                self.assertEqual(state.selected_model, "gpt-saved")
                self.assertIn("ページ", state.error)

    def test_model_page_failure_does_not_mark_a_saved_model_unavailable(self) -> None:
        client = FakeCodexClient()
        first = client.model_list()
        first.next_cursor = "page-2"
        provider = CodexAIProvider(client_factory=lambda: client, preferred_model="gpt-saved")
        self.addCleanup(provider.close)
        with (
            patch.object(client, "model_list", return_value=first),
            patch.object(client, "request", side_effect=OSError("second page failed")),
        ):
            state = provider.connect()
        self.assertEqual(state.availability, "error")
        self.assertEqual(state.models, ())
        self.assertEqual(state.selected_model, "gpt-saved")
        self.assertIn("second page failed", state.error)

    def test_model_pagination_is_bounded(self) -> None:
        client = FakeCodexClient()
        first = client.model_list()
        first.next_cursor = "page-2"
        second = ModelListResponse(data=first.data, next_cursor="page-3")
        provider = CodexAIProvider(client_factory=lambda: client)
        self.addCleanup(provider.close)
        with (
            patch("src.codex_ai_provider.MAX_MODEL_LIST_PAGES", 2),
            patch.object(client, "model_list", return_value=first),
            patch.object(client, "request", return_value=second),
        ):
            state = provider.connect()
        self.assertEqual(state.availability, "error")
        self.assertEqual(state.models, ())
        self.assertIn("ページ", state.error)

    def test_connection_failure_is_exposed_as_provider_error_state(self) -> None:
        class BrokenClient(FakeCodexClient):
            def start(self) -> None:
                raise OSError("secret local path")

        provider = CodexAIProvider(client_factory=BrokenClient)
        self.addCleanup(provider.close)
        state = provider.connect()
        self.assertEqual(state.availability, "error")
        self.assertEqual(state.auth_state, "error")
        self.assertIn("接続できません", state.error)

    def test_failed_turn_without_error_message_uses_fallback(self) -> None:
        client = FakeCodexClient()
        provider = CodexAIProvider(client_factory=lambda: client)
        self.addCleanup(provider.close)
        events: list[AIProviderEvent] = []
        provider.subscribe(events.append)
        provider.connect()
        callback = provider._on_notification

        for turn in (
            {"status": "failed"},
            {"status": "failed", "error": {}},
            {"status": "failed", "error": {"message": None}},
        ):
            with self.subTest(turn=turn):
                callback(CodexNotification("turn/completed", {"turn": turn}))
                self.assertEqual(events[-1].error, "原因を確認できません")

    def test_close_during_client_creation_prevents_late_runtime_start(self) -> None:
        client = FakeCodexClient()
        entered, release = threading.Event(), threading.Event()

        def create_client() -> FakeCodexClient:
            entered.set()
            if not release.wait(5):
                raise TimeoutError("fixture factory was not released")
            return client

        provider = CodexAIProvider(client_factory=create_client)
        self.addCleanup(provider.close)
        worker = threading.Thread(target=provider.connect)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            provider.close()
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertFalse(client.started)
        self.assertEqual(provider.state.availability, "disconnected")
        self.assertEqual(provider.connect().availability, "disconnected")
        self.assertFalse(client.started)


if __name__ == "__main__":
    unittest.main()
