from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

from src.ai_provider import AIProviderEvent, AIProviderPrompt
from src.gemini_acp_provider import (
    GeminiAcpClient,
    GeminiAcpError,
    GeminiAcpProvider,
)
from src.gemini_runtime import GeminiRuntimeInfo


ROOT = Path(__file__).resolve().parent.parent
FAKE_SERVER = Path(__file__).resolve().parent / "fake_gemini_acp_server.py"


def wait_for(predicate, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    if not predicate():
        raise AssertionError("condition was not reached")


def fake_command() -> list[str]:
    return [sys.executable, "-u", str(FAKE_SERVER), "--acp"]


class GeminiAcpClientTests(unittest.TestCase):
    def test_command_rejects_experimental_and_automatic_approval_modes(self) -> None:
        with self.assertRaises(ValueError):
            GeminiAcpClient(fake_command() + ["--experimental-acp"])
        with self.assertRaises(ValueError):
            GeminiAcpClient(fake_command() + ["--yolo"])

    def test_initialize_rejects_unsupported_protocol_version(self) -> None:
        client = GeminiAcpClient(
            fake_command(),
            cwd=ROOT,
            environment={"FAKE_ACP_PROTOCOL_VERSION": "2"},
            request_timeout=2,
        )
        with self.assertRaises(GeminiAcpError):
            client.start()
        client.stop()

    def test_malformed_message_does_not_break_stream(self) -> None:
        events: list[object] = []
        environment = {"FAKE_ACP_MALFORMED": "1"}
        client = GeminiAcpClient(
            fake_command(),
            cwd=ROOT,
            environment=environment,
            request_timeout=2,
            notification_callback=events.append,
        )
        try:
            client.start()
            session = client.new_session(cwd=ROOT)
            response = client.prompt(
                session_id=str(session["sessionId"]),
                text="hello",
                turn_id="turn-1",
            )
            self.assertEqual(response["stopReason"], "end_turn")
            self.assertTrue(any(getattr(event, "method", "") == "protocol/error" for event in events))
            self.assertTrue(any(getattr(event, "method", "") == "session/update" for event in events))
        finally:
            client.stop()

    def test_permission_request_is_denied_without_exception(self) -> None:
        events: list[object] = []
        client = GeminiAcpClient(
            fake_command(),
            cwd=ROOT,
            environment={
                "FAKE_ACP_PERMISSION": "1",
                "FAKE_ACP_PERMISSION_REQUIRE_DENY": "1",
            },
            request_timeout=2,
            notification_callback=events.append,
        )
        try:
            client.start()
            session = client.new_session(cwd=ROOT)
            response = client.prompt(
                session_id=str(session["sessionId"]),
                text="hello",
                turn_id="turn-1",
            )
            self.assertEqual(response["stopReason"], "end_turn")
            permission = [
                event for event in events if getattr(event, "method", "") == "session/request_permission"
            ]
            self.assertEqual(len(permission), 1)
        finally:
            client.stop()

    def test_client_uses_stable_acp_session_methods(self) -> None:
        sent: list[dict[str, object]] = []

        class StubClient(GeminiAcpClient):
            def request(self, method, params=None, **kwargs):  # type: ignore[no-untyped-def]
                sent.append({"method": method, "params": params or {}})
                if method == "session/new":
                    return {"sessionId": "session-stable"}
                if method == "session/prompt":
                    return {"stopReason": "end_turn"}
                raise AssertionError(method)

            def notify(self, method, params=None):  # type: ignore[no-untyped-def]
                sent.append({"method": method, "params": params or {}})

            @property
            def is_running(self):
                return True

            @property
            def initialized(self):
                return True

        client = StubClient(fake_command())
        session = client.new_session(cwd=ROOT)
        client.prompt(session_id=str(session["sessionId"]), text="hello", turn_id="turn-1")
        client.cancel(session_id=str(session["sessionId"]), turn_id="turn-1")
        self.assertEqual(
            [item["method"] for item in sent],
            ["session/new", "session/prompt", "session/cancel"],
        )

    def test_subprocess_exit_fails_pending_request_and_notifies_disconnect(self) -> None:
        disconnects: list[Exception] = []
        client = GeminiAcpClient(
            fake_command(),
            cwd=ROOT,
            environment={"FAKE_ACP_EXIT_ON_PROMPT": "1"},
            request_timeout=2,
            disconnect_callback=disconnects.append,
        )
        try:
            client.start()
            session = client.new_session(cwd=ROOT)
            with self.assertRaises(GeminiAcpError):
                client.prompt(
                    session_id=str(session["sessionId"]),
                    text="exit",
                    turn_id="turn-1",
                )
            wait_for(lambda: bool(disconnects))
        finally:
            client.stop()


class GeminiAcpProviderTests(unittest.TestCase):
    def provider(self, *, environment: dict[str, str] | None = None) -> GeminiAcpProvider:
        return GeminiAcpProvider(
            workspace_root=ROOT,
            client_factory=lambda: GeminiAcpClient(
                fake_command(),
                cwd=ROOT,
                environment=environment,
                request_timeout=2,
            ),
        )

    def test_missing_runtime_is_exposed_without_starting_a_process(self) -> None:
        provider = GeminiAcpProvider(
            workspace_root=ROOT,
            runtime_detector=lambda _root: GeminiRuntimeInfo(
                available=False,
                error="Gemini CLIが見つかりません",
            ),
        )
        state = provider.connect()
        self.assertEqual(state.availability, "error")
        self.assertEqual(state.auth_state, "error")
        self.assertIn("見つかりません", state.error)

    def test_initialize_auth_state_and_missing_model_capability_are_explicit(self) -> None:
        provider = self.provider(environment={"FAKE_ACP_AUTH_STATE": "unauthenticated"})
        try:
            state = provider.connect()
            self.assertEqual(state.auth_state, "unauthenticated")
            self.assertFalse(state.model_selection_supported)
            self.assertEqual(state.models, ())
            self.assertTrue(state.login_available)
        finally:
            provider.close()

    def test_connect_restores_saved_preferred_model_from_initial_inventory(self) -> None:
        class InitialModelClient:
            notification_callback = None
            disconnect_callback = None

            def start(self) -> dict[str, object]:
                return {
                    "protocolVersion": 1,
                    "authState": "authenticated",
                    "models": {
                        "availableModels": [
                            {"modelId": "gemini-default", "name": "Default"},
                            {"modelId": "gemini-saved", "name": "Saved"},
                        ],
                        "currentModelId": "gemini-default",
                    },
                }

            def stop(self) -> None:
                return None

        provider = GeminiAcpProvider(
            workspace_root=ROOT,
            client_factory=InitialModelClient,
            preferred_model="gemini-saved",
        )
        try:
            state = provider.connect()
            self.assertEqual(state.selected_model, "gemini-saved")
            self.assertEqual(provider.state.selected_model, "gemini-saved")
        finally:
            provider.close()

    def test_initialize_session_dynamic_models_stream_and_cancel(self) -> None:
        provider = self.provider(
            environment={
                "FAKE_ACP_MODELS": '[{"modelId":"dynamic-model","name":"Dynamic Model"}]',
            }
        )
        events: list[AIProviderEvent] = []
        provider.subscribe(events.append)
        try:
            state = provider.connect()
            self.assertEqual(state.availability, "available")
            self.assertEqual(state.auth_state, "unknown")
            session = provider.new_session(
                model_id="",
                workspace_root=str(ROOT),
            )
            self.assertEqual(session.session_id, "session-1")
            self.assertTrue(provider.state.model_selection_supported)
            self.assertEqual(provider.state.models[0].model_id, "dynamic-model")
            result = provider.send_prompt(
                AIProviderPrompt(
                    session=session,
                    prompt="hello",
                    model_id="",
                    workspace_root=str(ROOT),
                )
            )
            self.assertTrue(result.turn_id)
            stream = [
                event
                for event in events
                if event.kind in {"turn_started", "text_delta", "turn_completed"}
            ]
            self.assertEqual(
                [event.kind for event in stream],
                ["turn_started", "text_delta", "text_delta", "turn_completed"],
            )
            self.assertEqual("".join(event.text for event in stream if event.kind == "text_delta"), "hello world")
            provider.cancel_active_turn(session_id=session.session_id, turn_id=result.turn_id)
        finally:
            provider.close()

    def test_sequential_sessions_keep_stream_events_scoped(self) -> None:
        provider = self.provider()
        events: list[AIProviderEvent] = []
        provider.subscribe(events.append)
        try:
            provider.connect()
            first = provider.new_session(model_id="", workspace_root=str(ROOT))
            second = provider.new_session(model_id="", workspace_root=str(ROOT))
            provider.send_prompt(
                AIProviderPrompt(first, "first", "", str(ROOT))
            )
            provider.send_prompt(
                AIProviderPrompt(second, "second", "", str(ROOT))
            )
            text_events = [event for event in events if event.kind == "text_delta"]
            self.assertEqual({event.session_id for event in text_events}, {"session-1", "session-2"})
            self.assertTrue(all(event.turn_id for event in text_events))
        finally:
            provider.close()

    def test_auth_error_transitions_to_unauthenticated(self) -> None:
        provider = self.provider(environment={"FAKE_ACP_AUTH_ERROR": "1"})
        try:
            provider.connect()
            with self.assertRaises(RuntimeError):
                provider.new_session(model_id="", workspace_root=str(ROOT))
            self.assertEqual(provider.state.auth_state, "unauthenticated")
        finally:
            provider.close()

    def test_cancel_sends_acp_cancel_and_maps_stop_reason(self) -> None:
        provider = self.provider(environment={"FAKE_ACP_CANCEL": "1"})
        events: list[AIProviderEvent] = []
        provider.subscribe(events.append)
        provider.connect()
        session = provider.new_session(model_id="", workspace_root=str(ROOT))
        result_holder: list[object] = []

        def send() -> None:
            try:
                result_holder.append(
                    provider.send_prompt(AIProviderPrompt(session, "hello", "", str(ROOT)))
                )
            except Exception as error:  # pragma: no cover - assertion below reports unexpected errors
                result_holder.append(error)

        worker = threading.Thread(target=send)
        worker.start()
        wait_for(lambda: any(event.kind == "text_delta" for event in events))
        turn = next(event.turn_id for event in events if event.kind == "turn_started")
        provider.cancel_active_turn(session_id=session.session_id, turn_id=turn)
        worker.join(timeout=3)
        try:
            self.assertFalse(worker.is_alive())
            self.assertFalse(any(isinstance(item, Exception) for item in result_holder))
            self.assertTrue(any(event.kind == "turn_completed" and event.status == "interrupted" for event in events))
        finally:
            provider.close()

    def test_connect_failure_is_redacted_and_does_not_log_credentials(self) -> None:
        class BrokenClient:
            notification_callback = None
            disconnect_callback = None

            def start(self):
                raise OSError('api_key="secret-value" /home/user/private.json')

            def stop(self):
                return None

        provider = GeminiAcpProvider(client_factory=BrokenClient)
        state = provider.connect()
        self.assertEqual(state.availability, "error")
        self.assertNotIn("secret-value", state.error)
        self.assertNotIn("/home/user", state.error)


if __name__ == "__main__":
    unittest.main()
