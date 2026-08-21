from __future__ import annotations

import time
import unittest

from src.gui_codex_chat_state import CodexChatController


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
        self.thread_params = dict(params or {})
        return {"thread": {"id": "thread-1"}}

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

    def test_send_streams_response_and_uses_selected_model(self) -> None:
        client = FakeChatClient()
        client.authenticated = True
        controller = CodexChatController(
            client_factory=lambda: client,
            preferred_model="gpt-fast",
        )
        try:
            controller.connect()
            wait_for(lambda: controller.snapshot.auth_state == "authenticated")
            controller.send_message("こんにちは")
            wait_for(lambda: controller.snapshot.chat_state == "idle" and len(controller.snapshot.messages) == 2)

            self.assertEqual(client.thread_params["model"], "gpt-fast")
            self.assertEqual(client.thread_params["approvalPolicy"], "never")
            self.assertEqual(client.thread_params["sandbox"], "readOnly")
            self.assertEqual(client.turn_params["model"], "gpt-fast")
            self.assertEqual(client.turn_params["prompt"], "こんにちは")
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


if __name__ == "__main__":
    unittest.main()
