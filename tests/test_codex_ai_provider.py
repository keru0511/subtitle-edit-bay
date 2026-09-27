from __future__ import annotations

import unittest
from collections.abc import Callable, Mapping
from pathlib import Path

from src.ai_provider import AIProviderEvent, AIProviderPrompt
from src.codex_ai_provider import CodexAIProvider
from src.codex_app_server_client import CodexNotification
from tests.typed_case import TypedTestCase


class FakeCodexClient:
    def __init__(self, *, authenticated: bool = True) -> None:
        self.notification_callback: Callable[[CodexNotification], None] | None = None
        self.disconnect_callback: Callable[[Exception], None] | None = None
        self.authenticated = authenticated
        self.started = False
        self.thread_params: dict[str, object] = {}
        self.resume_params: dict[str, object] = {}
        self.turn_params: dict[str, object] = {}
        self.interrupted: tuple[str, str] | None = None

    def start(self) -> dict[str, object]:
        self.started = True
        return {}

    def stop(self) -> None:
        self.started = False

    def account_read(self, *, refresh_token: bool = False) -> dict[str, object]:
        if self.authenticated:
            return {"account": {"type": "chatgpt", "planType": "plus"}}
        return {"account": None, "requiresOpenaiAuth": True}

    def account_login_start(
        self,
        *,
        login_type: str = "chatgpt",
        use_hosted_login_success_page: bool = True,
        app_brand: str = "chatgpt",
    ) -> dict[str, object]:
        return {"loginId": "login-1", "authUrl": "https://example.invalid/login"}

    def account_logout(self) -> dict[str, object]:
        self.authenticated = False
        return {}

    def model_list(self, *, limit: int = 100, include_hidden: bool = False) -> dict[str, object]:
        return {
            "data": [
                {"id": "gpt-default", "displayName": "Default", "isDefault": True},
                {"id": "gpt-hidden", "displayName": "Hidden", "hidden": True},
            ]
        }

    def thread_start(self, params: Mapping[str, object] | None = None) -> dict[str, object]:
        self.thread_params = dict(params or {})
        return {"thread": {"id": "thread-1"}}

    def thread_resume(
        self,
        thread_id: str,
        *,
        model: str | None = None,
        cwd: str | Path | None = None,
        approval_policy: str | None = None,
        sandbox: str | None = None,
    ) -> dict[str, object]:
        self.resume_params = {
            "thread_id": thread_id,
            "model": model,
            "cwd": cwd,
            "approval_policy": approval_policy,
            "sandbox": sandbox,
        }
        return {"thread": {"id": thread_id}}

    def turn_start(
        self,
        *,
        thread_id: str,
        prompt: str,
        model: str | None = None,
        cwd: str | Path | None = None,
        approval_policy: str | None = None,
        sandbox_policy: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        self.turn_params = {
            "thread_id": thread_id,
            "prompt": prompt,
            "model": model,
            "cwd": cwd,
            "approval_policy": approval_policy,
            "sandbox_policy": sandbox_policy,
        }
        if self.notification_callback:
            self.notification_callback(CodexNotification("turn/started", {"turn": {"id": "turn-1"}}))
            self.notification_callback(CodexNotification("item/agentMessage/delta", {"delta": "返"}))
            self.notification_callback(
                CodexNotification(
                    "item/completed",
                    {"item": {"type": "agentMessage", "text": "返答"}},
                )
            )
            self.notification_callback(CodexNotification("turn/completed", {"turn": {"status": "completed"}}))
        return {"turn": {"id": "turn-1"}}

    def turn_interrupt(self, turn_id: str, *, thread_id: str) -> dict[str, object]:
        self.interrupted = (thread_id, turn_id)
        return {}


class CodexAIProviderTests(TypedTestCase):
    def test_account_model_session_stream_and_cancel_are_adapterized(self) -> None:
        client = FakeCodexClient()
        provider = CodexAIProvider(client_factory=lambda: client)
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
        stream_events = {
            "turn_started",
            "text_delta",
            "message_completed",
            "turn_completed",
        }
        self.assertEqual(
            [event.kind for event in events if event.kind in stream_events],
            ["turn_started", "text_delta", "message_completed", "turn_completed"],
        )

    def test_unauthenticated_account_has_no_models_and_login_state_is_generic(self) -> None:
        client = FakeCodexClient(authenticated=False)
        provider = CodexAIProvider(client_factory=lambda: client)
        state = provider.connect()
        self.assertEqual(state.auth_state, "unauthenticated")
        self.assertEqual(state.models, ())

        state = provider.login()
        self.assertEqual(state.auth_state, "login_pending")
        self.assertEqual(state.login_id, "login-1")
        self.assertEqual(state.login_url, "https://example.invalid/login")

    def test_connection_failure_is_exposed_as_provider_error_state(self) -> None:
        class BrokenClient(FakeCodexClient):
            def start(self) -> dict[str, object]:
                raise OSError("secret local path")

        provider = CodexAIProvider(client_factory=BrokenClient)
        state = provider.connect()
        self.assertEqual(state.availability, "error")
        self.assertEqual(state.auth_state, "error")
        self.assertIn("接続できません", state.error)

    def test_failed_turn_without_error_message_uses_fallback(self) -> None:
        client = FakeCodexClient()
        provider = CodexAIProvider(client_factory=lambda: client)
        events: list[AIProviderEvent] = []
        provider.subscribe(events.append)
        provider.connect()
        callback = client.notification_callback
        assert callback is not None

        for turn in (
            {"status": "failed"},
            {"status": "failed", "error": {}},
            {"status": "failed", "error": {"message": None}},
        ):
            with self.subTest(turn=turn):
                callback(CodexNotification("turn/completed", {"turn": turn}))
                self.assertEqual(events[-1].error, "原因を確認できません")


if __name__ == "__main__":
    unittest.main()
