"""Gemini CLI の ACP イベントを AIProvider の契約へ変換する。

認証とモデル設定は Gemini CLI に委ねる。通信は GeminiAcpClient が担当し、
このモジュールは通知と応答をプロバイダー共通の状態・イベントへ変換する。
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .ai_provider import (
    AIModel,
    AIProviderEvent,
    AIProviderListener,
    AIProviderPrompt,
    AIProviderPromptResult,
    AIProviderSession,
    AIProviderState,
)
from .gemini_acp_client import (
    ACP_PROTOCOL_VERSION as ACP_PROTOCOL_VERSION,
    DEFAULT_GEMINI_CLIENT_NAME as DEFAULT_GEMINI_CLIENT_NAME,
    GeminiAcpClient as GeminiAcpClient,
    GeminiAcpClientProtocol as GeminiAcpClientProtocol,
    GeminiAcpError as GeminiAcpError,
    GeminiAcpNotification as GeminiAcpNotification,
    GeminiAcpRequestTimeout as GeminiAcpRequestTimeout,
    GeminiAcpRpcError as GeminiAcpRpcError,
    _extract_session_id,
    _safe_error,
)
from .gemini_runtime import GeminiRuntimeInfo, detect_gemini


class GeminiAcpProvider:
    """Gemini ACP の状態と通知を AIProvider の契約へ変換する。"""

    provider_id = "gemini"
    display_name = "Gemini"

    def __init__(
        self,
        *,
        workspace_root: str | Path = ".",
        client_factory: Callable[[], GeminiAcpClientProtocol] | None = None,
        runtime_detector: Callable[..., GeminiRuntimeInfo] = detect_gemini,
        preferred_model: str = "",
        request_timeout: float = 30.0,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.client_factory = client_factory
        self.runtime_detector = runtime_detector
        self._preferred_model = str(preferred_model).strip()
        self._request_timeout = max(0.1, float(request_timeout))
        self._state = AIProviderState(
            selected_model="",
            model_selection_supported=False,
        )
        self._client: GeminiAcpClientProtocol | None = None
        self._listeners: list[AIProviderListener] = []
        self._auth_methods: tuple[Mapping[str, str], ...] = ()
        self._lock = threading.RLock()
        self._turn_sequence = 0

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
        old_client: GeminiAcpClientProtocol | None = None
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

            runtime = None
            if self.client_factory is None:
                runtime = self.runtime_detector(self.workspace_root)
                if not runtime.available:
                    return self._set_state(
                        availability="error",
                        auth_state="error",
                        error=runtime.error,
                    )
                client: GeminiAcpClientProtocol = GeminiAcpClient(
                    runtime.command,
                    cwd=self.workspace_root,
                    request_timeout=self._request_timeout,
                )
            else:
                client = self.client_factory()
            client.notification_callback = self._on_notification
            client.disconnect_callback = self._on_disconnect
            with self._lock:
                self._client = client
            initialize_result = client.start()
            auth_state, auth_label = _auth_state_from_initialize(initialize_result)
            self._auth_methods = _normalize_auth_methods(initialize_result)
            initial_models, initial_selected = _normalize_models(initialize_result)
            available_model_ids = {model.model_id for model in initial_models}
            selected_model = (
                self._preferred_model
                if self._preferred_model in available_model_ids
                else initial_selected
            )
            if initial_models:
                self._preferred_model = selected_model
            return self._set_state(
                availability="available",
                auth_state=auth_state,
                auth_label=auth_label,
                models=initial_models,
                model_selection_supported=bool(initial_models),
                selected_model=selected_model if initial_models else "",
                login_available=bool(self._auth_methods),
                error="",
            )
        except Exception as error:
            with self._lock:
                failed_client = self._client
                self._client = None
            if failed_client is not None:
                try:
                    failed_client.stop()
                except Exception:
                    pass
            return self._set_state(
                availability="error",
                auth_state="error",
                error=f"Gemini CLIへ接続できません: {_safe_error(error)}",
            )

    def refresh(self) -> AIProviderState:
        with self._lock:
            if self._client is None:
                return self._set_state(
                    availability="error",
                    auth_state="error",
                    error="Gemini CLIへ接続されていません",
                )
            return self._state

    def login(self, *, relogin: bool = False) -> AIProviderState:
        del relogin
        client = self._require_client()
        method = next(
            (item.get("id", "") for item in self._auth_methods if item.get("id")),
            "",
        )
        if not method:
            raise GeminiAcpProviderError("Gemini CLIが認証方式を返していません")
        try:
            client.authenticate(method)
        except Exception as error:
            state = self._set_state(
                availability="available",
                auth_state="error",
                error=f"Geminiの認証に失敗しました: {_safe_error(error)}",
            )
            self._emit(AIProviderEvent(kind="auth_changed", refresh_state=False, error=state.error))
            raise GeminiAcpProviderError(state.error) from error
        label = next(
            (item.get("name", "") for item in self._auth_methods if item.get("id") == method),
            "Gemini CLI",
        )
        return self._set_state(
            availability="available",
            auth_state="authenticated",
            auth_label=label,
            error="",
        )

    def logout(self) -> AIProviderState:
        # ACP has no stable logout method.  Do not delete or mutate the CLI's
        # credential store; this state only prevents new turns in this client.
        return self._set_state(
            availability="available",
            auth_state="unauthenticated",
            auth_label="",
            models=(),
            model_selection_supported=False,
            selected_model="",
            login_available=bool(self._auth_methods),
            error="",
        )

    def select_model(self, model_id: str) -> AIProviderState:
        selected = str(model_id).strip()
        state = self.state
        available = {item.model_id for item in state.models}
        if not state.model_selection_supported or selected not in available:
            raise GeminiAcpProviderError(
                f"選択したGeminiモデルはACPから利用できません: {selected or '（未選択）'}"
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
        try:
            if existing_session_id and hasattr(client, "load_session"):
                result = client.load_session(
                    session_id=existing_session_id,
                    cwd=workspace_root,
                )
            else:
                result = client.new_session(cwd=workspace_root, model_id="")
        except Exception as error:
            if _looks_like_auth_error(error):
                self._set_state(
                    availability="available",
                    auth_state="unauthenticated",
                    auth_label="",
                    error="Gemini CLIの認証が必要です",
                )
            raise GeminiAcpProviderError(
                f"Geminiセッションを開始できません: {_safe_error(error)}"
            ) from error
        session_id = _extract_session_id(result)
        if not session_id:
            raise GeminiAcpProviderError("Gemini ACPがsession IDを返しませんでした")
        models, selected = _normalize_models(result)
        if not models:
            current_state = self.state
            models = current_state.models
            selected = current_state.selected_model
        requested = str(model_id).strip() or self._preferred_model
        if requested and requested in {item.model_id for item in models}:
            try:
                client.request(
                    "unstable_setSessionModel",
                    {"sessionId": session_id, "modelId": requested},
                )
                selected = requested
            except AttributeError:
                # A narrow fake/client compatibility path; the official client
                # exposes this through ``new_session`` when requested.
                pass
            except Exception as error:
                raise GeminiAcpProviderError(
                    f"Geminiモデルを選択できません: {_safe_error(error)}"
                ) from error
        self._set_state(
            availability="available",
            auth_state="authenticated",
            models=models,
            model_selection_supported=bool(models),
            selected_model=selected if models else "",
            error="",
        )
        return AIProviderSession(session_id=session_id)

    def send_prompt(self, request: AIProviderPrompt) -> AIProviderPromptResult:
        client = self._require_client()
        with self._lock:
            self._turn_sequence += 1
            turn_id = f"gemini-turn-{self._turn_sequence}-{uuid.uuid4().hex[:8]}"
        self._emit(
            AIProviderEvent(
                kind="turn_started",
                session_id=request.session.session_id,
                turn_id=turn_id,
            )
        )
        try:
            response = client.prompt(
                session_id=request.session.session_id,
                text=request.prompt,
                turn_id=turn_id,
            )
            stop_reason = str(response.get("stopReason", "end_turn"))
            status = _stop_reason_status(stop_reason)
            self._emit(
                AIProviderEvent(
                    kind="turn_completed",
                    session_id=request.session.session_id,
                    turn_id=turn_id,
                    status=status,
                    error="" if status != "failed" else f"Geminiの応答が終了しました: {stop_reason}",
                )
            )
            return AIProviderPromptResult(turn_id=turn_id)
        except Exception as error:
            if _looks_like_auth_error(error):
                self._set_state(
                    availability="available",
                    auth_state="unauthenticated",
                    auth_label="",
                    error="Gemini CLIの認証が必要です",
                )
            safe = f"Geminiの応答でエラーが発生しました: {_safe_error(error)}"
            self._emit(
                AIProviderEvent(
                    kind="error",
                    session_id=request.session.session_id,
                    turn_id=turn_id,
                    error=safe,
                )
            )
            self._emit(
                AIProviderEvent(
                    kind="turn_completed",
                    session_id=request.session.session_id,
                    turn_id=turn_id,
                    status="failed",
                    error=safe,
                )
            )
            raise GeminiAcpProviderError(safe) from error

    def cancel_active_turn(self, *, session_id: str, turn_id: str) -> None:
        if not session_id or not turn_id:
            return
        self._require_client().cancel(session_id=session_id, turn_id=turn_id)

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

    def _on_notification(self, notification: GeminiAcpNotification) -> None:
        if notification.method == "protocol/error":
            self._emit(
                AIProviderEvent(
                    kind="protocol_error",
                    session_id=notification.session_id,
                    turn_id=notification.turn_id,
                    error="Gemini ACPメッセージが不正です",
                )
            )
            return
        if notification.method != "session/update":
            return
        params = notification.params
        update = params.get("update", params)
        if not isinstance(update, Mapping):
            self._emit(
                AIProviderEvent(
                    kind="protocol_error",
                    session_id=notification.session_id,
                    turn_id=notification.turn_id,
                    error="Gemini ACPのsession updateが不正です",
                )
            )
            return
        update_kind = str(update.get("sessionUpdate", ""))
        if update_kind == "agent_message_chunk":
            text = _content_text(update.get("content"))
            if text:
                self._emit(
                    AIProviderEvent(
                        kind="text_delta",
                        session_id=notification.session_id,
                        turn_id=notification.turn_id,
                        text=text,
                        payload={"session_update": update_kind},
                    )
                )
            return
        if update_kind in {"tool_call", "tool_call_update"}:
            self._emit(
                AIProviderEvent(
                    kind="tool_call",
                    session_id=notification.session_id,
                    turn_id=notification.turn_id,
                    payload={"session_update": update_kind},
                )
            )

    def _on_disconnect(self, error: Exception) -> None:
        self._set_state(
            availability="disconnected",
            auth_state="unknown",
            error=f"Gemini CLIとの接続が切断されました: {_safe_error(error)}",
        )
        self._emit(
            AIProviderEvent(
                kind="disconnected",
                error="Gemini CLIとの接続が切断されました",
            )
        )

    def _require_client(self) -> GeminiAcpClientProtocol:
        with self._lock:
            client = self._client
            state = self._state
        if client is None or state.availability not in {"connecting", "available"}:
            raise GeminiAcpProviderError("Gemini ACPへ接続されていません")
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


class GeminiACPProvider(GeminiAcpProvider):
    """Compatibility spelling for callers that capitalize ACP."""


class GeminiAcpProviderError(GeminiAcpError):
    """Provider-level Gemini ACP error."""


GeminiACPProviderError = GeminiAcpProviderError


def _auth_state_from_initialize(result: Mapping[str, Any]) -> tuple[str, str]:
    raw = result.get("authState", result.get("auth_state"))
    if isinstance(raw, str):
        normalized = raw.casefold().replace("-", "_")
        if normalized in {"authenticated", "authenticted", "logged_in", "loggedin"}:
            return "authenticated", str(result.get("authLabel") or "Gemini CLI")
        if normalized in {"unauthenticated", "unauthorized", "auth_required", "required"}:
            return "unauthenticated", ""
        if normalized in {"error", "failed"}:
            return "error", ""
    for key in ("authenticated", "isAuthenticated", "loggedIn"):
        if key in result and isinstance(result[key], bool):
            return ("authenticated", "Gemini CLI") if result[key] else ("unauthenticated", "")
    # ACP v1 initialize advertises auth methods but does not expose whether
    # the CLI's persisted credentials are valid.  Keep this explicit rather
    # than guessing from a provider-specific model name.
    return "unknown", ""


def _normalize_auth_methods(result: Mapping[str, Any]) -> tuple[Mapping[str, str], ...]:
    raw = result.get("authMethods")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    methods: list[Mapping[str, str]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        method_id = str(item.get("id") or "").strip()
        if not method_id:
            continue
        methods.append(
            {
                "id": method_id,
                "name": str(item.get("name") or method_id),
            }
        )
    return tuple(methods)


def _normalize_models(result: Mapping[str, Any]) -> tuple[tuple[AIModel, ...], str]:
    payload = result.get("models")
    if not isinstance(payload, Mapping):
        return (), ""
    raw = payload.get("availableModels")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return (), ""
    current = str(payload.get("currentModelId") or "").strip()
    models: list[AIModel] = []
    for item in raw:
        if isinstance(item, Mapping):
            model_id = str(item.get("modelId") or item.get("id") or "").strip()
            label = str(item.get("name") or item.get("displayName") or model_id)
        else:
            model_id = str(item).strip()
            label = model_id
        if not model_id:
            continue
        models.append(
            AIModel(
                model_id=model_id,
                display_name=label,
                is_default=model_id == current,
            )
        )
    return tuple(models), current if current in {item.model_id for item in models} else ""


def _content_text(content: object) -> str:
    if isinstance(content, Mapping):
        if str(content.get("type", "")) == "text":
            return str(content.get("text") or "")
        return str(content.get("text") or "")
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        return "".join(_content_text(item) for item in content)
    return str(content) if isinstance(content, str) else ""


def _stop_reason_status(stop_reason: str) -> str:
    normalized = stop_reason.casefold()
    if normalized in {"cancelled", "canceled", "interrupted"}:
        return "interrupted"
    if normalized in {"error", "failed", "refusal"}:
        return "failed"
    return "completed"


def _looks_like_auth_error(error: object) -> bool:
    text = _safe_error(error).casefold()
    return any(
        token in text
        for token in (
            "auth",
            "login",
            "credential",
            "api key",
            "apikey",
            "unauthorized",
            "unauthenticated",
            "permission denied",
        )
    )


__all__ = [
    "ACP_PROTOCOL_VERSION",
    "GeminiACPProvider",
    "GeminiACPProviderError",
    "GeminiAcpClient",
    "GeminiAcpClientProtocol",
    "GeminiAcpError",
    "GeminiAcpNotification",
    "GeminiAcpProvider",
    "GeminiAcpProviderError",
    "GeminiAcpRequestTimeout",
    "GeminiAcpRpcError",
]
