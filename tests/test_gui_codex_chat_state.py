from __future__ import annotations

import subprocess
import sys
import threading
import time
import unittest
from collections.abc import Callable
from pathlib import Path

from openai_codex.generated import v2_all as v2
from openai_codex.models import JsonObject

from src.gui_codex_chat_state import CodexChatController
from tests.codex_fixtures import FakeSDKClient, params_object, sdk_model
from tests.codex_fixtures import notification as CodexNotification
from tests.typed_case import TypedTestCase


class FakeChatClient(FakeSDKClient):
    def __init__(self) -> None:
        super().__init__(authenticated=False)


class BlockingTurnStartClient(FakeChatClient):
    def __init__(self) -> None:
        super().__init__()
        self.turn_entered = threading.Event()
        self.turn_release = threading.Event()

    def turn_start(
        self,
        thread_id: str,
        input_items: list[JsonObject] | JsonObject | str,
        params: v2.TurnStartParams | JsonObject | None = None,
    ) -> v2.TurnStartResponse:
        values = params_object(params)
        self.turn_params = {
            "thread_id": thread_id,
            "prompt": input_items,
            "model": values.get("model"),
            "cwd": values.get("cwd"),
            "approval_policy": values.get("approvalPolicy"),
            "sandbox_policy": values.get("sandboxPolicy"),
        }
        self.turn_entered.set()
        self.turn_release.wait(2)
        return sdk_model(v2.TurnStartResponse, {"turn": {"id": "turn-blocked", "items": [], "status": "inProgress"}})


class FailingStartClient(FakeChatClient):
    def start(self) -> None:
        raise OSError("接続に失敗しました")


def wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        time.sleep(0.01)
    if not predicate():
        raise AssertionError("condition was not reached")


