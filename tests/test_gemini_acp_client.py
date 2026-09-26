"""Gemini ACP 通信クライアントの分離後の公開経路を確認する。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from src import gemini_acp_client, gemini_acp_provider


ROOT = Path(__file__).resolve().parent.parent
FAKE_SERVER = Path(__file__).resolve().parent / "fake_gemini_acp_server.py"


class GeminiAcpClientModuleTests(unittest.TestCase):
    def test_legacy_provider_exports_reference_the_client_module(self) -> None:
        for name in (
            "ACP_PROTOCOL_VERSION",
            "DEFAULT_GEMINI_CLIENT_NAME",
            "GeminiAcpClient",
            "GeminiAcpClientProtocol",
            "GeminiAcpError",
            "GeminiAcpNotification",
            "GeminiAcpRequestTimeout",
            "GeminiAcpRpcError",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(gemini_acp_provider, name),
                    getattr(gemini_acp_client, name),
                )

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
