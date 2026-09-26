from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.gemini_acp_provider import GeminiAcpClient, GeminiAcpProvider
from src.gui_ai_facade import AIChatFacade
from src.gui_ai_chat_state import AIProviderChatRouter
from src.gui_codex_chat_state import CodexChatController, CodexChatSnapshot


ROOT = Path(__file__).resolve().parents[1]
FAKE_GEMINI = ROOT / "tests" / "fake_gemini_acp_server.py"


class StubChatController:
    def __init__(self, snapshot: CodexChatSnapshot) -> None:
        self._snapshot = snapshot
        self.connect_calls = 0
        self.reconnect_calls = 0
        self.login_calls = 0
        self.shutdown_calls = 0

    @property
    def snapshot(self) -> CodexChatSnapshot:
        return self._snapshot

    def connect(self) -> None:
        self.connect_calls += 1

    def reconnect(self) -> None:
        self.reconnect_calls += 1

    def login(self, *, relogin: bool = False) -> None:
        del relogin
        self.login_calls += 1

    def logout(self) -> None:
        self._snapshot = CodexChatSnapshot(
            **{**self._snapshot.__dict__, "auth_state": "unauthenticated", "messages": ()}
        )

    def select_model(self, model_id: str) -> None:
        self._snapshot = CodexChatSnapshot(
            **{**self._snapshot.__dict__, "selected_model": model_id}
        )

    def send_message(self, text: str) -> None:
        self._snapshot = CodexChatSnapshot(
            **{
                **self._snapshot.__dict__,
                "messages": self._snapshot.messages + (("text", text),),
            }
        )

    def begin_proposal(self, text: str, **kwargs: object) -> bool:
        del kwargs
        self.send_message(text)
        return True

    def complete_proposal(self, summary: str) -> None:
        self.send_message(summary)

    def fail_proposal(self, message: str, *, cancelled: bool = False) -> None:
        del message, cancelled

    def interrupt(self) -> None:
        self._snapshot = CodexChatSnapshot(
            **{**self._snapshot.__dict__, "chat_state": "idle"}
        )

    def new_chat(self) -> None:
        self._snapshot = CodexChatSnapshot(
            **{**self._snapshot.__dict__, "messages": (), "thread_id": ""}
        )

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def snapshot(
    provider_id: str,
    provider_name: str,
    *,
    connection_state: str = "ready",
    auth_state: str = "authenticated",
    messages: tuple[object, ...] = (),
    chat_state: str = "idle",
    models: tuple[dict[str, str], ...] = (),
    model_selection_supported: bool = True,
    login_available: bool = True,
    selected_model: str = "",
) -> CodexChatSnapshot:
    return CodexChatSnapshot(
        provider_id=provider_id,
        provider_name=provider_name,
        connection_state=connection_state,
        auth_state=auth_state,
        chat_state=chat_state,
        messages=messages,
        models=models,
        model_selection_supported=model_selection_supported,
        login_available=login_available,
        selected_model=selected_model,
    )


