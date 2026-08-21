from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.codex_app_server_client import (
    CodexAppServerClient,
    CodexRpcError,
    CodexRequestTimeout,
    _redact_log,
    _redact_payload,
)


CODEX_APP_SERVER_SCHEMA_COMMIT = "3882ced09c4917b0bb528f597abd87f3c905fe47"


class CodexAppServerClientTests(unittest.TestCase):
    def _client(self, notifications: list[object], logs: list[str]) -> CodexAppServerClient:
        fake_server = Path(__file__).with_name("fake_codex_app_server.py")
        return CodexAppServerClient(
            [sys.executable, str(fake_server)],
            notification_callback=notifications.append,
            log_callback=logs.append,
            request_timeout=1.0,
        )

    def test_handshake_auth_thread_turn_and_partial_json_are_supported(self) -> None:
        notifications: list[object] = []
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
            self.assertEqual(client.model_list()["data"][0]["id"], "gpt-test")
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

    def test_unsupported_server_request_is_rejected_instead_of_hanging(self) -> None:
        client = CodexAppServerClient(["codex"])
        with patch.object(client, "_send") as send:
            client._handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 901,
                    "method": "item/tool/requestUserInput",
                    "params": {"questions": []},
                }
            )

        response = send.call_args.args[0]
        self.assertEqual(response["id"], 901)
        self.assertEqual(response["error"]["code"], -32601)
        self.assertIn("not supported", response["error"]["message"])
        self.assertTrue(
            any(item.method == "item/tool/requestUserInput" for item in client.notifications)
        )

    def test_turn_start_payload_matches_pinned_v2_sandbox_contract(self) -> None:
        client = CodexAppServerClient(["codex"])
        sandbox_policy = {"type": "readOnly", "networkAccess": False}
        with patch.object(client, "request", return_value={}) as request:
            client.turn_start(
                thread_id="thread-1",
                prompt="contract check",
                cwd="C:/workspace",
                approval_policy="never",
                sandbox_policy=sandbox_policy,
                context={"segment": "字幕"},
            )

        method, params = request.call_args.args
        self.assertEqual(method, "turn/start")
        self.assertNotIn("context", params, CODEX_APP_SERVER_SCHEMA_COMMIT)
        self.assertEqual(
            set(params),
            {"threadId", "input", "cwd", "approvalPolicy", "sandboxPolicy"},
            CODEX_APP_SERVER_SCHEMA_COMMIT,
        )
        self.assertEqual(params["input"][0], {"type": "text", "text": "contract check"})
        self.assertEqual(params["input"][1]["type"], "text")
        self.assertIn('"segment":"字幕"', params["input"][1]["text"])
        self.assertEqual(
            params["sandboxPolicy"],
            sandbox_policy,
            CODEX_APP_SERVER_SCHEMA_COMMIT,
        )
        self.assertEqual(
            set(params["sandboxPolicy"]),
            {"type", "networkAccess"},
            CODEX_APP_SERVER_SCHEMA_COMMIT,
        )

    def test_thread_resume_payload_matches_pinned_v2_contract(self) -> None:
        client = CodexAppServerClient(["codex"])
        with patch.object(client, "request", return_value={}) as request:
            client.thread_resume(
                "thread-1",
                model="gpt-test",
                cwd="C:/workspace",
                approval_policy="never",
                sandbox="read-only",
            )

        method, params = request.call_args.args
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
            'Bearer bearer-secret {"access_token":"json-secret"} '
            "https://example.test/callback?token=url-secret&next=ok"
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
        self.assertEqual(payload["access_token"], "[REDACTED]")
        self.assertEqual(payload["context"]["password"], "[REDACTED]")
        self.assertEqual(payload["items"][0]["authorization"], "[REDACTED]")
        self.assertNotIn("nested-secret", repr(payload))

        error = CodexRpcError(400, "token=message-secret", {"token": "data-secret"})
        self.assertNotIn("message-secret", str(error))
        self.assertEqual(error.data["token"], "[REDACTED]")


if __name__ == "__main__":
    unittest.main()
