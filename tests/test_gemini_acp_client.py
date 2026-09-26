"""Gemini ACP 通信クライアントの分離後の公開経路を確認する。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from src import gemini_acp_client, gemini_acp_provider
from tests.typed_case import TypedTestCase


ROOT = Path(__file__).resolve().parent.parent
FAKE_SERVER = Path(__file__).resolve().parent / "fake_gemini_acp_server.py"


class GeminiAcpClientModuleTests(TypedTestCase):
    def test_legacy_provider_exports_reference_the_client_module(self) -> None:
        exports: tuple[tuple[str, object, object], ...] = (
            ("ACP_PROTOCOL_VERSION", gemini_acp_provider.ACP_PROTOCOL_VERSION, gemini_acp_client.ACP_PROTOCOL_VERSION),
            (
                "DEFAULT_GEMINI_CLIENT_NAME",
                gemini_acp_provider.DEFAULT_GEMINI_CLIENT_NAME,
                gemini_acp_client.DEFAULT_GEMINI_CLIENT_NAME,
            ),
            ("GeminiAcpClient", gemini_acp_provider.GeminiAcpClient, gemini_acp_client.GeminiAcpClient),
            (
                "GeminiAcpClientProtocol",
                gemini_acp_provider.GeminiAcpClientProtocol,
                gemini_acp_client.GeminiAcpClientProtocol,
            ),
            ("GeminiAcpError", gemini_acp_provider.GeminiAcpError, gemini_acp_client.GeminiAcpError),
            (
                "GeminiAcpNotification",
                gemini_acp_provider.GeminiAcpNotification,
                gemini_acp_client.GeminiAcpNotification,
            ),
            (
                "GeminiAcpRequestTimeout",
                gemini_acp_provider.GeminiAcpRequestTimeout,
                gemini_acp_client.GeminiAcpRequestTimeout,
            ),
            ("GeminiAcpRpcError", gemini_acp_provider.GeminiAcpRpcError, gemini_acp_client.GeminiAcpRpcError),
        )
        for name, provided, expected in exports:
            with self.subTest(name=name):
                self.assertIs(provided, expected)

    def test_direct_client_import_completes_acp_turn(self) -> None:
        notifications: list[gemini_acp_client.GeminiAcpNotification] = []
        client = gemini_acp_client.GeminiAcpClient(
            [sys.executable, "-u", str(FAKE_SERVER), "--acp"],
            cwd=ROOT,
            request_timeout=2,
            notification_callback=notifications.append,
        )
        try:
            self.assertEqual(client.start()["protocolVersion"], gemini_acp_client.ACP_PROTOCOL_VERSION)
            session = client.new_session(cwd=ROOT)
            response = client.prompt(
                session_id=str(session["sessionId"]),
                text="hello",
                turn_id="turn-1",
            )
            self.assertEqual(response["stopReason"], "end_turn")
            self.assertEqual(
                [event.turn_id for event in notifications if event.method == "session/update"],
                ["turn-1", "turn-1"],
            )
        finally:
            client.stop()


if __name__ == "__main__":
    unittest.main()
