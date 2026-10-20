"""Codex app-server adapter for the provider-neutral AI chat boundary."""

from __future__ import annotations

import threading
from collections.abc import Callable

from openai_codex.client import CodexClient
from openai_codex.generated.v2_all import (
    AccountLoginCompletedNotification,
    AccountUpdatedNotification,
    AgentMessageDeltaNotification,
    AgentMessageThreadItem,
    ChatgptAccount,
    ChatgptLoginAccountResponse,
    ErrorNotification,
    ItemCompletedNotification,
    TurnCompletedNotification,
    TurnStartedNotification,
)
from openai_codex.models import JsonObject, Notification

from .ai_provider import (
    AIModel,
    AIProviderEvent,
    AIProviderListener,
    AIProviderPrompt,
    AIProviderPromptResult,
    AIProviderSession,
    AIProviderState,
)
from .codex_runtime import redact_codex_diagnostic
from .codex_sdk import sdk_deadline


class CodexAIProvider:
    """Translate Codex account/model/thread/turn RPCs into generic provider events."""

    provider_id = "codex"
    display_name = "Codex"

    def __init__(
        self,
        *,
        client_factory: Callable[[], CodexClient],
        preferred_model: str = "",
    ) -> None:
        self.client_factory = client_factory
        self._preferred_model = str(preferred_model).strip()
        self._state = AIProviderState(selected_model=self._preferred_model)
        self._client: CodexClient | None = None
        self._listeners: list[AIProviderListener] = []
        self._lock = threading.RLock()

    @property
    def state(self) -> AIProviderState:
        with self._lock:
            return self._state

    def subscribe(self, listener: AIProviderListener) -> None:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def unsubscribe(self, listener: AIProviderListener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def connect(self, *, force: bool = False) -> AIProviderState:
        old_client: CodexClient | None = None
        try:
            with self._lock:
                if not force and self._client is not None:
                    return self._state
                if force:
                    old_client = self._client
                    self._client = None
                self._set_state(availability="connecting", auth_state="checking", error="")
            if old_client is not None:
                old_client.close()
            client = self.client_factory()
            with self._lock:
                self._client = client
            with sdk_deadline(client):
                client.start()
                client.initialize()
            threading.Thread(
                target=self._consume_global, args=(client,), name="codex-account-events", daemon=True
            ).start()
            return self.refresh()
        except Exception as error:
            with self._lock:
                failed_client = self._client
                self._client = None
            if failed_client is not None:
                try:
                    failed_client.close()
                except Exception:
                    pass
            return self._set_state(
                availability="error",
                auth_state="error",
                login_available=True,
                error=f"Codexへ接続できません: {self._safe_error(error)}",
            )

    def refresh(self) -> AIProviderState:
        client = self._require_client()
        with sdk_deadline(client):
            account = client.account_read({"refreshToken": False})
        authenticated = account.account is not None or not account.requires_openai_auth
        auth_label = "Codex" if authenticated else ""
        if account.account is not None:
            identity = account.account.root
            if isinstance(identity, ChatgptAccount):
                auth_label = f"ChatGPT · {identity.plan_type.value}"
            elif identity.type == "apiKey":
                auth_label = "APIキー"
            else:
                auth_label = "Amazon Bedrock"
        if not authenticated:
            return self._set_state(
                availability="available",
                auth_state="unauthenticated",
                auth_label="",
                models=(),
                login_url="",
                login_id="",
                login_available=True,
                error="",
            )
        with sdk_deadline(client):
            model_result = client.model_list(include_hidden=False)
        models = tuple(
            AIModel(model_id=item.id, display_name=item.display_name or item.id, is_default=item.is_default)
            for item in model_result.data
            if not item.hidden
        )
        if not models:
            raise CodexAIProviderError("利用可能なCodexモデルが返されませんでした")
        available = {item.model_id for item in models}
        selected = self._preferred_model
        model_error = ""
        if selected not in available:
            if selected:
                model_error = f"保存されていたCodexモデルは現在利用できません: {selected}"
            selected = next(
                (item.model_id for item in models if item.is_default),
                models[0].model_id,
            )
        self._preferred_model = selected
        return self._set_state(
            availability="available",
            auth_state="authenticated",
            auth_label=auth_label,
            login_url="",
            login_id="",
            models=models,
            selected_model=selected,
            login_available=True,
            error=model_error,
        )

    def login(self, *, relogin: bool = False) -> AIProviderState:
        client = self._require_client()
        if relogin:
            with sdk_deadline(client):
                client.account_logout()
        with sdk_deadline(client):
            result = client.account_login_start(
                {"type": "chatgpt", "useHostedLoginSuccessPage": True, "appBrand": "chatgpt"}
            )
        login = result.root
        if not isinstance(login, ChatgptLoginAccountResponse) or not login.auth_url:
            raise CodexAIProviderError("ログイン用URLが返されませんでした")
        login_id, login_url = login.login_id, login.auth_url
        state = self._set_state(
            availability="available",
            auth_state="login_pending",
            login_id=login_id,
            login_url=login_url,
            login_available=True,
            error="",
        )
        threading.Thread(
            target=self._consume_login, args=(client, login_id), name="codex-login-events", daemon=True
        ).start()
        return state

    def logout(self) -> AIProviderState:
        client = self._require_client()
        with sdk_deadline(client):
            client.account_logout()
        return self._set_state(
            availability="available",
            auth_state="unauthenticated",
            auth_label="",
            models=(),
            login_available=True,
            login_url="",
            login_id="",
            error="",
        )

    def select_model(self, model_id: str) -> AIProviderState:
        selected = str(model_id).strip()
        available = {item.model_id for item in self.state.models}
        if selected not in available:
            raise CodexAIProviderError(f"選択したCodexモデルは現在利用できません: {selected or '（未選択）'}")
        self._preferred_model = selected
        return self._set_state(selected_model=selected, error="")

    def new_session(
        self,
        *,
        model_id: str,
        workspace_root: str,
        existing_session_id: str = "",
    ) -> AIProviderSession:
        client = self._require_client()
        params: JsonObject = {
            "model": model_id,
            "cwd": workspace_root,
            "approvalPolicy": "never",
            "sandbox": "read-only",
        }
        with sdk_deadline(client):
            if existing_session_id:
                session_id = client.thread_resume(existing_session_id, params).thread.id
            else:
                session_id = client.thread_start({**params, "serviceName": "subtitle_edit_bay"}).thread.id
        return AIProviderSession(session_id=session_id)

    def send_prompt(self, request: AIProviderPrompt) -> AIProviderPromptResult:
        client = self._require_client()
        with sdk_deadline(client):
            response = client.turn_start(
                request.session.session_id,
                request.prompt,
                {
                    "model": request.model_id,
                    "cwd": request.workspace_root,
                    "approvalPolicy": "never",
                    "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
                },
            )
        turn_id = response.turn.id
        threading.Thread(
            target=self._consume_turn, args=(client, turn_id), name="codex-chat-events", daemon=True
        ).start()
        return AIProviderPromptResult(turn_id=turn_id)

    def cancel_active_turn(self, *, session_id: str, turn_id: str) -> None:
        if not session_id or not turn_id:
            return
        client = self._require_client()
        with sdk_deadline(client):
            client.turn_interrupt(session_id, turn_id)

    def cancel(self, *, session_id: str, turn_id: str) -> None:
        self.cancel_active_turn(session_id=session_id, turn_id=turn_id)

    def close(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        self._set_state(availability="disconnected", auth_state="unknown")

    def _consume_global(self, client: CodexClient) -> None:
        try:
            while self._client is client:
                event = client.next_notification()
                if self._client is client:
                    self._on_notification(event)
        except Exception as error:
            self._connection_failed(client, error)

    def _consume_login(self, client: CodexClient, login_id: str) -> None:
        try:
            result = client.wait_for_login_completed(login_id)
            if self._client is client:
                self._on_notification(Notification("account/login/completed", result))
        except Exception as error:
            self._connection_failed(client, error)

    def _consume_turn(self, client: CodexClient, turn_id: str) -> None:
        try:
            while self._client is client:
                event = client.next_turn_notification(turn_id)
                if self._client is not client:
                    return
                self._on_notification(event)
                if isinstance(event.payload, TurnCompletedNotification):
                    return
        except Exception as error:
            self._connection_failed(client, error)
        finally:
            client.unregister_turn_notifications(turn_id)

    def _connection_failed(self, client: CodexClient, error: Exception) -> None:
        with self._lock:
            if self._client is not client:
                return
            self._client = None
        try:
            client.close()
        except Exception:
            pass
        self._on_disconnect(error)

    def _on_notification(self, notification: Notification) -> None:
        payload = notification.payload
        if isinstance(payload, AccountLoginCompletedNotification):
            if payload.success:
                self._emit(AIProviderEvent(kind="auth_changed", refresh_state=True))
            else:
                self._set_state(
                    auth_state="error",
                    login_url="",
                    error=f"Codexの認証に失敗しました: {self._safe_error(payload.error or '認証が完了しませんでした')}",
                )
        elif isinstance(payload, AccountUpdatedNotification):
            if self.state.auth_state not in {"logging_in", "login_pending"}:
                self._emit(AIProviderEvent(kind="auth_changed", refresh_state=True))
        elif isinstance(payload, TurnStartedNotification):
            self._emit(AIProviderEvent(kind="turn_started", turn_id=payload.turn.id))
        elif isinstance(payload, AgentMessageDeltaNotification):
            self._emit(AIProviderEvent(kind="text_delta", text=payload.delta))
        elif isinstance(payload, ItemCompletedNotification):
            item = payload.item.root
            if isinstance(item, AgentMessageThreadItem):
                self._emit(AIProviderEvent(kind="message_completed", text=item.text))
        elif isinstance(payload, ErrorNotification):
            self._emit(
                AIProviderEvent(
                    kind="error", error=f"Codexの応答でエラーが発生しました: {self._safe_error(payload.error.message)}"
                )
            )
        elif isinstance(payload, TurnCompletedNotification):
            turn = payload.turn
            detail = turn.error.message if turn.error is not None else ""
            self._emit(
                AIProviderEvent(
                    kind="turn_completed",
                    status=turn.status.value,
                    error=self._safe_error(detail or "原因を確認できません") if turn.status.value == "failed" else "",
                )
            )

    def _on_disconnect(self, error: Exception) -> None:
        self._set_state(
            availability="disconnected",
            auth_state="unknown",
            error="Codexとの接続が切断されました。再接続してください",
        )
        self._emit(
            AIProviderEvent(
                kind="disconnected",
                error="Codexとの接続が切断されました。再接続してください",
            )
        )

    def _require_client(self) -> CodexClient:
        with self._lock:
            client = self._client
            state = self._state
        if client is None or state.availability not in {"connecting", "available"}:
            raise CodexAIProviderError("Codex App Serverへ接続されていません")
        return client

    def _set_state(
        self,
        *,
        availability: str | None = None,
        auth_state: str | None = None,
        auth_label: str | None = None,
        login_url: str | None = None,
        login_id: str | None = None,
        models: tuple[AIModel, ...] | None = None,
        model_selection_supported: bool | None = None,
        selected_model: str | None = None,
        error: str | None = None,
        login_available: bool | None = None,
    ) -> AIProviderState:
        with self._lock:
            current = self._state
            self._state = AIProviderState(
                availability=current.availability if availability is None else availability,
                auth_state=current.auth_state if auth_state is None else auth_state,
                auth_label=current.auth_label if auth_label is None else auth_label,
                login_url=current.login_url if login_url is None else login_url,
                login_id=current.login_id if login_id is None else login_id,
                models=current.models if models is None else models,
                model_selection_supported=(
                    current.model_selection_supported
                    if model_selection_supported is None
                    else model_selection_supported
                ),
                selected_model=current.selected_model if selected_model is None else selected_model,
                error=current.error if error is None else error,
                login_available=current.login_available if login_available is None else login_available,
            )
            state = self._state
        self._emit(AIProviderEvent(kind="state_changed"))
        return state

    def _emit(self, event: AIProviderEvent) -> None:
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except RuntimeError:
                pass

    @staticmethod
    def _safe_error(error: object) -> str:
        return redact_codex_diagnostic(error)


class CodexAIProviderError(RuntimeError):
    pass
