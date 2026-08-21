from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path

from src.gui_codex_chat_state import CodexChatController


CODEX_APP_SERVER_SCHEMA_COMMIT = "3882ced09c4917b0bb528f597abd87f3c905fe47"


class FakeNotification:
    def __init__(self, method: str, params: dict[str, object]) -> None:
        self.method = method
        self.params = params


class FakeChatClient:
    def __init__(self) -> None:
        self.notification_callback = None
        self.disconnect_callback = None
        self.authenticated = False
        self.started = False
        self.thread_params: dict[str, object] = {}
        self.thread_start_count = 0
        self.resumed_threads: list[tuple[str, dict[str, object]]] = []
        self.turn_params: dict[str, object] = {}
        self.interrupted: tuple[str, str] | None = None

    def start(self) -> dict[str, object]:
        self.started = True
        return {"protocolVersion": "1"}

    def stop(self) -> None:
        self.started = False

    def account_read(self, *, refresh_token: bool = False) -> dict[str, object]:
        if self.authenticated:
            return {
                "account": {"type": "chatgpt", "planType": "plus"},
                "requiresOpenaiAuth": True,
            }
        return {"account": None, "requiresOpenaiAuth": True}

    def account_login_start(self, **kwargs) -> dict[str, object]:
        self.login_kwargs = kwargs
        return {
            "type": "chatgpt",
            "loginId": "login-1",
            "authUrl": "https://example.invalid/login",
        }

    def account_logout(self) -> dict[str, object]:
        self.authenticated = False
        return {}

    def model_list(self, **kwargs) -> dict[str, object]:
        return {
            "data": [
                {
                    "id": "gpt-default",
                    "displayName": "Default",
                    "isDefault": True,
                    "hidden": False,
                },
                {
                    "id": "gpt-fast",
                    "displayName": "Fast",
                    "isDefault": False,
                    "hidden": False,
                },
                {"id": "hidden", "displayName": "Hidden", "hidden": True},
            ]
        }

    def thread_start(self, params=None) -> dict[str, object]:
        self.thread_start_count += 1
        self.thread_params = dict(params or {})
        return {"thread": {"id": f"thread-{self.thread_start_count}"}}

    def thread_resume(
        self,
        thread_id: str,
        *,
        model: str | None = None,
        cwd: str | Path | None = None,
        approval_policy: str | None = None,
        sandbox: str | None = None,
    ) -> dict[str, object]:
        params = {}
        if model:
            params["model"] = model
        if cwd is not None:
            params["cwd"] = str(cwd)
        if approval_policy:
            params["approvalPolicy"] = approval_policy
        if sandbox:
            params["sandbox"] = sandbox
        self.resumed_threads.append((thread_id, params))
        return {"thread": {"id": thread_id}}

    def turn_start(self, **kwargs) -> dict[str, object]:
        self.turn_params = dict(kwargs)
        if self.notification_callback:
            self.notification_callback(
                FakeNotification("turn/started", {"turn": {"id": "turn-1"}})
            )
            self.notification_callback(
                FakeNotification("item/agentMessage/delta", {"delta": "返"})
            )
            self.notification_callback(
                FakeNotification("item/agentMessage/delta", {"delta": "答"})
            )
            self.notification_callback(
                FakeNotification(
                    "item/completed",
                    {"item": {"type": "agentMessage", "id": "item-1", "text": "返答"}},
                )
            )
            self.notification_callback(
                FakeNotification(
                    "turn/completed",
                    {"turn": {"id": "turn-1", "status": "completed"}},
                )
            )
        return {"turn": {"id": "turn-1", "status": "inProgress"}}

    def turn_interrupt(self, turn_id: str, *, thread_id: str = "") -> dict[str, object]:
        self.interrupted = (thread_id, turn_id)
        return {}

    def complete_login(self) -> None:
        self.authenticated = True
        if self.notification_callback:
            self.notification_callback(
                FakeNotification(
                    "account/login/completed",
                    {"loginId": "login-1", "success": True, "error": None},
                )
            )

    def disconnect(self) -> None:
        if self.disconnect_callback:
            self.disconnect_callback(RuntimeError("closed"))


class BlockingTurnStartClient(FakeChatClient):
    def __init__(self) -> None:
        super().__init__()
        self.turn_entered = threading.Event()
        self.turn_release = threading.Event()

    def turn_start(self, **kwargs) -> dict[str, object]:
        self.turn_params = dict(kwargs)
        self.turn_entered.set()
        self.turn_release.wait(2)
        return {"turn": {"id": "turn-blocked", "status": "inProgress"}}


def wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        time.sleep(0.01)
    if not predicate():
        raise AssertionError("condition was not reached")


class CodexChatControllerTests(unittest.TestCase):
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
            self.assertIn(
                client.thread_params["sandbox"],
                {"read-only", "workspace-write", "danger-full-access"},
                CODEX_APP_SERVER_SCHEMA_COMMIT,
            )
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
                CODEX_APP_SERVER_SCHEMA_COMMIT,
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
                CODEX_APP_SERVER_SCHEMA_COMMIT,
            )
            self.assertNotIn(
                "serviceName",
                second_client.resumed_threads[0][1],
                CODEX_APP_SERVER_SCHEMA_COMMIT,
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
            client.notification_callback(
                FakeNotification(
                    "turn/completed",
                    {"turn": {"id": "turn-blocked", "status": "interrupted"}},
                )
            )
            wait_for(lambda: controller.snapshot.chat_state == "idle")
            self.assertEqual(controller.snapshot.messages[-1]["status"], "interrupted")
        finally:
            client.turn_release.set()
            controller.shutdown()


if __name__ == "__main__":
    unittest.main()
