"""Provider registry and provider-scoped chat state for the GUI.

The individual chat controllers own the provider protocol and their own
session/history.  This module only selects one provider for the visible chat
panel; it never copies messages, sessions, models, or credentials between
providers.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import Callable, Mapping, cast

from .gui_codex_chat_state import CodexChatController, CodexChatSnapshot


ProviderStateCallback = Callable[[CodexChatSnapshot], None]


class AIProviderChatRouter:
    """Expose a single active chat while retaining state per provider."""

    def __init__(
        self,
        controllers: Mapping[str, CodexChatController],
        *,
        preferred_provider: str = "codex",
        on_state: ProviderStateCallback | None = None,
        on_provider_changed: Callable[[str], None] | None = None,
        on_selected_provider: Callable[[str], None] | None = None,
    ) -> None:
        if not controllers:
            raise ValueError("at least one provider controller is required")
        self._controllers = dict(controllers)
        self._provider_order = tuple(self._controllers)
        self._active_provider_id = (
            str(preferred_provider).strip()
            if str(preferred_provider).strip() in self._controllers
            else self._provider_order[0]
        )
        self._on_state_callback = on_state
        self._on_provider_changed = on_provider_changed
        self._on_selected_provider = on_selected_provider
        self._lock = threading.RLock()
        self._router_error = ""
        self._shutdown = False

    @property
    def active_provider_id(self) -> str:
        with self._lock:
            return self._active_provider_id

    @property
    def active_provider_name(self) -> str:
        return self.snapshot.provider_name

    @property
    def active_controller(self) -> CodexChatController:
        with self._lock:
            return self._controllers[self._active_provider_id]

    @property
    def snapshot(self) -> CodexChatSnapshot:
        controller = self.active_controller
        snapshot = controller.snapshot
        with self._lock:
            error = self._router_error
        if error:
            return replace(snapshot, error=error)
        return snapshot

    @property
    def controllers(self) -> Mapping[str, CodexChatController]:
        return dict(self._controllers)

    def controller(self, provider_id: str) -> CodexChatController | None:
        return self._controllers.get(str(provider_id).strip())

    def connect(self) -> None:
        """Start all configured providers independently.

        A missing Gemini runtime must not prevent Codex from connecting (and
        vice versa), therefore each controller owns its own worker and error
        state.
        """

        for provider_id, controller in self._controllers.items():
            if self._shutdown:
                return
            controller.connect()
            # A controller callback normally supplies this update.  Calling
            # once here also makes a synchronous fake provider visible.
            self._provider_state_changed(provider_id, controller.snapshot)

    def reconnect(self) -> None:
        self.active_controller.reconnect()

    def login(self, *, relogin: bool = False) -> None:
        self.active_controller.login(relogin=relogin)

    def logout(self) -> None:
        self.active_controller.logout()

    def select_model(self, model_id: str) -> None:
        self.active_controller.select_model(model_id)

    def send_message(self, text: str) -> None:
        self.active_controller.send_message(text)

    def begin_proposal(
        self,
        text: str,
        *,
        content_type: str = "subtitle_proposal",
        pending_text: str = "",
    ) -> bool:
        return self.active_controller.begin_proposal(
            text,
            content_type=content_type,
            pending_text=pending_text,
        )

    def complete_proposal(self, summary: str) -> None:
        self.active_controller.complete_proposal(summary)

    def fail_proposal(self, message: str, *, cancelled: bool = False) -> None:
        self.active_controller.fail_proposal(message, cancelled=cancelled)

    def interrupt(self) -> None:
        self.active_controller.interrupt()

    def new_chat(self) -> None:
        self.active_controller.new_chat()

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
        for controller in self._controllers.values():
            controller.shutdown()

    def select_provider(self, provider_id: str) -> bool:
        """Switch visible provider without transferring conversation state."""

        selected = str(provider_id).strip()
        with self._lock:
            current = self._active_provider_id
            if selected not in self._controllers:
                self._router_error = f"利用できないAIプロバイダです: {selected or '（未選択）'}"
                self._notify_state()
                return False
            if selected == current:
                self._router_error = ""
                return True
            active = self._controllers[current]
            if _is_busy(active.snapshot):
                self._router_error = "応答中はAIプロバイダを切り替えできません"
                self._notify_state()
                return False
            target_snapshot = self._controllers[selected].snapshot
            if not _provider_visible(target_snapshot, selected == current):
                self._router_error = "選択したAIプロバイダは現在利用できません"
                self._notify_state()
                return False
            self._active_provider_id = selected
            self._router_error = ""
        if self._on_selected_provider is not None:
            self._on_selected_provider(selected)
        if self._on_provider_changed is not None:
            self._on_provider_changed(selected)
        self._notify_state()
        return True

    def available_providers(self) -> list[dict[str, object]]:
        """Return only providers whose current runtime is usable.

        A provider in ``error`` is intentionally omitted.  ``connecting`` and
        ``disconnected`` remain visible because they can recover through the
        provider's own reconnect/login action.
        """

        result: list[dict[str, object]] = []
        active = self.active_provider_id
        for provider_id in self._provider_order:
            snapshot = self._controllers[provider_id].snapshot
            if not _provider_visible(snapshot, provider_id == active):
                continue
            result.append(_provider_mapping(provider_id, snapshot, provider_id == active))
        return result

    def provider_states(self) -> list[dict[str, object]]:
        """Return all provider states for diagnostics/tests, including errors."""

        active = self.active_provider_id
        return [
            _provider_mapping(provider_id, self._controllers[provider_id].snapshot, provider_id == active)
            for provider_id in self._provider_order
        ]

    def _provider_state_changed(self, provider_id: str, snapshot: CodexChatSnapshot) -> None:
        # Do not mutate the controller snapshot.  It remains the source of
        # truth for direct callers and for the provider-specific history.
        if snapshot.provider_id != provider_id:
            snapshot = replace(snapshot, provider_id=provider_id)
        if self._on_state_callback is not None and provider_id == self.active_provider_id:
            self._notify_state(snapshot)
        elif self._on_state_callback is not None:
            # Availability/model/auth changes of the background provider also
            # affect the provider selector.
            self._notify_state(self.snapshot)

    def _notify_state(self, snapshot: CodexChatSnapshot | None = None) -> None:
        if self._on_state_callback is None:
            return
        self._on_state_callback(snapshot or self.snapshot)


def _is_busy(snapshot: CodexChatSnapshot) -> bool:
    return snapshot.chat_state in {"sending", "streaming", "stopping"}


def _provider_visible(snapshot: CodexChatSnapshot, is_active: bool) -> bool:
    # Keep the current provider visible long enough to display its error and
    # reconnect route, while unavailable non-active providers are hidden.
    return is_active or snapshot.connection_state != "error"


def _provider_mapping(
    provider_id: str,
    snapshot: CodexChatSnapshot,
    selected: bool,
) -> dict[str, object]:
    models = cast(tuple[Mapping[str, object], ...], snapshot.models)
    return {
        "id": provider_id,
        "label": snapshot.provider_name,
        "selected": selected,
        "available": snapshot.connection_state != "error",
        "connection_state": snapshot.connection_state,
        "auth_state": snapshot.auth_state,
        "auth_label": snapshot.auth_label,
        "login_url": snapshot.login_url,
        "login_available": snapshot.login_available,
        "models": [dict(model) for model in models],
        "model_selection_supported": snapshot.model_selection_supported,
        "selected_model": snapshot.selected_model,
        "error": snapshot.error,
    }


__all__ = ["AIProviderChatRouter"]