def wait_for(predicate, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    if not predicate():
        raise AssertionError("condition was not reached")


class AIProviderChatRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.codex = StubChatController(
            snapshot(
                "codex",
                "Codex",
                messages=(("provider", "codex"),),
                models=({"id": "gpt", "label": "GPT"},),
                selected_model="gpt",
            )
        )
        self.gemini = StubChatController(
            snapshot(
                "gemini",
                "Gemini",
                messages=(("provider", "gemini"),),
                models=(),
                model_selection_supported=False,
                login_available=False,
            )
        )
        self.states: list[CodexChatSnapshot] = []
        self.selected: list[str] = []
        self.router = AIProviderChatRouter(
            {"codex": self.codex, "gemini": self.gemini},
            on_state=self.states.append,
            on_selected_provider=self.selected.append,
        )

    def test_codex_is_default_and_provider_state_is_scoped(self) -> None:
        self.assertEqual(self.router.active_provider_id, "codex")
        self.assertEqual(
            [item["id"] for item in self.router.available_providers()],
            ["codex", "gemini"],
        )
        self.assertEqual(self.router.snapshot.messages, (("provider", "codex"),))

        self.assertTrue(self.router.select_provider("gemini"))
        self.assertEqual(self.selected, ["gemini"])
        self.assertEqual(self.router.snapshot.messages, (("provider", "gemini"),))
        self.assertFalse(self.router.select_provider("codex") is False)
        self.assertEqual(self.router.snapshot.messages, (("provider", "codex"),))

    def test_active_turn_blocks_provider_switch(self) -> None:
        self.codex._snapshot = snapshot("codex", "Codex", chat_state="streaming")
        self.assertFalse(self.router.select_provider("gemini"))
        self.assertEqual(self.router.active_provider_id, "codex")
        self.assertIn("応答中", self.router.snapshot.error)

    def test_unavailable_provider_is_hidden_but_active_error_remains_visible(self) -> None:
        self.gemini._snapshot = snapshot("gemini", "Gemini", connection_state="error")
        self.assertEqual([item["id"] for item in self.router.available_providers()], ["codex"])
        self.codex._snapshot = snapshot("codex", "Codex", connection_state="error")
        self.assertEqual([item["id"] for item in self.router.available_providers()], ["codex"])

    def test_model_inventory_and_login_capability_are_provider_specific(self) -> None:
        codex = self.router.available_providers()[0]
        self.assertTrue(codex["model_selection_supported"])
        self.assertTrue(codex["login_available"])
        self.router.select_provider("gemini")
        gemini = self.router.available_providers()[1]
        self.assertFalse(gemini["model_selection_supported"])
        self.assertFalse(gemini["login_available"])
        self.assertEqual(gemini["models"], [])
        self.router.select_provider("codex")
        self.codex.select_model("gpt")
        self.router.select_provider("gemini")
        self.router.select_provider("codex")
        self.assertEqual(self.router.snapshot.selected_model, "gpt")


class GeminiAuthHintTests(unittest.TestCase):
    @staticmethod
    def _hint(*, auth_state: str, login_available: bool) -> str:
        facade = SimpleNamespace(
            services=SimpleNamespace(
                chat_router=SimpleNamespace(
                    snapshot=CodexChatSnapshot(
                        provider_id="gemini",
                        provider_name="Gemini",
                        auth_state=auth_state,
                        login_available=login_available,
                    )
                )
            )
        )
        getter = AIChatFacade.aiChatAuthHint.fget
        assert getter is not None
        return getter(facade)

    def test_authenticated_gemini_without_login_action_has_no_auth_hint(self) -> None:
        self.assertEqual(
            self._hint(auth_state="authenticated", login_available=False),
            "",
        )

    def test_unauthenticated_gemini_without_login_action_keeps_auth_hint(self) -> None:
        self.assertEqual(
            self._hint(auth_state="unauthenticated", login_available=False),
            "Gemini CLIでログインしてください",
        )

    def test_unauthenticated_gemini_with_login_action_exposes_generic_login_route(self) -> None:
        facade = SimpleNamespace(
            services=SimpleNamespace(
                chat_router=SimpleNamespace(
                    snapshot=CodexChatSnapshot(
                        provider_id="gemini",
                        provider_name="Gemini",
                        auth_state="unauthenticated",
                        login_available=True,
                    )
                )
            )
        )
        login_available = AIChatFacade.aiChatLoginAvailable.fget
        assert login_available is not None
        self.assertTrue(login_available(facade))
        self.assertEqual(self._hint(auth_state="unauthenticated", login_available=True), "")


class GeminiProviderFactoryTests(unittest.TestCase):
    def test_factory_passes_saved_model_to_gemini_provider(self) -> None:
        backend = SimpleNamespace(
            workspace_root=ROOT,
            _settings={"gemini_model": "gemini-saved"},
        )
        with patch("src.gui_ai_facade.GeminiAcpProvider") as provider_class:
            AIChatFacade._create_gemini_chat_provider(SimpleNamespace(_backend=backend))
        provider_class.assert_called_once_with(
            workspace_root=ROOT,
            preferred_model="gemini-saved",
        )


class GeminiRouterFakeAcpE2ETests(unittest.TestCase):
    def test_gemini_chat_controller_streams_through_provider_router(self) -> None:
        provider = GeminiAcpProvider(
            workspace_root=ROOT,
            client_factory=lambda: GeminiAcpClient(
                [sys.executable, "-u", str(FAKE_GEMINI), "--acp"],
                cwd=ROOT,
                environment={
                    "FAKE_ACP_AUTH_STATE": "authenticated",
                    "FAKE_ACP_NO_AUTH_METHODS": "1",
                    "FAKE_ACP_MODELS": '[{"modelId":"router-model","name":"Router Model"}]',
                },
                request_timeout=2,
            ),
        )
        controller = CodexChatController(
            provider_factory=lambda: provider,
            workspace_root=ROOT,
            provider_id="gemini",
            provider_name="Gemini",
            initial_model_selection_supported=False,
            initial_login_available=False,
        )
        router = AIProviderChatRouter({"gemini": controller}, preferred_provider="gemini")
        try:
            self.assertFalse(router.snapshot.model_selection_supported)
            self.assertFalse(router.snapshot.login_available)
            router.connect()
            wait_for(lambda: router.snapshot.auth_state == "authenticated")
            router.send_message("fake ACPから応答してください")
            wait_for(lambda: router.snapshot.chat_state == "idle")
            self.assertEqual(router.snapshot.provider_id, "gemini")
            self.assertEqual(router.snapshot.provider_name, "Gemini")
            self.assertEqual(router.snapshot.auth_state, "authenticated")
            self.assertFalse(router.snapshot.login_available)
            facade = SimpleNamespace(services=SimpleNamespace(chat_router=router))
            getter = AIChatFacade.aiChatAuthHint.fget
            assert getter is not None
            self.assertEqual(getter(facade), "")
            self.assertTrue(router.snapshot.model_selection_supported)
            self.assertEqual(router.snapshot.selected_model, "router-model")
            self.assertEqual([item["id"] for item in router.snapshot.models], ["router-model"])
            self.assertEqual(router.snapshot.messages[-1]["text"], "hello world")
            self.assertEqual(router.snapshot.messages[-1]["status"], "completed")
        finally:
            router.shutdown()


if __name__ == "__main__":
    unittest.main()
