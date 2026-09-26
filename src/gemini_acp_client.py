"""Gemini CLI の ACP プロセスとの JSON-RPC 通信を担当する。

公式の ``gemini --acp`` を起動し、認証情報や生の通信内容は保存・記録しない。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence, SupportsInt, cast

from .application_logging import redact_text


ACP_PROTOCOL_VERSION = 1
DEFAULT_GEMINI_CLIENT_NAME = "Subtitle Edit Bay"


class GeminiAcpError(RuntimeError):
    """Gemini ACP のプロセス・プロトコルの失敗を表す基底例外。"""


class GeminiAcpRequestTimeout(GeminiAcpError):
    """Gemini ACP の応答が期限までに届かなかったことを表す。"""


class GeminiAcpRpcError(GeminiAcpError):
    """Gemini CLI が返した JSON-RPC エラー。"""

    def __init__(self, code: int, message: object) -> None:
        self.code = int(code)
        self.message = _safe_error(message)
        super().__init__(self.message)


@dataclass(frozen=True)
class GeminiAcpNotification:
    """Gemini CLI から受け取った ACP 通知。"""

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
    """``gemini --acp`` と改行区切り JSON-RPC で通信する。"""

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
            return future.result(timeout=self.request_timeout if timeout is None else max(0.1, timeout))
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
            # 権限要求はエージェントからクライアントへ届く。通常のチャットでは
            # 選択肢を承認せず、プロトコルの cancelled 応答を返す。
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
        return int(cast(str | bytes | bytearray | SupportsInt, value))
    except (TypeError, ValueError):
        return None


def _int_or_default(value: object, default: int) -> int:
    try:
        return int(cast(str | bytes | bytearray | SupportsInt, value))
    except (TypeError, ValueError):
        return default


def _is_permission_request(method: str) -> bool:
    lowered = method.casefold()
    return "permission" in lowered or "tool_call" in lowered or "toolcall" in lowered


def _extract_session_id(result: Mapping[str, Any]) -> str:
    return str(result.get("sessionId") or result.get("session_id") or "").strip()


__all__ = [
    "ACP_PROTOCOL_VERSION",
    "DEFAULT_GEMINI_CLIENT_NAME",
    "GeminiAcpClient",
    "GeminiAcpClientProtocol",
    "GeminiAcpError",
    "GeminiAcpNotification",
    "GeminiAcpRequestTimeout",
    "GeminiAcpRpcError",
]
