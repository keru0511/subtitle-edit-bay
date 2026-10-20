"""Typed SDK doubles for controller tests; transport is tested with the real SDK."""

from __future__ import annotations

import queue
from collections.abc import Callable, Mapping
from typing import TypeVar, cast

from openai_codex.client import CodexClient
from openai_codex.errors import TransportClosedError
from openai_codex.generated import v2_all as v2
from openai_codex.generated.notification_registry import NOTIFICATION_MODELS
from openai_codex.models import InitializeResponse, JsonObject, Notification, NotificationPayload
from pydantic import BaseModel

from src.data_boundary import decode_json, is_string_object_mapping

ModelT = TypeVar("ModelT", bound=BaseModel)


def notification(method: str, params: Mapping[str, object]) -> Notification:
    values = dict(params)
    values.setdefault("threadId", "thread-1")
    values.setdefault("turnId", "turn-1")
    values.setdefault("itemId", "item-1")
    values.setdefault("completedAtMs", 1)
    if "turn" in values:
        turn = values["turn"]
        assert is_string_object_mapping(turn)
        turn = {"id": "turn-1", "items": [], "status": "completed", **turn}
        error = turn.get("error")
        if is_string_object_mapping(error) and not error.get("message"):
            turn["error"] = None
        values["turn"] = turn
    if "item" in values:
        item = values["item"]
        assert is_string_object_mapping(item)
        values["item"] = {"id": "item-1", **item}
    model = NOTIFICATION_MODELS[method]
    return Notification(method, cast(NotificationPayload, model.model_validate(values)))


def thread_response(thread_id: str = "thread-1") -> JsonObject:
    return {
        "approvalPolicy": "never",
        "approvalsReviewer": "user",
        "cwd": "/tmp",
        "model": "gpt-default",
        "modelProvider": "openai",
        "sandbox": {"type": "readOnly"},
        "thread": {
            "id": thread_id,
            "cliVersion": "0.163.0",
            "createdAt": 1,
            "updatedAt": 1,
            "cwd": "/tmp",
            "ephemeral": True,
            "modelProvider": "openai",
            "preview": "",
            "sessionId": "session-1",
            "source": "cli",
            "status": {"type": "idle"},
            "turns": [],
        },
    }


def params_object(params: BaseModel | JsonObject | None) -> dict[str, object]:
    if params is None:
        return {}
    if isinstance(params, dict):
        return dict(params)
    value = decode_json(params.model_dump_json(by_alias=True, exclude_none=True))
    assert is_string_object_mapping(value)
    return dict(value)


def sdk_model(model: type[ModelT], payload: Mapping[str, object]) -> ModelT:
    return model.model_validate(payload)


