from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .codex_runtime import redact_codex_diagnostic


class CodexChatClientProtocol(Protocol):
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


class CodexChatError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodexChatSnapshot:
    connection_state: str = "disconnected"
    auth_state: str = "unknown"
    auth_label: str = ""
    chat_state: str = "idle"
    login_url: str = ""
    login_id: str = ""
    models: tuple[Mapping[str, Any], ...] = ()
    selected_model: str = ""
    model_error: str = ""
    thread_id: str = ""
    turn_id: str = ""
    messages: tuple[Mapping[str, Any], ...] = ()
    error: str = ""


def _safe_error(error: object) -> str:
    return redact_codex_diagnostic(error)


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


def _normalize_models(result: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    normalized: list[dict[str, Any]] = []
    for item in result.get("data", []):
        if not isinstance(item, Mapping) or bool(item.get("hidden", False)):
            continue
        model_id = str(item.get("id") or item.get("model") or "").strip()
        if not model_id:
            continue
        normalized.append(
            {
                "id": model_id,
                "label": str(item.get("displayName") or model_id),
                "is_default": bool(item.get("isDefault", False)),
            }
        )
    return tuple(normalized)


class CodexChatController:
    """Own a persistent app-server session for authentication and plain chat.

    This boundary intentionally does not build subtitle context or apply Codex
    edits. Those responsibilities remain in the existing edit workflow.
    """

    def __init__(
        self,
        *,
        client_factory: Callable[[], CodexChatClientProtocol],
        workspace_root: str | Path,
        preferred_model: str = "",
        on_state: Callable[[CodexChatSnapshot], None] | None = None,
        on_selected_model: Callable[[str], None] | None = None,
        callback_dispatcher: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self.client_factory = client_factory
        self._workspace_root = str(Path(workspace_root).resolve())
        self.on_state = on_state
        self.on_selected_model = on_selected_model
        self._callback_dispatcher = callback_dispatcher or (lambda callback: callback())
        self._snapshot = CodexChatSnapshot(selected_model=str(preferred_model).strip())
        self._preferred_model = str(preferred_model).strip()
        self._client: CodexChatClientProtocol | None = None
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="codex-chat")
        self._interrupt_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="codex-chat-stop",
        )
        self._shutdown = False
        self._message_sequence = 0
        self._active_assistant_id = ""
        self._thread_needs_resume = False
        self._stop_requested = False

    @property
    def snapshot(self) -> CodexChatSnapshot:
        with self._lock:
            return self._snapshot

    def connect(self) -> None:
        if self.snapshot.connection_state in {"connecting", "ready"}:
            return
        self._update(connection_state="connecting", auth_state="checking", error="")
        self._submit(self._connect_worker, False)

    def reconnect(self) -> None:
        self._update(connection_state="connecting", auth_state="checking", error="")
        self._submit(self._connect_worker, True)

    def login(self, *, relogin: bool = False) -> None:
        self._update(auth_state="logging_in", login_url="", login_id="", error="")
        self._submit(self._login_worker, relogin)

    def logout(self) -> None:
        self._submit(self._logout_worker)

    def select_model(self, model: str) -> None:
        selected = str(model).strip()
        available = {str(item["id"]) for item in self.snapshot.models}
        if selected not in available:
            error = f"選択したCodexモデルは現在利用できません: {selected or '（未選択）'}"
            self._update(model_error=error, error=error)
            return
        self._preferred_model = selected
        self._update(selected_model=selected, model_error="", error="")
        if self.on_selected_model is not None:
            self._dispatch(lambda: self.on_selected_model(selected))

    def send_message(self, text: str) -> None:
        prompt = str(text).strip()
        if not prompt:
            self._update(error="メッセージを入力してください")
            return
        snapshot = self.snapshot
        if snapshot.auth_state != "authenticated":
            self._update(error="Codexへログインしてからメッセージを送信してください")
            return
        if snapshot.chat_state in {"sending", "streaming", "stopping"}:
            self._update(error="Codexの応答が完了してから次のメッセージを送信してください")
            return
        available = {str(item["id"]) for item in snapshot.models}
        if not snapshot.selected_model or snapshot.selected_model not in available:
            self._update(
                model_error="利用可能なCodexモデルを選択してください",
                error="利用可能なCodexモデルを選択してください",
            )
            return
        with self._lock:
            self._stop_requested = False
        self._message_sequence += 1
        user_id = f"local-user-{self._message_sequence}"
        self._message_sequence += 1
        assistant_id = f"local-assistant-{self._message_sequence}"
        self._active_assistant_id = assistant_id
        messages = list(snapshot.messages)
        messages.extend(
            [
                {"id": user_id, "role": "user", "text": prompt, "status": "completed"},
                {"id": assistant_id, "role": "assistant", "text": "", "status": "streaming"},
            ]
        )
        self._update(messages=tuple(messages), chat_state="sending", error="")
        self._submit(self._send_worker, prompt, snapshot.selected_model)

    def interrupt(self) -> None:
        snapshot = self.snapshot
        if snapshot.chat_state not in {"sending", "streaming"}:
            return
        with self._lock:
            self._stop_requested = True
        self._update(chat_state="stopping", error="")
        self._schedule_interrupt_if_ready(snapshot.thread_id, snapshot.turn_id)

    def new_chat(self) -> None:
        if self.snapshot.chat_state in {"sending", "streaming", "stopping"}:
            self._update(error="応答中は新しいチャットを開始できません")
            return
        self._active_assistant_id = ""
        with self._lock:
            self._thread_needs_resume = False
            self._stop_requested = False
        self._update(thread_id="", turn_id="", messages=(), chat_state="idle", error="")

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            self._stop_requested = False
            client = self._client
            self._client = None
        if client is not None:
            try:
                client.stop()
            except Exception:
                pass
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._interrupt_executor.shutdown(wait=False, cancel_futures=True)

    def _submit(self, callback: Callable[..., None], *args: Any) -> None:
        with self._lock:
            if self._shutdown:
                return
        self._executor.submit(callback, *args)

    def _connect_worker(self, force: bool) -> None:
        old_client: CodexChatClientProtocol | None = None
        try:
            with self._lock:
                if force:
                    old_client = self._client
                    self._client = None
                    self._thread_needs_resume = bool(self._snapshot.thread_id)
                elif self._client is not None:
                    return
            if old_client is not None:
                old_client.stop()
            client = self.client_factory()
            client.notification_callback = self._on_notification
            client.disconnect_callback = self._on_disconnect
            with self._lock:
                self._client = client
            client.start()
            self._refresh_account_and_models(client)
        except Exception as error:
            with self._lock:
                client = self._client
                self._client = None
            if client is not None:
                try:
                    client.stop()
                except Exception:
                    pass
            message = f"Codexへ接続できません: {_safe_error(error)}"
            self._update(
                connection_state="error",
                auth_state="error",
                chat_state="disconnected",
                error=message,
            )

    def _login_worker(self, relogin: bool) -> None:
        try:
            client = self._require_client()
            if relogin:
                client.account_logout()
                self._active_assistant_id = ""
                with self._lock:
                    self._thread_needs_resume = False
                    self._stop_requested = False
                self._update(
                    auth_label="",
                    chat_state="idle",
                    thread_id="",
                    turn_id="",
                    messages=(),
                )
            result = client.account_login_start(
                login_type="chatgpt",
                use_hosted_login_success_page=True,
                app_brand="chatgpt",
            )
            login_id = str(result.get("loginId") or "")
            login_url = str(result.get("authUrl") or result.get("url") or "")
            if not login_url:
                raise CodexChatError("ログイン用URLが返されませんでした")
            self._update(
                auth_state="login_pending",
                login_id=login_id,
                login_url=login_url,
                error="",
            )
        except Exception as error:
            self._update(
                auth_state="error",
                error=f"Codexのログインを開始できません: {_safe_error(error)}",
            )

    def _logout_worker(self) -> None:
        try:
            client = self._require_client()
            client.account_logout()
            self._active_assistant_id = ""
            with self._lock:
                self._thread_needs_resume = False
                self._stop_requested = False
            self._update(
                auth_state="unauthenticated",
                auth_label="",
                chat_state="idle",
                login_url="",
                login_id="",
                thread_id="",
                turn_id="",
                messages=(),
                error="",
            )
        except Exception as error:
            self._update(
                auth_state="error",
                error=f"Codexからログアウトできません: {_safe_error(error)}",
            )

    def _refresh_account_and_models(self, client: CodexChatClientProtocol) -> None:
        account = client.account_read(refresh_token=False)
        authenticated, auth_label = _account_status(account)
        if not authenticated:
            self._active_assistant_id = ""
            with self._lock:
                self._thread_needs_resume = False
                self._stop_requested = False
            self._update(
                connection_state="ready",
                auth_state="unauthenticated",
                auth_label="",
                chat_state="idle",
                models=(),
                login_url="",
                login_id="",
                thread_id="",
                turn_id="",
                messages=(),
                error="",
            )
            return
        model_result = client.model_list(limit=100, include_hidden=False)
        models = _normalize_models(model_result)
        if not models:
            raise CodexChatError("利用可能なCodexモデルが返されませんでした")
        available = {str(item["id"]) for item in models}
        selected = self._preferred_model
        model_error = ""
        if selected not in available:
            if selected:
                model_error = f"保存されていたCodexモデルは現在利用できません: {selected}"
            selected = next(
                (str(item["id"]) for item in models if bool(item.get("is_default"))),
                str(models[0]["id"]),
            )
        self._preferred_model = selected
        self._update(
            connection_state="ready",
            auth_state="authenticated",
            auth_label=auth_label,
            chat_state="idle" if self.snapshot.chat_state == "disconnected" else self.snapshot.chat_state,
            login_url="",
            login_id="",
            models=models,
            selected_model=selected,
            model_error=model_error,
            error=model_error,
        )
        if self.on_selected_model is not None:
            self._dispatch(lambda: self.on_selected_model(selected))

    def _send_worker(self, prompt: str, model: str) -> None:
        try:
            if self._consume_stop_request():
                self._complete_pending_stop(self.snapshot.thread_id)
                return
            client = self._require_client()
            snapshot = self.snapshot
            thread_id = snapshot.thread_id
            with self._lock:
                thread_needs_resume = self._thread_needs_resume
            thread_start_options = {
                "model": model,
                "cwd": self._workspace_root,
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "serviceName": "subtitle_edit_bay",
            }
            if thread_id and thread_needs_resume:
                thread_result = client.thread_resume(
                    thread_id,
                    model=model,
                    cwd=self._workspace_root,
                    approval_policy="never",
                    sandbox="read-only",
                )
                thread = thread_result.get("thread", thread_result)
                resumed_thread_id = str(
                    thread.get("id", "") if isinstance(thread, Mapping) else ""
                ) or str(thread_result.get("threadId", ""))
                if not resumed_thread_id:
                    raise CodexChatError("Codexチャットを再開できませんでした")
                thread_id = resumed_thread_id
                with self._lock:
                    self._thread_needs_resume = False
                self._update(thread_id=thread_id)
            if not thread_id:
                thread_result = client.thread_start(thread_start_options)
                thread = thread_result.get("thread", thread_result)
                thread_id = str(
                    thread.get("id", "") if isinstance(thread, Mapping) else ""
                ) or str(thread_result.get("threadId", ""))
                if not thread_id:
                    raise CodexChatError("Codexチャットを開始できませんでした")
                with self._lock:
                    self._thread_needs_resume = False
                self._update(thread_id=thread_id)
            if self._consume_stop_request():
                self._complete_pending_stop(thread_id)
                return
            response = client.turn_start(
                thread_id=thread_id,
                prompt=prompt,
                model=model,
                cwd=self._workspace_root,
                approval_policy="never",
                sandbox_policy={
                    "type": "readOnly",
                    "networkAccess": False,
                },
            )
            turn = response.get("turn", response)
            turn_id = str(turn.get("id", "") if isinstance(turn, Mapping) else "") or str(
                response.get("turnId", "")
            )
            current = self.snapshot
            effective_turn_id = turn_id or current.turn_id
            if not effective_turn_id and current.chat_state in {
                "sending",
                "streaming",
                "stopping",
            }:
                raise CodexChatError("Codex turn IDが返されませんでした")
            changes: dict[str, Any] = {"thread_id": thread_id}
            if effective_turn_id and current.chat_state in {"sending", "streaming", "stopping"}:
                changes["turn_id"] = effective_turn_id
            if current.chat_state == "sending":
                changes["chat_state"] = "streaming"
            self._update(**changes)
            if self.snapshot.chat_state == "stopping":
                self._schedule_interrupt_if_ready(thread_id, effective_turn_id)
        except Exception as error:
            with self._lock:
                self._stop_requested = False
            message = f"Codexへメッセージを送信できません: {_safe_error(error)}"
            self._finish_active_assistant("error")
            self._update(chat_state="send_failed", error=message)

    def _interrupt_worker(self, thread_id: str, turn_id: str) -> None:
        current = self.snapshot
        if (
            current.chat_state != "stopping"
            or current.thread_id != thread_id
            or (current.turn_id and current.turn_id != turn_id)
        ):
            return
        try:
            client = self._require_client()
            client.turn_interrupt(turn_id, thread_id=thread_id)
        except Exception as error:
            if self._shutdown or self.snapshot.chat_state != "stopping":
                return
            self._finish_active_assistant("error")
            self._update(
                chat_state="send_failed",
                error=f"Codexの応答を停止できません: {_safe_error(error)}",
            )

    def _schedule_interrupt_if_ready(self, thread_id: str, turn_id: str) -> None:
        if not thread_id or not turn_id:
            return
        with self._lock:
            if self._shutdown or not self._stop_requested:
                return
            self._stop_requested = False
        try:
            self._interrupt_executor.submit(self._interrupt_worker, thread_id, turn_id)
        except RuntimeError:
            pass

    def _consume_stop_request(self) -> bool:
        with self._lock:
            if not self._stop_requested:
                return False
            self._stop_requested = False
            return True

    def _complete_pending_stop(self, thread_id: str) -> None:
        self._finish_active_assistant("interrupted")
        self._update(
            chat_state="idle",
            thread_id=thread_id,
            turn_id="",
            error="",
        )

    def _require_client(self) -> CodexChatClientProtocol:
        with self._lock:
            client = self._client
        if client is None or self.snapshot.connection_state != "ready":
            raise CodexChatError("Codex App Serverへ接続されていません")
        return client

    def _on_notification(self, notification: Any) -> None:
        method = str(getattr(notification, "method", ""))
        params = getattr(notification, "params", {})
        if not isinstance(params, Mapping):
            params = {}
        if method == "account/login/completed":
            if bool(params.get("success", False)):
                self._submit(self._refresh_from_notification)
            else:
                error = _safe_error(params.get("error") or "認証が完了しませんでした")
                self._update(auth_state="error", login_url="", error=f"Codexの認証に失敗しました: {error}")
            return
        if method == "account/updated":
            if self.snapshot.auth_state not in {"logging_in", "login_pending"}:
                self._submit(self._refresh_from_notification)
            return
        if method == "turn/started":
            turn = params.get("turn", params)
            turn_id = str(turn.get("id", "") if isinstance(turn, Mapping) else "") or str(
                params.get("turnId", "")
            )
            current = self.snapshot
            chat_state = "stopping" if current.chat_state == "stopping" else "streaming"
            effective_turn_id = turn_id or current.turn_id
            self._update(turn_id=effective_turn_id, chat_state=chat_state)
            if chat_state == "stopping":
                self._schedule_interrupt_if_ready(current.thread_id, effective_turn_id)
            return
        if method == "item/agentMessage/delta":
            delta = str(params.get("delta") or params.get("text") or "")
            if delta:
                self._append_active_assistant(delta)
                chat_state = "stopping" if self.snapshot.chat_state == "stopping" else "streaming"
                self._update(chat_state=chat_state)
            return
        if method == "item/completed":
            item = params.get("item")
            if isinstance(item, Mapping) and item.get("type") == "agentMessage":
                self._replace_active_assistant(str(item.get("text") or ""), "completed")
            return
        if method == "error":
            payload = params.get("error", params)
            detail = payload.get("message") if isinstance(payload, Mapping) else payload
            with self._lock:
                self._stop_requested = False
            self._finish_active_assistant("error")
            self._update(chat_state="send_failed", error=f"Codexの応答でエラーが発生しました: {_safe_error(detail)}")
            return
        if method == "turn/completed":
            with self._lock:
                self._stop_requested = False
            turn = params.get("turn", params)
            status = str(turn.get("status", "completed") if isinstance(turn, Mapping) else "completed")
            if status == "failed":
                error_payload = turn.get("error", {}) if isinstance(turn, Mapping) else {}
                detail = error_payload.get("message") if isinstance(error_payload, Mapping) else error_payload
                self._finish_active_assistant("error")
                self._update(
                    chat_state="send_failed",
                    turn_id="",
                    error=f"Codexの応答に失敗しました: {_safe_error(detail or '原因を確認できません')}",
                )
            else:
                self._finish_active_assistant(
                    "interrupted" if status == "interrupted" else "completed"
                )
                self._update(chat_state="idle", turn_id="", error="")

    def _refresh_from_notification(self) -> None:
        try:
            self._refresh_account_and_models(self._require_client())
        except Exception as error:
            self._update(auth_state="error", error=f"Codexの認証状態を確認できません: {_safe_error(error)}")

    def _on_disconnect(self, _error: Exception) -> None:
        current = self.snapshot
        with self._lock:
            self._client = None
            self._thread_needs_resume = bool(self._snapshot.thread_id)
            self._stop_requested = False
        if current.chat_state in {"sending", "streaming", "stopping"}:
            self._finish_active_assistant("error")
        else:
            with self._lock:
                self._active_assistant_id = ""
        self._update(
            connection_state="disconnected",
            auth_state="unknown",
            chat_state="disconnected",
            turn_id="",
            error="Codexとの接続が切断されました。再接続してください",
        )

    def _append_active_assistant(self, delta: str) -> None:
        messages = [dict(item) for item in self.snapshot.messages]
        for item in reversed(messages):
            if str(item.get("id")) == self._active_assistant_id:
                item["text"] = str(item.get("text", "")) + delta
                item["status"] = "streaming"
                self._update(messages=tuple(messages))
                return

    def _replace_active_assistant(self, text: str, status: str) -> None:
        messages = [dict(item) for item in self.snapshot.messages]
        for item in reversed(messages):
            if str(item.get("id")) == self._active_assistant_id:
                item["text"] = text
                item["status"] = status
                self._update(messages=tuple(messages))
                return

    def _set_active_assistant_status(self, status: str) -> None:
        messages = [dict(item) for item in self.snapshot.messages]
        for item in reversed(messages):
            if str(item.get("id")) == self._active_assistant_id:
                item["status"] = status
                self._update(messages=tuple(messages))
                return

    def _finish_active_assistant(self, status: str) -> None:
        self._set_active_assistant_status(status)
        with self._lock:
            self._active_assistant_id = ""

    def _update(self, **changes: Any) -> None:
        with self._lock:
            self._snapshot = replace(self._snapshot, **changes)
            snapshot = self._snapshot
        if self.on_state is not None:
            self._dispatch(lambda: self.on_state(snapshot))

    def _dispatch(self, callback: Callable[[], None]) -> None:
        try:
            self._callback_dispatcher(callback)
        except RuntimeError:
            pass