class CodexChatControllerTests(TypedTestCase):
    def test_chat_state_imports_with_installed_sdk_dependencies(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import src.gui_codex_chat_state",
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_connection_error_clears_provider_and_normal_connect_retries(self) -> None:
        first_client = FailingStartClient()
        second_client = FakeChatClient()
        second_client.authenticated = True
        clients = iter((first_client, second_client))
        controller = CodexChatController(
            client_factory=lambda: next(clients),
            workspace_root=Path.cwd(),
        )
        try:
            controller.connect()
            wait_for(
                lambda: (
                    controller.snapshot.connection_state == "error" and controller.snapshot.chat_state == "disconnected"
                )
            )
            self.assertIsNone(controller._provider)

            controller.connect()
            wait_for(lambda: controller.snapshot.auth_state == "authenticated")
            self.assertEqual(controller.snapshot.connection_state, "ready")
            self.assertTrue(second_client.started)
        finally:
            controller.shutdown()

    def test_subtitle_proposal_is_recorded_in_the_shared_conversation(self) -> None:
        client = FakeChatClient()
        client.authenticated = True
        controller = CodexChatController(
            client_factory=lambda: client,
            workspace_root=Path.cwd(),
        )
        try:
            controller.connect()
            wait_for(lambda: controller.snapshot.auth_state == "authenticated")
            self.assertTrue(controller.begin_proposal("字幕を短くして"))
            self.assertEqual(controller.snapshot.chat_state, "sending")
            self.assertEqual(controller.snapshot.messages[-1]["content_type"], "subtitle_proposal")

            controller.complete_proposal("1件の変更案です")
            self.assertEqual(controller.snapshot.chat_state, "idle")
            self.assertEqual(controller.snapshot.messages[-1]["text"], "1件の変更案です")
            self.assertEqual(controller.snapshot.messages[-1]["status"], "completed")
            self.assertEqual(client.turn_params, {})
        finally:
            controller.shutdown()

    def test_login_states_models_and_model_persistence(self) -> None:
        client = FakeChatClient()
        selected_models: list[str] = []
        controller = CodexChatController(
            client_factory=lambda: client,
            workspace_root=Path.cwd(),
            preferred_model="removed-model",
            on_selected_model=selected_models.append,
        )
        try:
            controller.connect()
            wait_for(lambda: controller.snapshot.auth_state == "unauthenticated")
            self.assertEqual(controller.snapshot.connection_state, "ready")

            controller.login()
            wait_for(lambda: controller.snapshot.auth_state == "login_pending")
            self.assertEqual(controller.snapshot.login_url, "https://example.invalid/login")
            self.assertEqual(client.login_kwargs["login_type"], "chatgpt")

            client.complete_login()
            wait_for(lambda: controller.snapshot.auth_state == "authenticated")
            self.assertEqual(controller.snapshot.auth_label, "ChatGPT · plus")
            self.assertEqual(controller.snapshot.selected_model, "gpt-default")
            self.assertIn("removed-model", controller.snapshot.model_error)
            self.assertEqual([item["id"] for item in controller.snapshot.models], ["gpt-default", "gpt-fast"])

            controller.select_model("gpt-fast")
            self.assertEqual(controller.snapshot.selected_model, "gpt-fast")
            self.assertEqual(selected_models[-1], "gpt-fast")

            controller.select_model("not-available")
            self.assertEqual(controller.snapshot.selected_model, "gpt-fast")
            self.assertIn("利用できません", controller.snapshot.model_error)
        finally:
            controller.shutdown()

    def test_send_payload_matches_pinned_codex_app_server_v2_contract(self) -> None:
        client = FakeChatClient()
        client.authenticated = True
        controller = CodexChatController(
            client_factory=lambda: client,
            workspace_root=Path.cwd(),
            preferred_model="gpt-fast",
        )
        try:
            controller.connect()
            wait_for(lambda: controller.snapshot.auth_state == "authenticated")
            controller.send_message("こんにちは")
            wait_for(lambda: controller.snapshot.chat_state == "idle" and len(controller.snapshot.messages) == 2)

            self.assertEqual(client.thread_params["model"], "gpt-fast")
            self.assertEqual(client.thread_params["approvalPolicy"], "never")
            self.assertEqual(client.thread_params["sandbox"], "read-only")
            self.assertEqual(client.thread_params["cwd"], str(Path.cwd().resolve()))
            self.assertEqual(client.turn_params["model"], "gpt-fast")
            self.assertEqual(client.turn_params["prompt"], "こんにちは")
            self.assertEqual(client.turn_params["approval_policy"], "never")
            self.assertEqual(client.turn_params["cwd"], str(Path.cwd().resolve()))
            self.assertEqual(
                client.turn_params["sandbox_policy"],
                {
                    "type": "readOnly",
                    "networkAccess": False,
                },
            )
            self.assertEqual(controller.snapshot.messages[0]["role"], "user")
            self.assertEqual(controller.snapshot.messages[1]["text"], "返答")
            self.assertEqual(controller.snapshot.messages[1]["status"], "completed")
            self.assertEqual(controller.snapshot.thread_id, "thread-1")
            self.assertEqual(controller.snapshot.turn_id, "")
        finally:
            controller.shutdown()

    def test_disconnect_and_logout_are_visible_states(self) -> None:
        client = FakeChatClient()
        client.authenticated = True
        controller = CodexChatController(
            client_factory=lambda: client,
            workspace_root=Path.cwd(),
            preferred_model="gpt-default",
        )
        try:
            controller.connect()
            wait_for(lambda: controller.snapshot.auth_state == "authenticated")
            controller.logout()
            wait_for(lambda: controller.snapshot.auth_state == "unauthenticated")
            self.assertEqual(controller.snapshot.messages, ())

            client.disconnect()
            wait_for(lambda: controller.snapshot.connection_state == "disconnected")
            self.assertEqual(controller.snapshot.connection_state, "disconnected")
            self.assertEqual(controller.snapshot.chat_state, "disconnected")
            self.assertIn("再接続", controller.snapshot.error)
        finally:
            controller.shutdown()

    def test_relogin_clears_the_previous_accounts_thread_and_messages(self) -> None:
        client = FakeChatClient()
        client.authenticated = True
        controller = CodexChatController(
            client_factory=lambda: client,
            workspace_root=Path.cwd(),
            preferred_model="gpt-default",
        )
        try:
            controller.connect()
            wait_for(lambda: controller.snapshot.auth_state == "authenticated")
            controller.send_message("Account Aの会話")
            wait_for(lambda: controller.snapshot.chat_state == "idle")
            self.assertEqual(controller.snapshot.thread_id, "thread-1")

            controller.login(relogin=True)
            wait_for(lambda: controller.snapshot.auth_state == "login_pending")
            self.assertEqual(controller.snapshot.thread_id, "")
            self.assertEqual(controller.snapshot.messages, ())

            client.complete_login()
            wait_for(lambda: controller.snapshot.auth_state == "authenticated")
            controller.send_message("Account Bの会話")
            wait_for(lambda: controller.snapshot.chat_state == "idle")
            self.assertEqual(controller.snapshot.thread_id, "thread-2")
            self.assertEqual(client.thread_start_count, 2)
            self.assertEqual(len(controller.snapshot.messages), 2)
        finally:
            controller.shutdown()

    def test_reconnect_resumes_thread_before_starting_the_next_turn(self) -> None:
        first_client = FakeChatClient()
        first_client.authenticated = True
        second_client = FakeChatClient()
        second_client.authenticated = True
        clients = iter((first_client, second_client))
        controller = CodexChatController(
            client_factory=lambda: next(clients),
            workspace_root=Path.cwd(),
            preferred_model="gpt-default",
        )
        try:
            controller.connect()
            wait_for(lambda: controller.snapshot.auth_state == "authenticated")
            controller.send_message("最初の送信")
            wait_for(lambda: controller.snapshot.chat_state == "idle")
            self.assertEqual(controller.snapshot.thread_id, "thread-1")

            first_client.disconnect()
            wait_for(lambda: controller.snapshot.connection_state == "disconnected")
            self.assertEqual(controller.snapshot.connection_state, "disconnected")
            controller.reconnect()
            wait_for(lambda: controller.snapshot.auth_state == "authenticated")
            controller.send_message("再接続後の送信")
            wait_for(lambda: controller.snapshot.chat_state == "idle")

            self.assertEqual(second_client.resumed_threads[0][0], "thread-1")
            self.assertEqual(
                second_client.resumed_threads[0][1],
                {
                    "model": "gpt-default",
                    "cwd": str(Path.cwd().resolve()),
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                },
            )
            self.assertNotIn(
                "serviceName",
                second_client.resumed_threads[0][1],
            )
            self.assertEqual(second_client.turn_params["thread_id"], "thread-1")
            self.assertEqual(second_client.thread_start_count, 0)
        finally:
            controller.shutdown()

    def test_stop_while_turn_start_is_pending_interrupts_after_id_arrives(self) -> None:
        client = BlockingTurnStartClient()
        client.authenticated = True
        controller = CodexChatController(
            client_factory=lambda: client,
            workspace_root=Path.cwd(),
            preferred_model="gpt-default",
        )
        try:
            controller.connect()
            wait_for(lambda: controller.snapshot.auth_state == "authenticated")
            controller.send_message("送信直後に停止")
            self.assertTrue(client.turn_entered.wait(2))
            self.assertEqual(controller.snapshot.turn_id, "")

            controller.interrupt()
            self.assertEqual(controller.snapshot.chat_state, "stopping")
            client.turn_release.set()

            wait_for(lambda: client.interrupted == ("thread-1", "turn-blocked"))
            self.assertEqual(controller.snapshot.chat_state, "stopping")
            callback = client.emit
            self.assertIsNotNone(callback)
            if callback is None:
                raise AssertionError("notification callback is not registered")
            callback(
                CodexNotification(
                    "turn/completed",
                    {"turn": {"id": "turn-blocked", "status": "interrupted"}},
                )
            )
            wait_for(lambda: controller.snapshot.chat_state == "idle")
            self.assertEqual(controller.snapshot.messages[-1]["status"], "interrupted")
        finally:
            client.turn_release.set()
            controller.shutdown()

    def test_idle_disconnect_preserves_the_completed_assistant_message(self) -> None:
        client = FakeChatClient()
        client.authenticated = True
        controller = CodexChatController(
            client_factory=lambda: client,
            workspace_root=Path.cwd(),
            preferred_model="gpt-default",
        )
        try:
            controller.connect()
            wait_for(lambda: controller.snapshot.auth_state == "authenticated")
            controller.send_message("完了後に切断")
            wait_for(lambda: controller.snapshot.chat_state == "idle")
            self.assertEqual(controller.snapshot.messages[-1]["status"], "completed")

            client.disconnect()
            wait_for(lambda: controller.snapshot.connection_state == "disconnected")

            self.assertEqual(controller.snapshot.connection_state, "disconnected")
            self.assertEqual(controller.snapshot.messages[-1]["status"], "completed")
        finally:
            controller.shutdown()


if __name__ == "__main__":
    unittest.main()
