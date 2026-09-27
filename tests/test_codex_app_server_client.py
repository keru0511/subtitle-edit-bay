from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Mapping

from src.codex_app_server_client import (
    MAX_RETAINED_NOTIFICATIONS,
    CodexAppServerClient,
    CodexNotification,
    CodexRpcError,
    CodexRequestTimeout,
    _redact_log,
    _redact_payload,
)
from src.data_boundary import is_object_list, is_object_mapping
from tests.typed_case import TypedTestCase


CODEX_APP_SERVER_SCHEMA_COMMIT = "3882ced09c4917b0bb528f597abd87f3c905fe47"


class RecordingClient(CodexAppServerClient):
    def __init__(self) -> None:
        super().__init__(["codex"])
        self.sent: list[Mapping[str, object]] = []
        self.requests: list[tuple[str, Mapping[str, object]]] = []

    def _send(self, message: Mapping[str, object]) -> None:
        self.sent.append(message)

    def request(
        self, method: str, params: Mapping[str, object] | None = None, *, timeout: float | None = None
    ) -> dict[str, object]:
        self.requests.append((method, dict(params or {})))
        return {}


class CodexAppServerClientTests(TypedTestCase):
    def _client(self, notifications: list[CodexNotification], logs: list[str]) -> CodexAppServerClient:
        fake_server = Path(__file__).with_name("fake_codex_app_server.py")
        return CodexAppServerClient(
            [sys.executable, str(fake_server)],
            notification_callback=notifications.append,
            log_callback=logs.append,
            request_timeout=1.0,
        )

    def test_handshake_auth_thread_turn_and_partial_json_are_supported(self) -> None:
        notifications: list[CodexNotification] = []
        logs: list[str] = []
        client = self._client(notifications, logs)
        try:
            initialized = client.start()
            self.assertEqual(initialized["protocolVersion"], "1")
            self.assertTrue(client.initialized)
            self.assertFalse(client.account_read()["authenticated"])
            login = client.account_login_start()
            self.assertEqual(login["loginId"], "login-1")
            self.assertEqual(login["receivedType"], "chatgpt")
            self.assertTrue(client.account_login_cancel("login-1")["cancelled"])
            models = client.model_list()["data"]
            assert is_object_list(models)
            first_model = models[0]
            assert is_object_mapping(first_model)
            self.assertEqual(first_model["id"], "gpt-test")
            thread = client.thread_start({"cwd": "<safe-context>"})
            self.assertEqual(thread["threadId"], "thread-1")
            self.assertEqual(client.thread_resume("thread-1")["threadId"], "thread-1")
            turn = client.turn_start(
                thread_id="thread-1",
                prompt="字幕を簡潔にする",
                output_schema={"type": "object"},
                model="gpt-test",
            )
            self.assertEqual(turn["status"], "completed")
            self.assertEqual(turn["receivedInput"], [{"type": "text", "text": "字幕を簡潔にする"}])
            self.assertEqual(turn["receivedModel"], "gpt-test")
            self.assertEqual(client.account_logout(), {})
            self.assertTrue(any(item.method == "item/agentMessage/delta" for item in notifications))
            self.assertNotIn("字幕を簡潔にする", " ".join(logs))
        finally:
            client.stop()

    def test_timeout_does_not_stop_client_and_restart_rehandshakes(self) -> None:
        client = self._client([], [])
        try:
            client.start()
            with self.assertRaises(CodexRequestTimeout):
                client.request("test/timeout", timeout=0.1)
            self.assertTrue(client.is_running)
            client.restart()
            self.assertTrue(client.initialized)
        finally:
            client.stop()

    def test_approval_request_is_rejected_without_auto_approval(self) -> None:
        client = self._client([], [])
        try:
            client.start()
            result = client.request("test/approval")
            self.assertTrue(result["approvalRequestSent"])
            self.assertTrue(any(item.method == "command/approval/request" for item in client.notifications))
        finally:
            client.stop()

    def test_unexpected_exit_releases_process_and_notifies_disconnect(self) -> None:
        disconnected = threading.Event()
        client = self._client([], [])
        client.disconnect_callback = lambda _error: disconnected.set()
        try:
            client.start()
            with self.assertRaisesRegex(RuntimeError, "exited unexpectedly"):
                client.request("test/exit")
            self.assertTrue(disconnected.wait(1))
            self.assertFalse(client.is_running)
            self.assertIsNone(client._process)
            self.assertIsNone(client._reader_thread)
        finally:
            client.stop()

    def test_unsupported_server_request_is_rejected_instead_of_hanging(self) -> None:
        client = RecordingClient()
        client._handle_message(
            {
                "jsonrpc": "2.0",
                "id": 901,
                "method": "item/tool/requestUserInput",
                "params": {"questions": []},
            }
        )

        response = client.sent[0]
        self.assertEqual(response["id"], 901)
        error = response["error"]
        assert is_object_mapping(error)
        self.assertEqual(error["code"], -32601)
        message = error["message"]
        assert isinstance(message, str)
        self.assertIn("not supported", message)
        self.assertTrue(any(item.method == "item/tool/requestUserInput" for item in client.notifications))

    def test_retained_notifications_are_bounded_for_persistent_clients(self) -> None:
        client = CodexAppServerClient(["codex"])
        for index in range(MAX_RETAINED_NOTIFICATIONS + 5):
            client._handle_message(
                {
                    "jsonrpc": "2.0",
                    "method": f"test/notification/{index}",
                    "params": {},
                }
            )

        notifications = client.notifications
        self.assertEqual(len(notifications), MAX_RETAINED_NOTIFICATIONS)
        self.assertEqual(notifications[0].method, "test/notification/5")

    def test_turn_start_payload_matches_pinned_v2_sandbox_contract(self) -> None:
        client = RecordingClient()
        sandbox_policy = {"type": "readOnly", "networkAccess": False}
        client.turn_start(
            thread_id="thread-1",
            prompt="contract check",
            cwd="C:/workspace",
            approval_policy="never",
            sandbox_policy=sandbox_policy,
            context={"segment": "字幕"},
        )

        method, params = client.requests[0]
        self.assertEqual(method, "turn/start")
        self.assertNotIn("context", params, CODEX_APP_SERVER_SCHEMA_COMMIT)
        self.assertEqual(
            set(params),
            {"threadId", "input", "cwd", "approvalPolicy", "sandboxPolicy"},
            CODEX_APP_SERVER_SCHEMA_COMMIT,
        )
        inputs = params["input"]
        assert is_object_list(inputs)
        first_input, second_input = inputs
        assert is_object_mapping(first_input)
        assert is_object_mapping(second_input)
        self.assertEqual(first_input, {"type": "text", "text": "contract check"})
        self.assertEqual(second_input["type"], "text")
        text = second_input["text"]
        assert isinstance(text, str)
        self.assertIn('"segment":"字幕"', text)
        self.assertEqual(
            params["sandboxPolicy"],
            sandbox_policy,
            CODEX_APP_SERVER_SCHEMA_COMMIT,
        )
        policy = params["sandboxPolicy"]
        assert is_object_mapping(policy)
        self.assertEqual(set(policy), {"type", "networkAccess"}, CODEX_APP_SERVER_SCHEMA_COMMIT)

    def test_thread_resume_payload_matches_pinned_v2_contract(self) -> None:
        client = RecordingClient()
        client.thread_resume(
            "thread-1",
            model="gpt-test",
            cwd="C:/workspace",
            approval_policy="never",
            sandbox="read-only",
        )

        method, params = client.requests[0]
        self.assertEqual(method, "thread/resume")
        self.assertEqual(
            params,
            {
                "threadId": "thread-1",
                "model": "gpt-test",
                "cwd": "C:/workspace",
                "approvalPolicy": "never",
                "sandbox": "read-only",
            },
            CODEX_APP_SERVER_SCHEMA_COMMIT,
        )
        self.assertNotIn("serviceName", params, CODEX_APP_SERVER_SCHEMA_COMMIT)

    def test_turn_interrupt_payload_matches_pinned_v2_contract(self) -> None:
        client = RecordingClient()
        client.turn_interrupt("turn-1", thread_id="thread-1")

        method, params = client.requests[0]
        self.assertEqual(method, "turn/interrupt")
        self.assertEqual(
            params,
            {"threadId": "thread-1", "turnId": "turn-1"},
            CODEX_APP_SERVER_SCHEMA_COMMIT,
        )

    def test_start_failure_is_reported_without_opening_a_socket(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = CodexAppServerClient(
                [str(Path(temp_dir) / "missing-codex")],
                cwd=temp_dir,
            )
            with self.assertRaisesRegex(RuntimeError, "could not start"):
                client.start()

    def test_redacts_bearer_json_url_and_structured_rpc_credentials(self) -> None:
        value = (
            'Bearer bearer-secret {"access_token":"json-secret"} https://example.test/callback?token=url-secret&next=ok'
        )

        redacted = _redact_log(value)
        self.assertNotIn("bearer-secret", redacted)
        self.assertNotIn("json-secret", redacted)
        self.assertNotIn("url-secret", redacted)

        payload = _redact_payload(
            {
                "access_token": "nested-secret",
                "context": {"password": "nested-password"},
                "items": [{"authorization": "nested-bearer"}],
            }
        )
        assert is_object_mapping(payload)
        self.assertEqual(payload["access_token"], "[REDACTED]")
        context = payload["context"]
        assert is_object_mapping(context)
        self.assertEqual(context["password"], "[REDACTED]")
        items = payload["items"]
        assert is_object_list(items)
        first_item = items[0]
        assert is_object_mapping(first_item)
        self.assertEqual(first_item["authorization"], "[REDACTED]")
        self.assertNotIn("nested-secret", repr(payload))

        error = CodexRpcError(400, "token=message-secret", {"token": "data-secret"})
        self.assertNotIn("message-secret", str(error))
        assert is_object_mapping(error.data)
        self.assertEqual(error.data["token"], "[REDACTED]")

        extended = _redact_log('Authorization: Basic Zm9vOmJhcg==\npassword="two words"')
        self.assertNotIn("Zm9vOmJhcg==", extended)
        self.assertNotIn("two words", extended)


if __name__ == "__main__":
    unittest.main()
