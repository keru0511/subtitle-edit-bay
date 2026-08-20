from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from src.codex_app_server_client import (
    CodexAppServerClient,
    CodexRequestTimeout,
)


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
            self.assertTrue(client.account_login_cancel("login-1")["cancelled"])
            thread = client.thread_start({"cwd": "<safe-context>"})
            self.assertEqual(thread["threadId"], "thread-1")
            self.assertEqual(client.thread_resume("thread-1")["threadId"], "thread-1")
            turn = client.turn_start(
                thread_id="thread-1",
                prompt="字幕を簡潔にする",
                output_schema={"type": "object"},
            )
            self.assertEqual(turn["status"], "completed")
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

    def test_start_failure_is_reported_without_opening_a_socket(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = CodexAppServerClient(
                [str(Path(temp_dir) / "missing-codex")],
                cwd=temp_dir,
            )
            with self.assertRaisesRegex(RuntimeError, "could not start"):
                client.start()


if __name__ == "__main__":
    unittest.main()
