"""Gemini CLI ACP adapter for the provider-neutral chat boundary.

Gemini CLI owns authentication and model configuration.  Subtitle Edit Bay
only starts the official ``gemini --acp`` subprocess and translates the ACP
JSON-RPC messages into :mod:`src.ai_provider` events.  No credential or raw
protocol payload is retained or logged here.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .ai_provider import (
    AIModel,
    AIProviderEvent,
    AIProviderListener,
    AIProviderPrompt,
    AIProviderPromptResult,
    AIProviderSession,
    AIProviderState,
)
from .application_logging import redact_text
from .gemini_runtime import GeminiRuntimeInfo, detect_gemini


ACP_PROTOCOL_VERSION = 1
DEFAULT_GEMINI_CLIENT_NAME = "Subtitle Edit Bay"


class GeminiAcpError(RuntimeError):
    """Base error for Gemini ACP process and protocol failures."""


class GeminiAcpRequestTimeout(GeminiAcpError):
    """Raised when a Gemini ACP response does not arrive in time."""


class GeminiAcpRpcError(GeminiAcpError):
    """A JSON-RPC error returned by Gemini CLI."""

    def __init__(self, code: int, message: object) -> None:
        self.code = int(code)
        self.message = _safe_error(message)
        super().__init__(self.message)


@dataclass(frozen=True)
class GeminiAcpNotification:
    """A safe ACP notification received from the CLI."""

    method: str
    params: Mapping[str, Any]
    session_id: str = ""
    turn_id: str = ""


class GeminiAcpClientProtocol(Protocol):
    notification_callback: Callable[[GeminiAcpNotification], None] | None
    disconnect_callback: Callable[[Exception], None] | None

    def start(self) -> Mapping[str, Any]: ...

    def stop(self) -> None: ...

    def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
        allow_uninitialized: bool = False,
    ) -> Any: ...

    def authenticate(self, method_id: str) -> Mapping[str, Any] | None: ...

    def new_session(self, *, cwd: str | Path, model_id: str = "") -> Mapping[str, Any]: ...

    def load_session(
        self,
        *,
        session_id: str,
        cwd: str | Path,
    ) -> Mapping[str, Any]: ...

    def prompt(self, *, session_id: str, text: str, turn_id: str) -> Mapping[str, Any]: ...

    def cancel(self, *, session_id: str, turn_id: str) -> None: ...


class GeminiAcpClient:
    """Minimal newline-delimited JSON-RPC client for ``gemini --acp``."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path | None = None,
        environment: Mapping[str, str] | None = None,
        request_timeout: float = 30.0,
        notification_callback: Callable[[GeminiAcpNotification], None] | None = None,
        disconnect_callback: Callable[[Exception], None] | None = None,
        log_callback: Callable[[str], None] | None = None,
    ) -> None:
        if not command:
            raise ValueError("Gemini ACP command must not be empty")
        self.command = tuple(str(item) for item in command)
        _validate_acp_command(self.command)
        self.cwd = str(cwd) if cwd else None
        self.environment = dict(environment or {})
        self.request_timeout = max(0.1, float(request_timeout))
        self.notification_callback = notification_callback
        self.disconnect_callback = disconnect_callback
        self.log_callback = log_callback
        self._process: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._pending: dict[int, Future[Any]] = {}
        self._next_request_id = 1
        self._active_turns: dict[str, str] = {}
        self._initialized = False
        self._stopping = False

    @property
    def is_running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    @property
    def initialized(self) -> bool:
        with self._state_lock:
            return self._initialized

    def start(self) -> Mapping[str, Any]:
        if self.is_running:
            if not self.initialized:
                raise GeminiAcpError("Gemini ACP is starting")
            return {}

        environment = os.environ.copy()
        environment.update(self.environment)
        environment.setdefault("PYTHONUTF8", "1")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._process = subprocess.Popen(
                list(self.command),
                cwd=self.cwd,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
                shell=False,
            )
        except OSError as error:
            self._process = None
            raise GeminiAcpError(f"Gemini ACPを起動できません: {_safe_error(error)}") from error

        with self._state_lock:
            self._stopping = False
            self._initialized = False
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="gemini-acp-reader",
            daemon=True,
        )
        self._reader_thread.start()
        try:
            result = self.request(
                "initialize",
                {
                    "protocolVersion": ACP_PROTOCOL_VERSION,
                    "clientInfo": {
                        "name": DEFAULT_GEMINI_CLIENT_NAME,
                        "title": DEFAULT_GEMINI_CLIENT_NAME,
                        "version": "1",
                    },
                    "clientCapabilities": {
                        "fs": {},
                        "terminal": False,
                    },
                },
                allow_uninitialized=True,
            )
            if not isinstance(result, Mapping):
                raise GeminiAcpError("Gemini ACP initialize response is malformed")
            if _int_or_default(result.get("protocolVersion"), -1) != ACP_PROTOCOL_VERSION:
                raise GeminiAcpError("Gemini ACP protocol version is unsupported")
            with self._state_lock:
                self._initialized = True
            return dict(result)
        except Exception:
            self.stop()
            raise

    def stop(self, timeout: float = 5.0) -> None:
        process = self._process
        if process is None:
            return
        with self._state_lock:
            self._stopping = True
            self._initialized = False
            self._active_turns.clear()
        self._fail_pending(GeminiAcpError("Gemini ACPを停止しました"))
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=max(0.1, float(timeout)))
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
        reader_thread = self._reader_thread
        if reader_thread is not None and reader_thread is not threading.current_thread():
            reader_thread.join(timeout=1.0)
        if process.stdout is not None:
            process.stdout.close()
        self._process = None
        self._reader_thread = None

    def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
        allow_uninitialized: bool = False,
    ) -> Any:
        if not self.is_running:
            raise GeminiAcpError("Gemini ACPが起動していません")
        if not allow_uninitialized and not self.initialized:
            raise GeminiAcpError("Gemini ACPのinitializeが完了していません")
        request_id = self._reserve_request()
        future: Future[Any] = Future()
        with self._state_lock:
            self._pending[request_id] = future
        try:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": dict(params or {}),
                }
            )
            self._log(f"request {method}")
            return future.result(
                timeout=self.request_timeout if timeout is None else max(0.1, timeout)
            )
        except FutureTimeoutError as error:
            with self._state_lock:
                self._pending.pop(request_id, None)
            raise GeminiAcpRequestTimeout(f"{method}の応答がタイムアウトしました") from error
        finally:
            with self._state_lock:
                self._pending.pop(request_id, None)

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        if not self.is_running:
            raise GeminiAcpError("Gemini ACPが起動していません")
        if not self.initialized:
            raise GeminiAcpError("Gemini ACPのinitializeが完了していません")
        self._send(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": dict(params or {}),
            }
        )

    def authenticate(self, method_id: str) -> Mapping[str, Any] | None:
        if not str(method_id).strip():
            raise ValueError("Gemini ACP authentication method is required")
        result = self.request("authenticate", {"methodId": str(method_id).strip()})
        return dict(result) if isinstance(result, Mapping) else None

    def new_session(self, *, cwd: str | Path, model_id: str = "") -> Mapping[str, Any]:
        result = self.request(
            "session/new",
            {
                "cwd": str(Path(cwd).resolve()),
                "mcpServers": [],
            },
        )
        if not isinstance(result, Mapping):
            raise GeminiAcpError("Gemini ACP newSession response is malformed")
        session_id = _extract_session_id(result)
        if not session_id:
            raise GeminiAcpError("Gemini ACPがsession IDを返しませんでした")
        if model_id:
            self.request(
                "unstable_setSessionModel",
                {"sessionId": session_id, "modelId": str(model_id)},
            )
        return dict(result)

    def load_session(self, *, session_id: str, cwd: str | Path) -> Mapping[str, Any]:
        result = self.request(
            "session/load",
            {
                "sessionId": str(session_id),
                "cwd": str(Path(cwd).resolve()),
                "mcpServers": [],
            },
        )
        if not isinstance(result, Mapping):
            raise GeminiAcpError("Gemini ACP loadSession response is malformed")
        return dict(result) | {"sessionId": str(session_id)}

    def prompt(self, *, session_id: str, text: str, turn_id: str) -> Mapping[str, Any]:
        session_key = str(session_id)
        with self._state_lock:
            self._active_turns[session_key] = str(turn_id)
        try:
            result = self.request(
                "session/prompt",
                {
                    "sessionId": session_key,
                    "prompt": [{"type": "text", "text": str(text)}],
                },
            )
            if not isinstance(result, Mapping):
                raise GeminiAcpError("Gemini ACP prompt response is malformed")
            return dict(result)
        finally:
            with self._state_lock:
                if self._active_turns.get(session_key) == str(turn_id):
                    self._active_turns.pop(session_key, None)

    def cancel(self, *, session_id: str, turn_id: str) -> None:
        if not session_id or not turn_id:
            return
        self.notify("session/cancel", {"sessionId": str(session_id)})

    def _reserve_request(self) -> int:
        with self._state_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            return request_id

    def _send(self, message: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise GeminiAcpError("Gemini ACPへ書き込めません")
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with self._write_lock:
                process.stdin.write(payload)
                process.stdin.flush()
        except OSError as error:
            raise GeminiAcpError(f"Gemini ACPへの送信に失敗しました: {_safe_error(error)}") from error

    def _reader_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self._notify_protocol_error("JSONメッセージを解釈できませんでした")
                    continue
                if not isinstance(message, Mapping):
                    self._notify_protocol_error("JSON-RPCメッセージがオブジェクトではありません")
                    continue
                self._handle_message(message)
        finally:
            with self._state_lock:
                stopping = self._stopping
                self._initialized = False
            if not stopping:
                error = GeminiAcpError("Gemini ACPプロセスが予期せず終了しました")
                self._fail_pending(error)
                if self.disconnect_callback is not None:
                    try:
                        self.disconnect_callback(error)
                    except Exception as callback_error:
                        self._log(f"disconnect callback failed: {_safe_error(callback_error)}", error=True)

    def _handle_message(self, message: Mapping[str, Any]) -> None:
        message_id = message.get("id")
        if message_id is not None and ("result" in message or "error" in message):
            request_id = _request_id_as_int(message_id)
            if request_id is None:
                self._notify_protocol_error("応答のrequest IDが不正です")
                return
            with self._state_lock:
                future = self._pending.get(request_id)
            if future is None:
                self._log("response for unknown request", error=True)
                return
            if future.done():
                self._log("duplicate response for request", error=True)
                return
            if "error" in message:
                payload = message.get("error")
                if isinstance(payload, Mapping):
                    code = _int_or_default(payload.get("code"), -32000)
                    detail = payload.get("message", "Gemini ACP request failed")
                else:
                    code = -32000
                    detail = "Gemini ACP request failed"
                future.set_exception(GeminiAcpRpcError(code, detail))
            else:
                future.set_result(message.get("result"))
            return

        method = str(message.get("method", "")).strip()
        if not method:
            self._notify_protocol_error("メッセージにmethodがありません")
            return
        params = message.get("params")
        safe_params = dict(params) if isinstance(params, Mapping) else {}
        if message_id is not None:
            self._send_request_denial(message_id, method)
        session_id = str(safe_params.get("sessionId") or "")
        with self._state_lock:
            turn_id = self._active_turns.get(session_id, "")
        notification = GeminiAcpNotification(
            method=method,
            params=safe_params,
            session_id=session_id,
            turn_id=turn_id,
        )
        if self.notification_callback is not None:
            try:
                self.notification_callback(notification)
            except Exception as error:
                self._log(f"notification callback failed: {_safe_error(error)}", error=True)

    def _send_request_denial(self, message_id: object, method: str) -> None:
        is_permission = _is_permission_request(method)
        if is_permission:
            # ACP permission requests are agent-to-client requests.  A plain
            # chat provider must answer with the protocol's cancelled outcome,
            # never select an option or return an approval-shaped payload.
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {"outcome": {"outcome": "cancelled"}},
                }
            )
            return
        self._send(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {
                    "code": -32601,
                    "message": "server request is not supported by Subtitle Edit Bay",
                },
            }
        )

    def _notify_protocol_error(self, message: str) -> None:
        self._log(message, error=True)
        if self.notification_callback is not None:
            try:
                self.notification_callback(
                    GeminiAcpNotification(
                        method="protocol/error",
                        params={"message": _safe_error(message)},
                    )
                )
            except Exception as error:
                self._log(f"protocol error callback failed: {_safe_error(error)}", error=True)

    def _fail_pending(self, error: Exception) -> None:
        with self._state_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(error)

    def _log(self, message: object, *, error: bool = False) -> None:
        if self.log_callback is not None:
            self.log_callback(("ERROR: " if error else "") + _safe_error(message))


class GeminiAcpProvider:
    """Translate Gemini ACP lifecycle and stream events into AIProvider."""

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
            return self._set_state(
                availability="available",
                auth_state=auth_state,
                auth_label=auth_label,
                models=initial_models,
                model_selection_supported=bool(initial_models),
                selected_model=initial_selected if initial_models else "",
                login_available=bool(self._auth_methods),
                error="",
            )
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


def _validate_acp_command(command: Sequence[str]) -> None:
    banned = {"--experimental-acp", "--yolo", "-y", "--approval-mode=yolo"}
    if any(str(item).casefold() in banned for item in command):
        raise ValueError("Gemini ACPではexperimental ACPと自動承認モードを使用できません")
    if "--acp" not in command:
        raise ValueError("Gemini ACP command must include --acp")


def _safe_error(error: object) -> str:
    return redact_text(error, paths=True)


def _request_id_as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int_or_default(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_permission_request(method: str) -> bool:
    lowered = method.casefold()
    return "permission" in lowered or "tool_call" in lowered or "toolcall" in lowered


def _extract_session_id(result: Mapping[str, Any]) -> str:
    return str(result.get("sessionId") or result.get("session_id") or "").strip()


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
