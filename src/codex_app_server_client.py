from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from collections import deque
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .application_logging import redact_text


DEFAULT_CODEX_COMMAND = ("codex", "app-server", "--listen", "stdio://")
MAX_RETAINED_NOTIFICATIONS = 512
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)^(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret|authorization)$"
)


class CodexAppServerError(RuntimeError):
    """Base error for app-server process and protocol failures."""


class CodexRequestTimeout(CodexAppServerError):
    """Raised when an app-server response does not arrive in time."""


class CodexRpcError(CodexAppServerError):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = int(code)
        self.data = _redact_payload(data)
        super().__init__(_redact_log(message))


@dataclass(frozen=True)
class CodexNotification:
    method: str
    params: Mapping[str, Any]


def _redact_log(value: object) -> str:
    return redact_text(value)


def _redact_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]"
            if _SENSITIVE_KEY_PATTERN.fullmatch(str(key))
            else _redact_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_payload(item) for item in value)
    if isinstance(value, str):
        return _redact_log(value)
    return value


class CodexAppServerClient:
    """Small JSON-RPC client for a local ``codex app-server`` process.

    The client deliberately accepts no shell command string and never opens a
    listening socket. Approval requests from the server are rejected instead
    of being silently accepted.
    """

    def __init__(
        self,
        command: Sequence[str] = DEFAULT_CODEX_COMMAND,
        *,
        cwd: str | Path | None = None,
        environment: Mapping[str, str] | None = None,
        request_timeout: float = 30.0,
        notification_callback: Callable[[CodexNotification], None] | None = None,
        disconnect_callback: Callable[[Exception], None] | None = None,
        log_callback: Callable[[str], None] | None = None,
    ) -> None:
        if not command:
            raise ValueError("app-server command must not be empty")
        self.command = tuple(str(item) for item in command)
        self.cwd = str(cwd) if cwd else None
        self.environment = dict(environment or {})
        self.request_timeout = max(0.1, float(request_timeout))
        self.notification_callback = notification_callback
        self.disconnect_callback = disconnect_callback
        self.log_callback = log_callback
        self._process: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._pending: dict[int, Future[dict[str, Any]]] = {}
        self._next_request_id = 1
        self._initialized = False
        self._stopping = False
        self._notifications: deque[CodexNotification] = deque(
            maxlen=MAX_RETAINED_NOTIFICATIONS
        )

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def notifications(self) -> list[CodexNotification]:
        with self._state_lock:
            return list(self._notifications)

    def start(self) -> dict[str, Any]:
        if self.is_running:
            if not self._initialized:
                raise CodexAppServerError("app-server is starting")
            return {"already_running": True}

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
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
                shell=False,
            )
        except OSError as error:
            self._process = None
            raise CodexAppServerError(f"app-server could not start: {error}") from error

        self._stopping = False
        self._initialized = False
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="codex-app-server-reader",
            daemon=True,
        )
        self._reader_thread.start()
        try:
            result = self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "Subtitle Edit Bay",
                        "version": "1",
                    },
                },
            )
            self._initialized = True
            self.notify("initialized", {})
            return result
        except Exception:
            self.stop()
            raise

    def stop(self, timeout: float = 5.0) -> None:
        process = self._process
        if process is None:
            return
        reader_thread = self._reader_thread
        self._stopping = True
        self._initialized = False
        self._fail_pending(CodexAppServerError("app-server stopped"))
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
        if reader_thread is not None and reader_thread is not threading.current_thread():
            reader_thread.join(timeout=1.0)
        if process.stdout is not None:
            process.stdout.close()
        self._process = None
        self._reader_thread = None

    def restart(self) -> dict[str, Any]:
        self.stop()
        return self.start()

    def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if not self.is_running:
            raise CodexAppServerError("app-server is not running")
        if not self._initialized and method != "initialize":
            raise CodexAppServerError("initialize has not completed")
        request_id = self._reserve_request()
        future: Future[dict[str, Any]] = Future()
        with self._state_lock:
            self._pending[request_id] = future
        try:
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params or {})})
            self._log(f"request {method}")
            return future.result(timeout=self.request_timeout if timeout is None else max(0.1, timeout))
        except FutureTimeoutError as error:
            with self._state_lock:
                self._pending.pop(request_id, None)
            raise CodexRequestTimeout(f"timed out waiting for {method}") from error
        finally:
            with self._state_lock:
                self._pending.pop(request_id, None)

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        if not self.is_running:
            raise CodexAppServerError("app-server is not running")
        self._send({"jsonrpc": "2.0", "method": method, "params": dict(params or {})})

    def account_read(self, *, refresh_token: bool = False) -> dict[str, Any]:
        return self.request("account/read", {"refreshToken": bool(refresh_token)})

    def account_login_start(
        self,
        *,
        login_type: str = "chatgpt",
        use_hosted_login_success_page: bool = True,
        app_brand: str = "chatgpt",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"type": login_type}
        if login_type == "chatgpt":
            params.update(
                {
                    "useHostedLoginSuccessPage": bool(use_hosted_login_success_page),
                    "appBrand": app_brand,
                }
            )
        return self.request("account/login/start", params)

    def account_login_cancel(self, login_id: str) -> dict[str, Any]:
        return self.request("account/login/cancel", {"loginId": login_id})

    def account_logout(self) -> dict[str, Any]:
        return self.request("account/logout")

    def model_list(
        self,
        *,
        limit: int = 100,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        return self.request(
            "model/list",
            {"limit": max(1, int(limit)), "includeHidden": bool(include_hidden)},
        )

    def thread_start(self, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self.request("thread/start", params)

    def thread_resume(
        self,
        thread_id: str,
        *,
        model: str | None = None,
        cwd: str | Path | None = None,
        approval_policy: str | None = None,
        sandbox: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"threadId": thread_id}
        if model:
            params["model"] = model
        if cwd is not None:
            params["cwd"] = str(cwd)
        if approval_policy:
            params["approvalPolicy"] = approval_policy
        if sandbox:
            params["sandbox"] = sandbox
        return self.request("thread/resume", params)

    def turn_start(
        self,
        *,
        thread_id: str,
        prompt: str,
        output_schema: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
        model: str | None = None,
        cwd: str | Path | None = None,
        approval_policy: str | None = None,
        sandbox_policy: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
        }
        if output_schema is not None:
            params["outputSchema"] = dict(output_schema)
        if context:
            params["input"].append(
                {
                    "type": "text",
                    "text": "参照コンテキスト(JSON):\n"
                    + json.dumps(
                        dict(context),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            )
        if model:
            params["model"] = model
        if cwd is not None:
            params["cwd"] = str(cwd)
        if approval_policy:
            params["approvalPolicy"] = approval_policy
        if sandbox_policy is not None:
            params["sandboxPolicy"] = dict(sandbox_policy)
        return self.request(
            "turn/start",
            params,
        )

    def turn_interrupt(self, turn_id: str, *, thread_id: str) -> dict[str, Any]:
        return self.request(
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
        )

    def _reserve_request(self) -> int:
        with self._state_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            return request_id

    def _send(self, message: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise CodexAppServerError("app-server is not writable")
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with self._write_lock:
                process.stdin.write(payload)
                process.stdin.flush()
        except OSError as error:
            raise CodexAppServerError(f"app-server write failed: {error}") from error

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
                    self._log("invalid app-server JSON line", error=True)
                    continue
                if not isinstance(message, dict):
                    self._log("ignored non-object app-server message", error=True)
                    continue
                self._handle_message(message)
        finally:
            if not self._stopping:
                self._initialized = False
                error = CodexAppServerError("app-server exited unexpectedly")
                self._fail_pending(error)
                self.stop()
                if self.disconnect_callback is not None:
                    try:
                        self.disconnect_callback(error)
                    except Exception as callback_error:
                        self._log(
                            f"disconnect callback failed: {_redact_log(callback_error)}",
                            error=True,
                        )

    def _handle_message(self, message: Mapping[str, Any]) -> None:
        message_id = message.get("id")
        if message_id is not None and ("result" in message or "error" in message):
            try:
                request_id = int(message_id)
            except (TypeError, ValueError):
                self._log("ignored response with invalid request id", error=True)
                return
            with self._state_lock:
                future = self._pending.get(request_id)
            if future is None:
                self._log("ignored response for unknown request", error=True)
                return
            if "error" in message:
                error = message.get("error") or {}
                future.set_exception(
                    CodexRpcError(
                        int(error.get("code", -32000)),
                        _redact_log(error.get("message", "app-server request failed")),
                        _redact_payload(error.get("data")),
                    )
                )
            else:
                result = message.get("result")
                future.set_result(result if isinstance(result, dict) else {"value": result})
            return

        method = str(message.get("method", ""))
        if not method:
            self._log("ignored app-server message without method", error=True)
            return
        if message_id is not None:
            approval_request = self._is_approval_request(method)
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {
                        "code": -32001 if approval_request else -32601,
                        "message": (
                            "approval is required and is not auto-approved"
                            if approval_request
                            else "server request is not supported by this client"
                        ),
                    },
                }
            )
        notification = CodexNotification(
            method=method,
            params=(
                _redact_payload(message.get("params"))
                if isinstance(message.get("params"), dict)
                else {}
            ),
        )
        with self._state_lock:
            self._notifications.append(notification)
        self._log(f"notification {method}")
        if self.notification_callback is not None:
            try:
                self.notification_callback(notification)
            except Exception as error:
                self._log(f"notification callback failed: {_redact_log(error)}", error=True)

    @staticmethod
    def _is_approval_request(method: str) -> bool:
        lowered = method.casefold()
        return "approval" in lowered or "command" in lowered or "file_change" in lowered

    def _fail_pending(self, error: Exception) -> None:
        with self._state_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(error)

    def _log(self, message: object, *, error: bool = False) -> None:
        if self.log_callback is not None:
            self.log_callback(("ERROR: " if error else "") + _redact_log(message))

