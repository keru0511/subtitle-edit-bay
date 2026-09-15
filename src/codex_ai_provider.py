"""Codex app-server adapter for the provider-neutral AI chat boundary."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

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


class CodexAppServerClientProtocol(Protocol):
    notification_callback: Callable[[Any], None] | None
    disconnect_callback: Callable[[Exception], None] | None

    def start(self) -> Mapping[str, Any]: ...

    def stop(self) -> None: ...

    def account_read(self, *, refresh_token: bool = False) -> Mapping[str, Any]: ...

    def account_login_start(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def account_logout(self) -> Mapping[str, Any]: ...

    def model_list(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def thread_start(self, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]: ...

    def thread_resume(
        self,
        thread_id: str,
        *,
        model: str | None = None,
        cwd: str | Path | None = None,
        approval_policy: str | None = None,
        sandbox: str | None = None,
    ) -> Mapping[str, Any]: ...

    def turn_start(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def turn_interrupt(self, turn_id: str, *, thread_id: str) -> Mapping[str, Any]: ...


class CodexAIProvider:
    """Translate Codex account/model/thread/turn RPCs into generic provider events."""

    provider_id = "codex"
    display_name = "Codex"

    def __init__(
        self,
        *,
        client_factory: Callable[[], CodexAppServerClientProtocol],
        preferred_model: str = "",
    ) -> None:
        self.client_factory = client_factory
        self._preferred_model = str(preferred_model).strip()
        self._state = AIProviderState(selected_model=self._preferred_model)
        self._client: CodexAppServerClientProtocol | None = None
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
        old_client: CodexAppServerClientProtocol | None = None
        try:
            with self._lock:
                if not force and self._client is not None:
                    return self._state
                if force:
                    old_client = self._client
                    self._client = None
                self._set_state(availability="connecting", auth_state="checking", error="")
            if old_client is not None:
                old_client.stop()
            client = self.client_factory()
            client.notification_callback = self._on_notification
            client.disconnect_callback = self._on_disconnect
            with self._lock:
                self._client = client
            client.start()
            return self.refresh()
        except Exception as error:
            with self._lock:
                client = self._client
                self._client = None
            if client is not None:
                try:
                    client.stop()
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
        account = client.account_read(refresh_token=False)
        authenticated, auth_label = _account_status(account)
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
        model_result = client.model_list(limit=100, include_hidden=False)
        models = _normalize_models(model_result)
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
            client.account_logout()
        result = client.account_login_start(
            login_type="chatgpt",
            use_hosted_login_success_page=True,
            app_brand="chatgpt",
        )
        login_id = str(result.get("loginId") or "")
        login_url = str(result.get("authUrl") or result.get("url") or "")
        if not login_url:
            raise CodexAIProviderError("ログイン用URLが返されませんでした")
        return self._set_state(
            availability="available",
            auth_state="login_pending",
            login_id=login_id,
            login_url=login_url,
            login_available=True,
            error="",
        )

    def logout(self) -> AIProviderState:
        client = self._require_client()
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
            raise CodexAIProviderError(
                f"選択したCodexモデルは現在利用できません: {selected or '（未選択）'}"
            )
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
        if existing_session_id:
            result = client.thread_resume(
                existing_session_id,
                model=model_id,
                cwd=workspace_root,
                approval_policy="never",
                sandbox="read-only",
            )
        else:
            result = client.thread_start(
                {
                    "model": model_id,
                    "cwd": workspace_root,
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "serviceName": "subtitle_edit_bay",
                }
            )
        session_id = _extract_id(result, "thread")
        if not session_id:
            raise CodexAIProviderError("Codexチャットを開始または再開できませんでした")
        return AIProviderSession(session_id=session_id)

    def send_prompt(self, request: AIProviderPrompt) -> AIProviderPromptResult:
        client = self._require_client()
        response = client.turn_start(
            thread_id=request.session.session_id,
            prompt=request.prompt,
            model=request.model_id,
            cwd=request.workspace_root,
            approval_policy="never",
            sandbox_policy={
                "type": "readOnly",
                "networkAccess": False,
            },
        )
        return AIProviderPromptResult(turn_id=_extract_id(response, "turn"))

    def cancel_active_turn(self, *, session_id: str, turn_id: str) -> None:
        if not session_id or not turn_id:
            return
        self._require_client().turn_interrupt(turn_id, thread_id=session_id)

    def cancel(self, *, session_id: str, turn_id: str) -> None:
        self.cancel_active_turn(session_id=session_id, turn_id=turn_id)

    def close(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
        if client is not None:
            try:
                client.stop()
            except Exception:
                pass
        self._set_state(availability="disconnected", auth_state="unknown")

    def _on_notification(self, notification: Any) -> None:
        method = str(getattr(notification, "method", ""))
        params = getattr(notification, "params", {})
        if not isinstance(params, Mapping):
            params = {}
        if method == "account/login/completed":
            if bool(params.get("success", False)):
                self._emit(AIProviderEvent(kind="auth_changed", refresh_state=True))
            else:
                error = self._safe_error(params.get("error") or "認証が完了しませんでした")
                self._set_state(
                    auth_state="error",
                    login_url="",
                    error=f"Codexの認証に失敗しました: {error}",
                )
            return
        if method == "account/updated":
            if self.state.auth_state not in {"logging_in", "login_pending"}:
                self._emit(AIProviderEvent(kind="auth_changed", refresh_state=True))
            return
        if method == "turn/started":
            turn_id = _extract_id(params, "turn") or str(params.get("turnId", ""))
            self._emit(AIProviderEvent(kind="turn_started", turn_id=turn_id))
            return
        if method == "item/agentMessage/delta":
            delta = str(params.get("delta") or params.get("text") or "")
            if delta:
                self._emit(AIProviderEvent(kind="text_delta", text=delta))
            return
        if method == "item/completed":
            item = params.get("item")
            if isinstance(item, Mapping) and item.get("type") == "agentMessage":
                self._emit(
                    AIProviderEvent(
                        kind="message_completed",
                        text=str(item.get("text") or ""),
                    )
                )
            return
        if method == "error":
            payload = params.get("error", params)
            detail = payload.get("message") if isinstance(payload, Mapping) else payload
            self._emit(
                AIProviderEvent(
                    kind="error",
                    error=f"Codexの応答でエラーが発生しました: {self._safe_error(detail)}",
                )
            )
            return
        if method == "turn/completed":
            turn = params.get("turn", params)
            status = str(turn.get("status", "completed") if isinstance(turn, Mapping) else "completed")
            detail = ""
            if status == "failed" and isinstance(turn, Mapping):
                payload = turn.get("error", {})
                detail = payload.get("message") if isinstance(payload, Mapping) else str(payload)
            self._emit(
                AIProviderEvent(
                    kind="turn_completed",
                    status=status,
                    error=self._safe_error(detail or "原因を確認できません") if status == "failed" else "",
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

    def _require_client(self) -> CodexAppServerClientProtocol:
        with self._lock:
            client = self._client
            state = self._state
        if client is None or state.availability not in {"connecting", "available"}:
            raise CodexAIProviderError("Codex App Serverへ接続されていません")
        return client

    def _set_state(self, **changes: Any) -> AIProviderState:
        with self._lock:
            current = self._state
            self._state = AIProviderState(
                availability=changes.get("availability", current.availability),
                auth_state=changes.get("auth_state", current.auth_state),
                auth_label=changes.get("auth_label", current.auth_label),
                login_url=changes.get("login_url", current.login_url),
                login_id=changes.get("login_id", current.login_id),
                models=changes.get("models", current.models),
                model_selection_supported=changes.get(
                    "model_selection_supported", current.model_selection_supported
                ),
                selected_model=changes.get("selected_model", current.selected_model),
                error=changes.get("error", current.error),
                login_available=changes.get("login_available", current.login_available),
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


def _account_status(result: Mapping[str, Any]) -> tuple[bool, str]:
    legacy_authenticated = result.get("authenticated", result.get("loggedIn"))
    account = result.get("account")
    authenticated = bool(legacy_authenticated)
    if isinstance(account, Mapping):
        authenticated = True
        account_type = str(account.get("type", ""))
        plan_type = str(account.get("planType", "") or "")
    else:
        account_type = str(result.get("authMode", "") or "")
        plan_type = str(result.get("planType", "") or "")
        if legacy_authenticated is None and result.get("requiresOpenaiAuth") is False:
            authenticated = True
    type_labels = {
        "chatgpt": "ChatGPT",
        "apikey": "APIキー",
        "apiKey": "APIキー",
        "amazonBedrock": "Amazon Bedrock",
        "bedrockApiKey": "Amazon Bedrock",
    }
    label = type_labels.get(account_type, "Codex") if authenticated else ""
    if plan_type:
        label = f"{label} · {plan_type}"
    return authenticated, label


def _normalize_models(result: Mapping[str, Any]) -> tuple[AIModel, ...]:
    normalized: list[AIModel] = []
    for item in result.get("data", []):
        if not isinstance(item, Mapping) or bool(item.get("hidden", False)):
            continue
        model_id = str(item.get("id") or item.get("model") or "").strip()
        if not model_id:
            continue
        normalized.append(
            AIModel(
                model_id=model_id,
                display_name=str(item.get("displayName") or model_id),
                is_default=bool(item.get("isDefault", False)),
            )
        )
    return tuple(normalized)


def _extract_id(result: Mapping[str, Any], key: str) -> str:
    nested = result.get(key, result)
    if isinstance(nested, Mapping):
        return str(nested.get("id", "")) or str(result.get(f"{key}Id", ""))
    return str(result.get(f"{key}Id", ""))
