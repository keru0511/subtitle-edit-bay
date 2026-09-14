from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from .ai_provider import (
    AIProvider,
    AIProviderEvent,
    AIProviderPrompt,
    AIProviderSession,
    AIProviderState,
)
from .codex_ai_provider import CodexAIProvider
from .codex_runtime import redact_codex_diagnostic


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


class CodexChatController:
    """Own authentication and the shared conversation shown by the Codex UI."""

    def __init__(
        self,
        *,
        client_factory: Callable[[], Any] | None = None,
        provider_factory: Callable[[], AIProvider] | None = None,
        workspace_root: str | Path,
        preferred_model: str = "",
        on_state: Callable[[CodexChatSnapshot], None] | None = None,
        on_selected_model: Callable[[str], None] | None = None,
        callback_dispatcher: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        if provider_factory is None and client_factory is None:
            raise ValueError("client_factory or provider_factory is required")
        self.client_factory = client_factory
        self.provider_factory = provider_factory
        self._workspace_root = str(Path(workspace_root).resolve())
        self.on_state = on_state
        self.on_selected_model = on_selected_model
        self._callback_dispatcher = callback_dispatcher or (lambda callback: callback())
        self._snapshot = CodexChatSnapshot(selected_model=str(preferred_model).strip())
        self._preferred_model = str(preferred_model).strip()
        self._provider: AIProvider | None = None
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
        self._local_proposal_active = False
        self._local_proposal_content_type = "subtitle_proposal"

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
        provider = self._provider_or_none()
        if provider is not None:
            try:
                provider.select_model(selected)
            except Exception as error:
                message = str(error)
                self._update(model_error=message, error=message)
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

    def begin_proposal(
        self,
        text: str,
        *,
        content_type: str = "subtitle_proposal",
        pending_text: str = "",
    ) -> bool:
        """Append a proposal request without starting a second plain-chat turn."""

        prompt = str(text).strip()
        snapshot = self.snapshot
        if not prompt:
            self._update(error="メッセージを入力してください")
            return False
        if snapshot.auth_state != "authenticated":
            self._update(error="Codexへログインしてからメッセージを送信してください")
            return False
        if snapshot.chat_state in {"sending", "streaming", "stopping"}:
            self._update(error="Codexの応答が完了してから次のメッセージを送信してください")
            return False
        self._message_sequence += 1
        user_id = f"local-user-{self._message_sequence}"
        self._message_sequence += 1
        assistant_id = f"local-assistant-{self._message_sequence}"
        self._active_assistant_id = assistant_id
        self._local_proposal_active = True
        self._local_proposal_content_type = str(content_type).strip() or "subtitle_proposal"
        pending = str(pending_text).strip() or (
            "音量ミキサーの変更案を作成しています…"
            if self._local_proposal_content_type == "audio_mix_proposal"
            else "字幕の変更案を作成しています…"
        )
        messages = list(snapshot.messages)
        messages.extend(
            [
                {"id": user_id, "role": "user", "text": prompt, "status": "completed"},
                {
                    "id": assistant_id,
                    "role": "assistant",
                    "text": pending,
                    "status": "streaming",
                    "content_type": self._local_proposal_content_type,
                },
            ]
        )
        self._update(messages=tuple(messages), chat_state="sending", error="")
        return True

    def complete_proposal(self, summary: str) -> None:
        if not self._local_proposal_active:
            return
        fallback = (
            "音量ミキサーの変更案を作成しました。内容を確認してください。"
            if self._local_proposal_content_type == "audio_mix_proposal"
            else "字幕の変更案を作成しました。内容を確認してください。"
        )
        self._replace_active_assistant(
            text=str(summary).strip() or fallback,
            status="completed",
            content_type=self._local_proposal_content_type,
        )
        self._local_proposal_active = False
        self._active_assistant_id = ""
        self._update(chat_state="idle", error="")

    def fail_proposal(self, message: str, *, cancelled: bool = False) -> None:
        if not self._local_proposal_active:
            return
        fallback = (
            "音量ミキサーの変更案の作成を停止しました。"
            if self._local_proposal_content_type == "audio_mix_proposal"
            else "字幕の変更案の作成を停止しました。"
        )
        text = fallback if cancelled else str(message)
        self._replace_active_assistant(
            text=text,
            status="cancelled" if cancelled else "failed",
            content_type=self._local_proposal_content_type,
        )
        self._local_proposal_active = False
        self._local_proposal_content_type = "subtitle_proposal"
        self._active_assistant_id = ""
        self._update(chat_state="idle", error="" if cancelled else text)

    def interrupt(self) -> None:
        snapshot = self.snapshot
        if snapshot.chat_state not in {"sending", "streaming"}:
            return
        if self._local_proposal_active:
            self.fail_proposal("", cancelled=True)
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
        self._local_proposal_active = False
        self._local_proposal_content_type = "subtitle_proposal"
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
            provider = self._provider
            self._provider = None
        if provider is not None:
            try:
                provider.unsubscribe(self._on_provider_event)
                provider.close()
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
        old_provider: AIProvider | None = None
        try:
            with self._lock:
                if force:
                    old_provider = self._provider
                    self._provider = None
                    self._thread_needs_resume = bool(self._snapshot.thread_id)
                elif self._provider is not None:
                    return
            if old_provider is not None:
                old_provider.unsubscribe(self._on_provider_event)
                old_provider.close()
            provider = self._create_provider()
            provider.subscribe(self._on_provider_event)
            with self._lock:
                self._provider = provider
            state = provider.connect(force=False)
            if state.availability == "error":
                self._handle_provider_error(state, provider)
                return
            self._apply_provider_state(state)
        except Exception as error:
            with self._lock:
                provider = self._provider
                self._provider = None
            self._close_provider(provider)
            message = f"Codexへ接続できません: {self._safe_error(error)}"
            self._update(
                connection_state="error",
                auth_state="error",
                chat_state="disconnected",
                error=message,
            )

    def _login_worker(self, relogin: bool) -> None:
        try:
            if relogin:
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
            state = self._require_provider().login(relogin=relogin)
            self._apply_provider_state(state)
        except Exception as error:
            self._update(
                auth_state="error",
                error=f"Codexのログインを開始できません: {self._safe_error(error)}",
            )

    def _logout_worker(self) -> None:
        try:
            state = self._require_provider().logout()
            self._active_assistant_id = ""
            with self._lock:
                self._thread_needs_resume = False
                self._stop_requested = False
            self._apply_provider_state(state, reset_chat=True)
        except Exception as error:
            self._update(
                auth_state="error",
                error=f"Codexからログアウトできません: {self._safe_error(error)}",
            )

    def _refresh_provider_state(self) -> None:
        try:
            state = self._require_provider().refresh()
            self._apply_provider_state(state)
        except Exception as error:
            self._update(
                auth_state="error",
                error=f"Codexの認証状態を確認できません: {self._safe_error(error)}",
            )

    def _send_worker(self, prompt: str, model: str) -> None:
        try:
            if self._consume_stop_request():
                self._complete_pending_stop(self.snapshot.thread_id)
                return
            provider = self._require_provider()
            snapshot = self.snapshot
            thread_id = snapshot.thread_id
            with self._lock:
                thread_needs_resume = self._thread_needs_resume
            if thread_needs_resume:
                session = provider.new_session(
                    model_id=model,
                    workspace_root=self._workspace_root,
                    existing_session_id=thread_id,
                )
                thread_id = session.session_id
                with self._lock:
                    self._thread_needs_resume = False
                self._update(thread_id=thread_id)
            if not thread_id:
                session = provider.new_session(
                    model_id=model,
                    workspace_root=self._workspace_root,
                )
                thread_id = session.session_id
                with self._lock:
                    self._thread_needs_resume = False
                self._update(thread_id=thread_id)
            if self._consume_stop_request():
                self._complete_pending_stop(thread_id)
                return
            result = provider.send_prompt(
                AIProviderPrompt(
                    session=AIProviderSession(thread_id),
                    prompt=prompt,
                    model_id=model,
                    workspace_root=self._workspace_root,
                )
            )
            current = self.snapshot
            effective_turn_id = result.turn_id or current.turn_id
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
            message = f"Codexへメッセージを送信できません: {self._safe_error(error)}"
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
            self._require_provider().cancel_active_turn(
                session_id=thread_id,
                turn_id=turn_id,
            )
        except Exception as error:
            if self._shutdown or self.snapshot.chat_state != "stopping":
                return
            self._finish_active_assistant("error")
            self._update(
                chat_state="send_failed",
                error=f"Codexの応答を停止できません: {self._safe_error(error)}",
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

    def _create_provider(self) -> AIProvider:
        if self.provider_factory is not None:
            return self.provider_factory()
        assert self.client_factory is not None
        return CodexAIProvider(
            client_factory=self.client_factory,
            preferred_model=self._preferred_model,
        )

    def _provider_or_none(self) -> AIProvider | None:
        with self._lock:
            return self._provider

    def _close_provider(self, provider: AIProvider | None) -> None:
        if provider is None:
            return
        try:
            provider.unsubscribe(self._on_provider_event)
        except Exception:
            pass
        try:
            provider.close()
        except Exception:
            pass

    def _handle_provider_error(
        self,
        state: AIProviderState,
        provider: AIProvider | None = None,
    ) -> None:
        with self._lock:
            current = self._provider
            if provider is not None and current is not provider:
                return
            self._provider = None
        self._close_provider(current)
        self._update(
            connection_state="error",
            auth_state=state.auth_state,
            chat_state="disconnected",
            error=state.error,
        )

    def _require_provider(self) -> AIProvider:
        provider = self._provider_or_none()
        if provider is None or self.snapshot.connection_state != "ready":
            raise CodexChatError("Codex App Serverへ接続されていません")
        return provider

    def _on_provider_event(self, event: AIProviderEvent) -> None:
        if event.kind == "auth_changed" and event.refresh_state:
            self._submit(self._refresh_provider_state)
            return
        if event.kind == "state_changed":
            provider = self._provider_or_none()
            if provider is not None:
                state = provider.state
                if state.availability == "error":
                    self._handle_provider_error(state, provider)
                else:
                    self._apply_provider_state(state)
            return
        if event.kind == "disconnected":
            self._on_disconnect(event.error)
            return
        if event.kind == "turn_started":
            current = self.snapshot
            chat_state = "stopping" if current.chat_state == "stopping" else "streaming"
            effective_turn_id = event.turn_id or current.turn_id
            self._update(turn_id=effective_turn_id, chat_state=chat_state)
            if chat_state == "stopping":
                self._schedule_interrupt_if_ready(current.thread_id, effective_turn_id)
            return
        if event.kind == "text_delta":
            if event.text:
                self._append_active_assistant(event.text)
                chat_state = "stopping" if self.snapshot.chat_state == "stopping" else "streaming"
                self._update(chat_state=chat_state)
            return
        if event.kind == "message_completed":
            self._replace_active_assistant(event.text, "completed")
            return
        if event.kind == "error":
            with self._lock:
                self._stop_requested = False
            self._finish_active_assistant("error")
            self._update(chat_state="send_failed", error=event.error)
            return
        if event.kind == "turn_completed":
            with self._lock:
                self._stop_requested = False
            if event.status == "failed":
                self._finish_active_assistant("error")
                self._update(
                    chat_state="send_failed",
                    turn_id="",
                    error=f"Codexの応答に失敗しました: {event.error}",
                )
            else:
                self._finish_active_assistant(
                    "interrupted" if event.status == "interrupted" else "completed"
                )
                self._update(chat_state="idle", turn_id="", error="")

    def _on_disconnect(self, _error: object) -> None:
        current = self.snapshot
        with self._lock:
            self._thread_needs_resume = bool(self._snapshot.thread_id)
            self._stop_requested = False
            self._local_proposal_active = False
            self._local_proposal_content_type = "subtitle_proposal"
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

    def _apply_provider_state(
        self,
        state: AIProviderState,
        *,
        reset_chat: bool = False,
    ) -> None:
        if state.availability == "error":
            self._handle_provider_error(state)
            return
        connection_state = "ready" if state.availability == "available" else state.availability
        models = tuple(model.as_mapping() for model in state.models)
        model_error = state.error if state.auth_state == "authenticated" else ""
        changes: dict[str, Any] = {
            "connection_state": connection_state,
            "auth_state": state.auth_state,
            "auth_label": state.auth_label,
            "login_url": state.login_url,
            "login_id": state.login_id,
            "models": models,
            "selected_model": state.selected_model,
            "model_error": model_error,
            "error": state.error,
        }
        if state.auth_state == "authenticated" and self.snapshot.chat_state == "disconnected":
            changes["chat_state"] = "idle"
        if state.auth_state == "unauthenticated" or reset_chat:
            self._active_assistant_id = ""
            with self._lock:
                self._thread_needs_resume = False
                self._stop_requested = False
            changes.update(
                {
                    "chat_state": "idle",
                    "thread_id": "",
                    "turn_id": "",
                    "messages": (),
                }
            )
        self._preferred_model = state.selected_model or self._preferred_model
        self._update(**changes)
        if state.auth_state == "authenticated" and state.selected_model and self.on_selected_model is not None:
            self._dispatch(lambda: self.on_selected_model(state.selected_model))

    @staticmethod
    def _safe_error(error: object) -> str:
        return redact_codex_diagnostic(error)

    def _append_active_assistant(self, delta: str) -> None:
        messages = [dict(item) for item in self.snapshot.messages]
        for item in reversed(messages):
            if str(item.get("id")) == self._active_assistant_id:
                item["text"] = str(item.get("text", "")) + delta
                item["status"] = "streaming"
                self._update(messages=tuple(messages))
                return

    def _replace_active_assistant(
        self,
        text: str,
        status: str,
        *,
        content_type: str | None = None,
    ) -> None:
        messages = [dict(item) for item in self.snapshot.messages]
        for item in reversed(messages):
            if str(item.get("id")) == self._active_assistant_id:
                item["text"] = text
                item["status"] = status
                if content_type is not None:
                    item["content_type"] = content_type
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