class FakeSDKClient(CodexClient):
    def __init__(self, *, authenticated: bool = True, fast_model: bool = True) -> None:
        self.authenticated = authenticated
        self.fast_model = fast_model
        self.started = False
        self.thread_params: dict[str, object] = {}
        self.resume_params: dict[str, object] = {}
        self.resumed_threads: list[tuple[str, dict[str, object]]] = []
        self.thread_start_count = 0
        self.turn_params: dict[str, object] = {}
        self.login_kwargs: dict[str, object] = {}
        self.interrupted: tuple[str, str] | None = None
        self.mcp_calls: list[dict[str, object]] = []
        self.structured_kwargs: dict[str, object] = {}
        self.proposal: object = {"summary": "修正", "warnings": [], "operations": []}
        self._globals: queue.Queue[Notification | Exception] = queue.Queue()
        self._login_events: queue.Queue[Notification | Exception] = queue.Queue()
        self._turn_events: queue.Queue[Notification | Exception] = queue.Queue()

    def start(self) -> None:
        self.started = True

    def initialize(self) -> InitializeResponse:
        return InitializeResponse(userAgent="codex/0.163.0")

    def close(self) -> None:
        self.started = False
        self.disconnect()

    def disconnect(self) -> None:
        for target in (self._globals, self._login_events, self._turn_events):
            target.put(TransportClosedError("fixture closed"))

    def account_read(self, params: v2.GetAccountParams | JsonObject | None = None) -> v2.GetAccountResponse:
        account = {"type": "chatgpt", "planType": "plus", "email": None} if self.authenticated else None
        return sdk_model(v2.GetAccountResponse, {"account": account, "requiresOpenaiAuth": True})

    def account_login_start(self, params: v2.LoginAccountParams | JsonObject) -> v2.LoginAccountResponse:
        values = params_object(params)
        self.login_kwargs = {
            "login_type": values.get("type"),
            "use_hosted_login_success_page": values.get("useHostedLoginSuccessPage"),
            "app_brand": values.get("appBrand"),
        }
        return sdk_model(
            v2.LoginAccountResponse,
            {"type": "chatgpt", "loginId": "login-1", "authUrl": "https://example.invalid/login"},
        )

    def account_logout(self) -> v2.LogoutAccountResponse:
        self.authenticated = False
        return v2.LogoutAccountResponse()

    def model_list(self, include_hidden: bool = False) -> v2.ModelListResponse:
        data: list[dict[str, object]] = []
        for model_id in ("gpt-default", "gpt-fast", "hidden"):
            if model_id == "gpt-fast" and not self.fast_model:
                continue
            data.append(
                {
                    "id": model_id,
                    "model": model_id,
                    "displayName": model_id,
                    "isDefault": model_id == "gpt-default",
                    "hidden": model_id == "hidden",
                    "description": "",
                    "defaultReasoningEffort": "medium",
                    "supportedReasoningEfforts": [],
                }
            )
        return sdk_model(v2.ModelListResponse, {"data": data})

    def thread_start(self, params: v2.ThreadStartParams | JsonObject | None = None) -> v2.ThreadStartResponse:
        self.thread_start_count += 1
        self.thread_params = params_object(params)
        return sdk_model(v2.ThreadStartResponse, thread_response(f"thread-{self.thread_start_count}"))

    def thread_resume(
        self, thread_id: str, params: v2.ThreadResumeParams | JsonObject | None = None
    ) -> v2.ThreadResumeResponse:
        self.resume_params = params_object(params)
        self.resumed_threads.append((thread_id, self.resume_params))
        return sdk_model(v2.ThreadResumeResponse, thread_response(thread_id))

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
        self.emit(notification("turn/started", {"turn": {"id": "turn-1"}}))
        for text in ("返", "答"):
            self.emit(notification("item/agentMessage/delta", {"delta": text}))
        self.emit(notification("item/completed", {"item": {"type": "agentMessage", "text": "返答"}}))
        self.emit(notification("turn/completed", {"turn": {"id": "turn-1", "status": "completed"}}))
        return sdk_model(v2.TurnStartResponse, {"turn": {"id": "turn-1", "status": "inProgress", "items": []}})

    def turn_interrupt(self, thread_id: str, turn_id: str) -> v2.TurnInterruptResponse:
        self.interrupted = (thread_id, turn_id)
        return v2.TurnInterruptResponse()

    def emit(self, event: Notification) -> None:
        target = self._turn_events
        if event.method.startswith("account/"):
            target = self._login_events if event.method == "account/login/completed" else self._globals
        target.put(event)

    @staticmethod
    def _receive(target: queue.Queue[Notification | Exception]) -> Notification:
        event = target.get()
        if isinstance(event, Exception):
            raise event
        return event

    def next_notification(self) -> Notification:
        return self._receive(self._globals)

    def next_turn_notification(self, turn_id: str) -> Notification:
        return self._receive(self._turn_events)

    def unregister_turn_notifications(self, turn_id: str) -> None:
        pass

    def wait_for_login_completed(self, login_id: str) -> v2.AccountLoginCompletedNotification:
        payload = self._receive(self._login_events).payload
        assert isinstance(payload, v2.AccountLoginCompletedNotification)
        return payload

    def complete_login(self) -> None:
        self.authenticated = True
        self.emit(notification("account/login/completed", {"loginId": "login-1", "success": True}))

    def request(self, method: str, params: JsonObject | None, *, response_model: type[ModelT]) -> ModelT:
        assert method == "mcpServerStatus/list"
        self.mcp_calls.append(dict(params or {}))
        return sdk_model(response_model, {"data": [], "nextCursor": None})


def fake_structured_turn(client: CodexClient, **kwargs: object) -> dict[str, object]:
    assert isinstance(client, FakeSDKClient)
    client.structured_kwargs = dict(kwargs)
    client.turn_params = dict(kwargs)
    callback = kwargs.get("on_event")
    if callable(callback):
        on_event = cast(Callable[[Notification], None], callback)
        on_event(notification("turn/started", {"turn": {"id": "turn-1"}}))
        on_event(notification("item/agentMessage/delta", {"delta": "提案"}))
    if not is_string_object_mapping(client.proposal):
        raise ValueError("invalid fixture output")
    return dict(client.proposal)
