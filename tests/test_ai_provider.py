from __future__ import annotations

import time
import unittest
from pathlib import Path

from src.ai_provider import (
    AIModel,
    AIProviderEvent,
    AIProviderPrompt,
    AIProviderPromptResult,
    AIProviderSession,
    AIProviderState,
)
from src.gui_codex_chat_state import CodexChatController
from tests.typed_case import TypedTestCase


def wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        time.sleep(0.01)
    if not predicate():
        raise AssertionError("condition was not reached")


class FakeProvider:
    provider_id = "fake"
    display_name = "Fake provider"

    def __init__(self, *, authenticated: bool = True, fail_send: bool = False) -> None:
        self._state = AIProviderState(
            availability="disconnected",
            auth_state="unknown",
        )
        self._authenticated = authenticated
        self._fail_send = fail_send
        self._listeners = []
        self.sessions: list[AIProviderSession] = []
        self.prompts: list[AIProviderPrompt] = []
        self.cancelled: tuple[str, str] | None = None

    @property
    def state(self) -> AIProviderState:
        return self._state

    def subscribe(self, listener) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _publish(self, state: AIProviderState) -> AIProviderState:
        self._state = state
        for listener in tuple(self._listeners):
            listener(AIProviderEvent(kind="state_changed"))
        return state

    def connect(self, *, force: bool = False) -> AIProviderState:
        if not self._authenticated:
            return self._publish(
                AIProviderState(availability="available", auth_state="unauthenticated")
            )
        return self._publish(
            AIProviderState(
                availability="available",
                auth_state="authenticated",
                auth_label="Fake",
                models=(AIModel("fake-fast", "Fake Fast", True),),
                selected_model="fake-fast",
            )
        )

    def refresh(self) -> AIProviderState:
        return self._state

    def login(self, *, relogin: bool = False) -> AIProviderState:
        return self._publish(
            AIProviderState(availability="available", auth_state="login_pending")
        )

    def logout(self) -> AIProviderState:
        self._authenticated = False
        return self._publish(
            AIProviderState(availability="available", auth_state="unauthenticated")
        )

    def select_model(self, model_id: str) -> AIProviderState:
        return self._publish(
            AIProviderState(
                availability="available",
                auth_state="authenticated",
                models=self._state.models,
                selected_model=model_id,
            )
        )

    def new_session(
        self,
        *,
        model_id: str,
        workspace_root: str,
        existing_session_id: str = "",
    ) -> AIProviderSession:
        session = AIProviderSession(existing_session_id or "fake-session")
        self.sessions.append(session)
        return session

    def send_prompt(self, request: AIProviderPrompt) -> AIProviderPromptResult:
        if self._fail_send:
            raise RuntimeError("fake provider failure")
        self.prompts.append(request)
        for listener in tuple(self._listeners):
            listener(AIProviderEvent(kind="turn_started", turn_id="fake-turn"))
            listener(AIProviderEvent(kind="text_delta", text="fake"))
            listener(AIProviderEvent(kind="message_completed", text="fake response"))
            listener(
                AIProviderEvent(
                    kind="turn_completed",
                    status="completed",
                )
            )
        return AIProviderPromptResult(turn_id="fake-turn")

    def cancel_active_turn(self, *, session_id: str, turn_id: str) -> None:
        self.cancelled = (session_id, turn_id)

    def close(self) -> None:
        self._state = AIProviderState(availability="disconnected")


class AIProviderControllerTests(TypedTestCase):
    def test_fake_provider_streams_through_controller_without_provider_rpc_names(self) -> None:
        provider = FakeProvider()
        controller = CodexChatController(
            provider_factory=lambda: provider,
            workspace_root=Path.cwd(),
        )
        try:
            controller.connect()
            wait_for(lambda: controller.snapshot.auth_state == "authenticated")
            controller.send_message("hello")
            wait_for(lambda: controller.snapshot.chat_state == "idle")
            self.assertEqual(provider.prompts[0].prompt, "hello")
            self.assertEqual(controller.snapshot.messages[-1]["text"], "fake response")
            self.assertEqual(controller.snapshot.messages[-1]["status"], "completed")
        finally:
            controller.shutdown()

    def test_unauthenticated_provider_is_visible_and_rejects_send(self) -> None:
        provider = FakeProvider(authenticated=False)
        controller = CodexChatController(
            provider_factory=lambda: provider,
            workspace_root=Path.cwd(),
        )
        try:
            controller.connect()
            wait_for(lambda: controller.snapshot.auth_state == "unauthenticated")
            controller.send_message("hello")
            self.assertIn("ログイン", controller.snapshot.error)
            self.assertEqual(provider.prompts, [])
        finally:
            controller.shutdown()

    def test_provider_send_failure_becomes_controller_error(self) -> None:
        provider = FakeProvider(fail_send=True)
        controller = CodexChatController(
            provider_factory=lambda: provider,
            workspace_root=Path.cwd(),
        )
        try:
            controller.connect()
            wait_for(lambda: controller.snapshot.auth_state == "authenticated")
            controller.send_message("hello")
            wait_for(lambda: controller.snapshot.chat_state == "send_failed")
            self.assertIn("送信できません", controller.snapshot.error)
            self.assertEqual(controller.snapshot.messages[-1]["status"], "error")
        finally:
            controller.shutdown()


if __name__ == "__main__":
    unittest.main()

